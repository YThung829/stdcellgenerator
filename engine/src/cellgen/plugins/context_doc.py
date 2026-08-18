"""Generate the constraint-authoring reference that ships into the sandbox.

Whoever writes a constraint -- a person or an LLM driving opencode -- needs to
know what is reachable on the ``inst`` object: which variable dictionaries
exist, what their keys look like, what the grid graph offers, and which CP-SAT
calls the logging wrapper supports.

All of that is already documented in the code, as trailing comments in
``_init_state_containers`` and as docstrings on the graph and solver wrappers.
Hand-copying it into a prompt file would go stale within a week, so this module
extracts it from the source instead. Regenerate with::

    python -m src.cellgen.plugins.context_doc --tech FinFET -o AGENTS.md
"""

from __future__ import annotations

import argparse
import ast
import inspect
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# `self.name = value   # comment`
_ASSIGN = re.compile(r"^\s*self\.(?P<name>\w+)\s*=\s*(?P<value>.+?)\s*(?:#\s*(?P<comment>.*))?$")
# A standalone `# ...` line, used as a section heading between groups.
_COMMENT = re.compile(r"^\s*#\s?(?P<text>.*)$")


@dataclass
class Container:
    name: str
    initial: str
    comment: str
    section: str


def _method_source(cls, name: str) -> list[str]:
    """Source lines of one method, or [] when the class doesn't define it."""
    fn = getattr(cls, name, None)
    if fn is None:
        return []
    return inspect.getsource(fn).splitlines()


def extract_containers(cls) -> list[Container]:
    """Pull the state containers and their trailing comments out of the source.

    Reads ``_init_state_containers``, which the orchestrators maintain precisely
    so "attribute provenance stays grep-able".
    """
    lines = _method_source(cls, "_init_state_containers")
    out: list[Container] = []
    section = ""
    pending: list[str] = []

    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith('"""') or stripped.startswith("def "):
            continue

        if stripped.startswith("#"):
            m = _COMMENT.match(raw)
            if m:
                pending.append(m.group("text").strip())
            continue

        m = _ASSIGN.match(raw)
        if not m:
            continue

        if pending:
            # A comment block immediately above assignments heads that group.
            section = " ".join(pending)
            pending = []

        out.append(Container(
            name=m.group("name"),
            initial=m.group("value").rstrip(","),
            comment=(m.group("comment") or "").strip(),
            section=section,
        ))
    return out


def extract_public_methods(cls, skip_dunder: bool = True) -> list[tuple[str, str, str]]:
    """``(name, signature, first docstring line)`` for each public method."""
    out = []
    for name, member in inspect.getmembers(cls):
        if skip_dunder and name.startswith("_"):
            continue
        if not (inspect.isfunction(member) or inspect.ismethod(member)):
            continue
        try:
            sig = re.sub(r"\(self,?\s*", "(", str(inspect.signature(member)))
        except (ValueError, TypeError):
            sig = "(...)"
        doc = (inspect.getdoc(member) or "").strip().split("\n")[0]
        out.append((name, sig, doc))
    return sorted(out)


def extract_wrapper_methods(module_path: Path, class_name: str) -> list[tuple[str, str]]:
    """``(signature, first docstring line)`` for methods defined on a class.

    Parsed from source rather than via ``inspect`` because the CPSAT wrapper
    decorates every method with a logging wrapper that does not preserve
    ``__name__``/``__doc__``/``__signature__``; introspection would report
    ``(*args, **kwargs)`` for all of them and inherit ~50 unrelated CpModel
    methods as well.
    """
    tree = ast.parse(module_path.read_text())
    out: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef) or item.name.startswith("_"):
                continue
            args = ast.unparse(item.args)
            # Drop the leading `self` so the table reads like a call site.
            args = re.sub(r"^self,?\s*", "", args)
            ret = f" -> {ast.unparse(item.returns)}" if item.returns else ""
            doc = (ast.get_docstring(item) or "").strip().split("\n")[0]
            out.append((f"{item.name}({args}){ret}", doc))
    return out


