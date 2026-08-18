"""What the constraint-development UI needs to see inside a sandbox.

Two questions the sidebar asks continuously: *what plugins are in this
workspace right now*, and *does the engine still solve with them loaded*.
Both are answered by looking into the sandbox rather than by tracking state
here, because the agent edits those files directly and any cached view of them
would go stale the moment it does.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from dataclasses import asdict, dataclass, field

from loguru import logger

from cellgen_api.artifacts import MANIFEST, PLUGIN_DIR
from cellgen_api.backends.base import SandboxBackend, SandboxHandle

# The smoke cell: the smallest FinFET cell in the bundled netlist, and the one
# the engine's own test suite uses as its safety net.
SMOKE_PRESET = "FinFET_4T_SH"
SMOKE_CELL = "INV_X1"
SMOKE_MAX_TIME = 60
SMOKE_OUTPUT_DIR = "runs/smoke"

# '** Objective value: 1021.0' -- the first line of a .res file. This is the
# invariant worth reporting: it is reproducible across runs, whereas the
# placement geometry is not (see docs/solve-reproducibility.md).
_OBJECTIVE = re.compile(r"Objective value:\s*(-?[0-9.]+)")
# 'status: OPTIMAL' is printed at line start; anchored so it cannot match the
# unrelated 'LRAT_status:' line.
_STATUS = re.compile(r"^status:\s*(\S+)", re.MULTILINE)
_ELAPSED = re.compile(r"Elapsed time:\s*([0-9.]+)\s*seconds")

_SOLVED = {"OPTIMAL", "FEASIBLE"}


@dataclass
class PluginInfo:
    """One plugin file as the UI lists it."""

    path: str
    id: str = ""
    stage: str = ""
    description: str = ""
    tech: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    enabled: bool = True
    error: str = ""


def describe_plugin(path: str, source: str) -> PluginInfo:
    """Read a plugin's `@constraint(...)` metadata without executing it.

    Parsed rather than imported: this runs in the API process, and a plugin is
    arbitrary user code that only ever runs inside the sandbox. A file that
    does not parse is still listed, carrying its error, so a syntax error is
    visible in the UI instead of vanishing from the list.
    """
    info = PluginInfo(path=path)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        info.error = f"{exc.msg} (line {exc.lineno})"
        return info

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            name = deco.func.attr if isinstance(deco.func, ast.Attribute) else \
                getattr(deco.func, "id", "")
            if name != "constraint":
                continue
            for kw in deco.keywords:
                try:
                    value = ast.literal_eval(kw.value)
                except ValueError:
                    continue  # a computed default; not something to display
                if kw.arg == "id":
                    info.id = str(value)
                elif kw.arg == "stage":
                    info.stage = str(value)
                elif kw.arg == "description":
                    info.description = str(value)
                elif kw.arg == "tech":
                    info.tech = list(value)
                elif kw.arg == "params":
                    info.params = dict(value)
            info.id = info.id or node.name
            return info

    info.error = "no @constraint decorator found"
    return info


async def list_plugins(backend: SandboxBackend, handle: SandboxHandle) -> dict:
    """The sandbox's plugin directory, as the sidebar shows it.

    The manifest decides what actually runs, so its `enabled` flag and any
    parameter override are folded into each entry -- otherwise the list would
    show a plugin's defaults while the engine used something else.
    """
    code, listing, _ = await backend.exec(
        handle, ["ls", "-1", PLUGIN_DIR], timeout=30)
    if code != 0:
        return {"plugins": [], "manifest": None}

    manifest: dict | None = None
    try:
        manifest = json.loads(await backend.read_file(
            handle, f"{PLUGIN_DIR}/{MANIFEST}"))
    except Exception:
        pass  # No manifest is the normal case: every plugin found then runs.

    overrides = {
        entry.get("id"): entry
        for entry in (manifest or {}).get("plugins", [])
        if isinstance(entry, dict)
    }

    plugins: list[PluginInfo] = []
    for name in sorted(listing.split()):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        rel = f"{PLUGIN_DIR}/{name}"
        try:
            source = await backend.read_file(handle, rel)
        except Exception as exc:
            plugins.append(PluginInfo(path=rel, error=str(exc)))
            continue
        info = describe_plugin(rel, source)
        if (entry := overrides.get(info.id)) is not None:
            info.enabled = bool(entry.get("enabled", True))
            info.params = {**info.params, **entry.get("params", {})}
        plugins.append(info)

    return {"plugins": [asdict(p) for p in plugins], "manifest": manifest}


@dataclass
class SmokeResult:
    ok: bool
    status: str = ""
    objective: float | None = None
    elapsed: float | None = None
    cell: str = SMOKE_CELL
    preset: str = SMOKE_PRESET
    command: str = ""
    log_tail: str = ""


async def run_smoke(backend: SandboxBackend, handle: SandboxHandle,
                    cell: str = SMOKE_CELL, preset: str = SMOKE_PRESET,
                    max_time: int = SMOKE_MAX_TIME,
                    timeout: float = 600) -> SmokeResult:
    """Solve one small cell in the sandbox with its plugins loaded.

    This is the loop that makes natural-language authoring workable: write a
    constraint, see within seconds whether the engine still solves and at what
    objective. A time cap is always passed, because the engine's own default is
    no limit at all and a bad constraint can otherwise run for hours.
    """
    cmd = [
        "python", "-m", "src.cellgen.run",
        "--preset", preset, "--cell", cell,
        "--output-dir", SMOKE_OUTPUT_DIR,
        "--plugin-dir", PLUGIN_DIR,
        "--override", "max_time.value=true",
        "--override", f"max_time.time={max_time}",
    ]
    command = " ".join(shlex.quote(part) for part in cmd)
    logger.info(f"[{handle.id}] smoke: {command}")

    try:
        code, out, err = await backend.exec(handle, cmd, timeout=timeout)
    except TimeoutError:
        return SmokeResult(ok=False, status="TIMEOUT", cell=cell, preset=preset,
                           command=command,
                           log_tail=f"no result within {timeout:.0f}s")

    # The engine logs through loguru to stderr; stdout carries the raw
    # 'status:' line. Both matter, so parse them together.
    output = f"{out}\n{err}"
    result = SmokeResult(ok=False, cell=cell, preset=preset, command=command,
                         log_tail="\n".join(output.strip().splitlines()[-40:]))

    if (m := _STATUS.search(output)):
        result.status = m.group(1)
    if (m := _ELAPSED.search(output)):
        result.elapsed = float(m.group(1))

    # A non-zero exit means the batch reported a failure for this cell, even if
    # a status was parsed -- trust the exit code over the log.
    result.ok = code == 0 and result.status.upper() in _SOLVED
    if not result.status and code != 0:
        result.status = "ERROR"

    # Only read the objective for a solve that succeeded. A failed solve writes
    # no .res, so reporting whatever file is there would attach the *previous*
    # run's objective to this failure -- INFEASIBLE at 1021.0 reads as success.
    if result.ok:
        try:
            res = await backend.read_file(
                handle, f"{SMOKE_OUTPUT_DIR}/result/{cell}.res")
            if (m := _OBJECTIVE.search(res)):
                result.objective = float(m.group(1))
        except Exception:
            logger.warning(f"[{handle.id}] smoke solved but no .res for {cell}")
    return result
