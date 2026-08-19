"""An MCP server the sandbox's agent can call instead of guessing.

``AGENTS.md`` tells the agent what the constraint interface looks like, but a
document can only ever describe the engine as it was when the document was
generated, and it cannot answer "did that work?". These tools close both gaps:
the context is read out of the live registry, and a constraint can be checked
and solved without leaving the conversation.

It runs *inside* the sandbox, over stdio, registered through ``opencode.json``.
That placement is deliberate: plugins are arbitrary code, and the only place
arbitrary code may run is the sandbox that was built to contain it. Nothing
here is reachable from the API process.

    python -m src.cellgen.mcp.server

The working directory must be the engine root -- ``config.init()`` reads
``./input`` with hardcoded relative paths -- which is where opencode starts it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = REPO_ROOT / "plugins"

# The smoke cell: the smallest FinFET cell in the bundled netlist, and the one
# the engine's own test suite uses as its safety net.
DEFAULT_CELL = "INV_X1"
DEFAULT_PRESET = "FinFET_4T_SH"
DEFAULT_MAX_TIME = 60

mcp = MCPServer(
    name="cellgen",
    instructions=(
        "Tools for authoring CP-SAT constraint plugins for the SMTCell "
        "place-and-route engine. Call describe_context before writing a "
        "constraint -- it lists the variable containers and helper APIs that "
        "actually exist -- and run_smoke afterwards to check the model still "
        "solves. Compare solves by objective, never by layout: two runs of the "
        "same model can produce different geometry at identical cost."
    ),
)


@mcp.tool()
def describe_context(tech: str = "FinFET", section: str = "") -> str:
    """List what a constraint plugin can use: `inst`'s variables and APIs.

    Generated from the live engine source for the given technology, so it
    cannot drift from the code the way a hand-written note would. Pass a
    section name (for example "lgg", "routing") to filter; omit it for
    everything.

    Read this before writing a constraint. It is the difference between using
    `inst.edge_vars` and inventing a container that does not exist. Note the
    technology matters: CFET's orchestrator is a fork with its own constraints.
    """
    from src.cellgen.plugins.context_doc import render

    try:
        doc = render(tech)
    except ValueError as exc:
        return str(exc)

    if not section:
        return doc

    wanted = section.strip().lower()
    blocks = [b for b in doc.split("\n## ") if wanted in b.lower()]
    if not blocks:
        return (
            f"No section matching {section!r}. Call describe_context() with no "
            f"section to see the whole reference."
        )
    return "\n## ".join(blocks)


@mcp.tool()
def list_plugins() -> dict[str, Any]:
    """The constraint plugins in this workspace, and which of them are enabled.

    Reports the parameters the manifest actually applies, which may differ from
    the defaults written in the decorator.
    """
    from src.cellgen.plugins import loader

    manifest_path = PLUGIN_DIR / "manifest.json"
    manifest = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as exc:
            return {"error": f"manifest.json is not valid JSON: {exc}"}

    try:
        selections = loader.load(PLUGIN_DIR)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "manifest": manifest}

    return {
        "plugin_dir": str(PLUGIN_DIR),
        "manifest": manifest,
        "plugins": [
            {
                "id": s.plugin.id,
                "stage": s.plugin.stage,
                "tech": list(s.plugin.tech),
                "description": s.plugin.description,
                "params": s.params,
            }
            for s in selections
        ],
    }


@mcp.tool()
def list_builtin_constraints(module: str = "") -> dict[str, Any]:
    """The engine's own constraints, and whether this workspace switches any off.

    These are the rules the engine has always applied. Each can be disabled per
    run by adding its id to `builtins` in `plugins/manifest.json`, which is how
    an experiment measures what one was buying. Filter by module:
    `placement`, `routing`, `rule`, `pin`.
    """
    from src.cellgen.plugins import builtins as builtin_registry

    entries = builtin_registry.catalogue()
    if module:
        entries = [e for e in entries if e["module"] == module.strip().lower()]
    return {"count": len(entries), "builtins": entries}


@mcp.tool()
def validate_constraint(path: str) -> dict[str, Any]:
    """Load one plugin file and report whether it registers a usable constraint.

    This imports the file, which runs it -- safe here, because this server only
    ever runs inside the sandbox. Checks the file parses, registers exactly one
    constraint, and declares a stage and technologies the engine knows.

    Use this before run_smoke: it fails in milliseconds where a solve takes
    seconds, and it distinguishes "the file is wrong" from "the constraint is
    unsatisfiable".
    """
    from src.cellgen.plugins.loader import PluginLoadError, load_plugin_file

    target = Path(path)
    if not target.is_absolute():
        target = REPO_ROOT / target
    if not target.is_file():
        return {"ok": False, "error": f"no such file: {target}"}

    try:
        added = load_plugin_file(target)
    except PluginLoadError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if not added:
        return {
            "ok": False,
            "error": (
                "the file loaded but registered no constraint; it needs a "
                "@constraint(...) decorated function"
            ),
        }
    if len(added) > 1:
        return {
            "ok": False,
            "error": (
                f"registers {len(added)} constraints "
                f"({[p.id for p in added]}); keep one per file so an experiment "
                f"can toggle them individually"
            ),
        }

    plugin = added[0]
    return {
        "ok": True,
        "id": plugin.id,
        "stage": plugin.stage,
        "tech": list(plugin.tech),
        "params": dict(plugin.params),
        "description": plugin.description,
    }


@mcp.tool()
def run_smoke(cell: str = DEFAULT_CELL, preset: str = DEFAULT_PRESET,
              max_time: int = DEFAULT_MAX_TIME) -> dict[str, Any]:
    """Solve one small cell with this workspace's plugins loaded.

    Returns the solver status, the objective, and how many CP-SAT constraints
    and variables each plugin added to the model.

    Run in a subprocess: a solve leaves global state behind and an unsatisfiable
    model must not be able to take this server down with it. A time cap is
    always applied because the engine's own default is no limit.

    `status` is OPTIMAL or FEASIBLE when the constraints are satisfiable,
    INFEASIBLE when a plugin is too tight -- which is a normal answer, not a
    crash. Compare runs by `objective`; layout geometry is not reproducible.
    """
    out_dir = REPO_ROOT / "runs" / "mcp-smoke"
    cmd = [
        sys.executable, "-m", "src.cellgen.run",
        "--preset", preset, "--cell", cell,
        "--output-dir", str(out_dir),
        "--plugin-dir", str(PLUGIN_DIR),
        "--override", "max_time.value=true",
        "--override", f"max_time.time={max_time}",
    ]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True,
        # Generous: the cap above bounds the search, not the process around it.
        timeout=max(max_time * 4, 300),
    )

    result: dict[str, Any] = {
        "ok": proc.returncode == 0,
        "cell": cell,
        "preset": preset,
        "command": " ".join(cmd[1:]),
    }

    run_json = out_dir / "run.json"
    if run_json.is_file():
        record = json.loads(run_json.read_text())
        result["status"] = record.get("results", {}).get(cell, "")
        result["plugin_deltas"] = record.get("plugin_deltas", {}).get(cell, [])

    # The objective is only meaningful when the solve actually succeeded: a
    # failed run writes no .res, so an older one would otherwise be reported.
    res_file = out_dir / "result" / f"{cell}.res"
    if result["ok"] and res_file.is_file():
        first = res_file.read_text().splitlines()[0]
        try:
            result["objective"] = float(first.split(":")[1])
        except (IndexError, ValueError):
            pass

    if not result["ok"]:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-25:]
        result["log_tail"] = "\n".join(tail)
    return result


@mcp.tool()
def export_constraint(path: str, name: str, description: str = "") -> dict[str, Any]:
    """Save this workspace's work as an artifact an experiment can run.

    The sandbox has no push path and must not touch the upstream engine, so
    exporting is how work leaves. Requires `CELLGEN_API_BASE` to point at a
    reachable Studio API; without it, use the "匯出" button in the web UI,
    which does the same thing from the other side.

    `path` is recorded for context -- the export always captures every plugin
    in the workspace plus a patch of any other edit, because an experiment
    needs the whole picture, not one file.
    """
    api_base = os.environ.get("CELLGEN_API_BASE", "").rstrip("/")
    sandbox_id = os.environ.get("CELLGEN_SANDBOX_ID", "")
    if not api_base or not sandbox_id:
        return {
            "ok": False,
            "error": (
                "exporting from inside the sandbox needs CELLGEN_API_BASE and "
                "CELLGEN_SANDBOX_ID in this environment. Use the web UI's "
                "export button instead -- it captures exactly the same thing."
            ),
        }

    import urllib.error
    import urllib.request

    payload = json.dumps({
        "name": name,
        "description": description or f"exported from {path}",
    }).encode()
    req = urllib.request.Request(
        f"{api_base}/api/sandboxes/{sandbox_id}/export",
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return {"ok": True, **json.loads(resp.read())}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"{exc.code}: {exc.read().decode()[:400]}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    # The engine reads ./input with relative paths, so the process must start
    # from the engine root no matter where opencode was invoked.
    os.chdir(REPO_ROOT)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