def extract_core_constraints() -> dict[str, list[tuple[str, str]]]:
    """Existing constraint functions per core module, as worked examples.

    Parsed with ``ast`` rather than imported: this runs in contexts where
    importing the whole solver stack is unnecessary and slow.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    core = REPO_ROOT / "src" / "cellgen" / "core"
    for module in ("placement", "routing", "rule", "pin"):
        path = core / f"{module}.py"
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text())
        fns = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            doc = (ast.get_docstring(node) or "").strip().split("\n")[0]
            fns.append((node.name, doc))
        out[module] = fns
    return out


def render(tech: str) -> str:
    """Build the full reference document for one technology."""
    from src.cellgen.core.graph import LayeredGridGraph
    from src.cellgen.plugins import registry
    from src.cellgen.solver.cpsat_wrapper import CPSAT

    if tech == "FinFET":
        from src.cellgen.archit.FinFET.main import FinFET as Orchestrator
    elif tech == "CFET":
        from src.cellgen.archit.CFET.main import CFET as Orchestrator
    elif tech == "QFET":
        from src.cellgen.archit.QFET.main import QFET as Orchestrator
    else:
        raise ValueError(f"Unknown technology {tech!r}")

    L: list[str] = []
    add = L.append

    add(f"# Writing a constraint plugin ({tech})")
    add("")
    add("Generated from the engine source by "
        "`python -m src.cellgen.plugins.context_doc`. Do not edit by hand -- "
        "regenerate it instead, so it cannot drift from the code.")
    add("")

    add("## The shape of a plugin")
    add("")
    add("A plugin is one file. It registers a function that receives the solve")
    add("orchestrator (`inst`) and a parameter dict, and emits CP-SAT constraints")
    add("through `inst.opt`.")
    add("")
    add("```python")
    add("from src.cellgen.plugins import constraint")
    add("")
    add('@constraint(')
    add('    id="max_vias_per_col",        # unique; this is how the UI refers to it')
    add(f'    stage="post_routing",         # one of: {", ".join(registry.STAGES)}')
    add(f'    tech={list(registry.TECHNOLOGIES)},')
    add('    params={"max_vias": 2},       # defaults; a manifest may override them')
    add('    description="Cap vias sharing a column.",')
    add(')')
    add("def max_vias_per_col(inst, params):")
    add('    inst.opt.Add(sum(...) <= params["max_vias"])')
    add("```")
    add("")
    add("Return value is ignored; a plugin mutates the model in place. It may also")
    add("create variables and hang them off `inst` for later constraints to use --")
    add("that is what the built-in `diffusion_alignment` does with `inst.db_vars`.")
    add("")

    add("## Stages")
    add("")
    add("| stage | fires |")
    add("|---|---|")
    add("| `pre_placement` | before any placement constraint |")
    add("| `post_placement` | after placement constraints and placement injection |")
    add("| `pre_routing` | before any routing constraint |")
    add("| `post_routing` | after every routing constraint |")
    add("| `pre_solve` | after injections and the top-layer cap, before the objective |")
    add("")
    add("On QFET, `pre_routing`/`post_routing` do not fire when the cell config")
    add("selects a placement-only stage -- there would be no routing constraints")
    add("to build on.")
    add("")

    add("## What is on `inst`")
    add("")
    add("| attribute | meaning |")
    add("|---|---|")
    add("| `inst.opt` | the CP-SAT model (logging wrapper, see below) |")
    add("| `inst.lgg` | layered grid graph; nodes are `(layer_idx, row, col)` |")
    add("| `inst.circuit` | parsed netlist: transistors, nets, IO pins |")
    add("| `inst.tech` | technology object: pitches, offsets, layer names |")
    add("| `inst.cell_config` | the per-cell config JSON, as a dict |")
    add("| `inst.plc_ci` / `inst.plc_zi` | placeable column / tier indices |")
    add("")

    containers = extract_containers(Orchestrator)
    add(f"### Variable containers ({len(containers)} on {tech})")
    add("")
    add("Every one of these is created up front and filled in by a later init")
    add("step, so they always exist by the time a plugin runs.")
    add("")
    current = object()  # sentinel: never equal to a real section string
    for c in containers:
        if c.section != current:
            current = c.section
            add("")  # a table must be followed by a blank line
            if current:
                # Section comments can run to several lines; keep the heading
                # to the first sentence and let the table carry the detail.
                heading = current.split(". ")[0].rstrip(".")
                add(f"**{heading}**")
                add("")
            add("| attribute | key -> value |")
            add("|---|---|")
        add(f"| `inst.{c.name}` | {c.comment or f'(initialised to `{c.initial}`)'} |")
    add("")

    add("### `inst.lgg` — the grid graph")
    add("")
    add("| method | purpose |")
    add("|---|---|")
    for name, sig, doc in extract_public_methods(LayeredGridGraph):
        add(f"| `lgg.{name}{sig}` | {doc} |")
    add("")

    add("### `inst.opt` — CP-SAT calls available")
    add("")
    add("A `cp_model.CpModel` subclass that mirrors every call into a readable")
    add("constraint log, which is how the UI shows what a plugin actually added.")
    add("")
    add("| method | purpose |")
    add("|---|---|")
    wrapper_path = REPO_ROOT / "src" / "cellgen" / "solver" / "cpsat_wrapper.py"
    for sig, doc in extract_wrapper_methods(wrapper_path, "CPSAT"):
        add(f"| `opt.{sig}` | {doc} |")
    add("")

    add("## Worked examples: the built-in constraints")
    add("")
    add("These are the existing rules, written against the same `inst` object.")
    add("Read one before writing a new plugin -- the idioms (reified booleans,")
    add("`OnlyEnforceIf` indicators, iterating `lgg.edges()`) carry straight over.")
    add("")
    for module, fns in extract_core_constraints().items():
        add(f"### `src/cellgen/core/{module}.py`")
        add("")
        for name, doc in fns:
            add(f"- `{name}` — {doc}" if doc else f"- `{name}`")
        add("")

    add("## Verifying a plugin")
    add("")
    add("```bash")
    add("# fast: INV_X1 on FinFET solves in well under a second")
    add("python -m src.cellgen.run --preset FinFET_4T_SH --cell INV_X1 \\")
    add("    --output-dir /tmp/check --plugin-dir plugins")
    add("")
    add("# the engine's own safety net")
    add("python -m pytest tests/ -q")
    add("```")
    add("")
    add("The run prints how many constraints and variables each plugin added.")
    add("Zero usually means the plugin matched nothing and is silently inert.")
    add("")
    add("Compare runs by **objective value**, never by layout geometry: repeated")
    add("identical solves agree on cost but can return different equal-cost")
    add("layouts. See `docs/solve-reproducibility.md`.")
    add("")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tech", default="FinFET", choices=["FinFET", "CFET", "QFET"])
    p.add_argument("-o", "--output", type=Path, default=None,
                   help="Write here instead of stdout.")
    args = p.parse_args(argv)

    text = render(args.tech)
    if args.output:
        args.output.write_text(text)
        print(f"wrote {args.output} ({len(text.splitlines())} lines)", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
