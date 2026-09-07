"""Build the standalone process-stack viewer page.

Pipeline
--------
    <engine>/input/layer/*.json  ─┐
    <engine>/input/presets/*.mk  ─┤
    data/solved/<tech>/*.res     ─┼─> payload ─> template/{head,body,app.js} ─> smtcell-stack.html
    data/solved/<tech>/*.gds     ─┘      ▲
                                    layers.py (the z model)

By default nothing is re-solved: the ``.res`` / ``.gds`` files under
``data/solved/`` are committed, so the page rebuilds on a checkout with only
``klayout`` installed.

    python tools/stack3d/build.py                     # rebuild from committed runs
    python tools/stack3d/build.py --gds               # re-run the GDS writers
    python tools/stack3d/build.py --solve --gds       # re-solve, then rebuild
    python tools/stack3d/build.py --tech MYCFET       # only rebuild one architecture

``--solve`` shells out to ``src.cellgen.run`` inside the engine and needs the
engine's own dependencies (ortools, klayout, networkx, loguru, matplotlib,
scikit-learn). Everything else only needs klayout.

Portability
-----------
The engine location and cell name are not baked in::

    --engine PATH   or  $STACK3D_ENGINE   (default: <repo>/engine)
    --cell NAME     or  $STACK3D_CELL     (default: INV_X1)

so this tool can be dropped into a fork whose engine lives elsewhere. Adding an
architecture is a ``layers.py`` edit only -- see the ``stack3d-onboard-tech``
skill, or tools/stack3d/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

sys.path.insert(0, str(HERE))
import layers as L  # noqa: E402  (path set above)
from dump_gds import dump  # noqa: E402

DEFAULT_ENGINE = Path(os.environ.get("STACK3D_ENGINE", REPO / "engine"))
DEFAULT_CELL = os.environ.get("STACK3D_CELL", "INV_X1")


@dataclass
class Ctx:
    """Everything a build needs that is not in layers.py."""
    engine: Path
    cell: str

    def layer_json(self, cfg: dict) -> Path:
        return self.engine / "input" / "layer" / cfg["layer_json"]

    def preset_mk(self, cfg: dict) -> Path:
        return self.engine / "input" / "presets" / f"{cfg['preset']}.mk"

    def run_dir(self, cfg: dict) -> Path:
        return HERE / "data" / "solved" / cfg["run"]


# --------------------------------------------------------------------------
# reading the real inputs
# --------------------------------------------------------------------------
def read_preset(path: Path) -> dict:
    """Pull the scalar assignments out of a preset .mk (CPP / M1P / M1OF / ...)."""
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Z_0-9]+)\s*[:?]?=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def lgg_order(path: Path) -> list:
    """The LGG z order: metal layers sorted by layer_number.

    Mirrors ``LayerStack.__init__`` (engine core/entity.py), which sorts
    ``metal`` entries by ``layer_number`` and uses the resulting index as the
    graph's z-index -- and as the ``MET`` column in a .res routing table.
    Deriving it here means the viewer stays honest if a layer JSON is edited.
    """
    data = json.loads(path.read_text())
    metals = [(v["layer_number"], v["layer_name"])
              for v in data.values() if v.get("layer_type") == "metal"]
    return [name for _, name in sorted(metals)]


def read_objective(res_path: Path) -> str:
    """First line of a .res is ``** Objective value: <float>``."""
    first = res_path.read_text().splitlines()[0]
    m = re.search(r"Objective value:\s*(\S+)", first)
    return f"obj = {m.group(1)}" if m else "obj = ?"


def cell_extent(cell_layers: dict, boundary_key: str) -> str:
    """Cell size straight off the BOUNDARY polygon the writers emit."""
    b = cell_layers.get(boundary_key, {}).get("polygons")
    if not b:
        return "—"
    xs = [p[0] for p in b[0]]
    ys = [p[1] for p in b[0]]
    return f"{max(xs) - min(xs):g} × {max(ys) - min(ys):g} nm"


# --------------------------------------------------------------------------
# optional regeneration
# --------------------------------------------------------------------------
def run_engine(ctx, argv: list) -> None:
    print("  $ python -m " + " ".join(argv))
    subprocess.run([sys.executable, "-m", *argv], cwd=ctx.engine, check=True)


def resolve(ctx, cfg: dict, scratch: Path) -> None:
    run_engine(ctx, [
        "src.cellgen.run",
        "--preset", cfg["preset"],
        "--cell", ctx.cell,
        "--output-dir", str(scratch),
        "--override", "max_time.value=true",
        "--override", "max_time.time=300",
    ])
    produced = scratch / "result" / f"{ctx.cell}.res"
    dest = ctx.run_dir(cfg) / f"{ctx.cell}.res"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(produced.read_bytes())


def regen_gds(ctx, cfg: dict) -> None:
    """Re-run this architecture's GDS writer.

    Flag names differ between writers (the QFET writer took the chance to
    rename them), so each architecture declares its own argv template in
    layers.py rather than this function growing a per-tech if-chain.
    """
    run_dir = ctx.run_dir(cfg)
    res = run_dir / f"{ctx.cell}.res"
    gds = run_dir / f"{ctx.cell}.gds"
    # The writers APPEND into an existing library file, so a stale file would
    # accumulate duplicate cells instead of being replaced.
    gds.unlink(missing_ok=True)
    fields = {
        "res": str(res),
        "gds": str(gds),
        "cell": ctx.cell,
        "layer": str(ctx.layer_json(cfg)),
    }
    argv = [f"src.cellgen.postprocess.{cfg['gds_writer']}"]
    argv += [a.format(**fields) for a in cfg["gds_argv"]]
    run_engine(ctx, argv)


# --------------------------------------------------------------------------
# payload + page
# --------------------------------------------------------------------------
def build_payload(ctx, techs_wanted: list) -> dict:
    techs, meta = {}, {}
    for tech in techs_wanted:
        cfg = L.TECHS[tech]
        run_dir = ctx.run_dir(cfg)
        gds_path = run_dir / f"{ctx.cell}.gds"
        if not gds_path.is_file():
            raise SystemExit(
                f"{tech}: no GDS at {gds_path}.\n"
                f"  -> run with --solve --gds to generate it, or --gds if the "
                f".res is already there."
            )
        cell = dump(str(gds_path))["cells"][ctx.cell]

        stack = []
        for key, name, z0, z1, group, color, note, on in cfg["stack"]:
            entry = cell.get(key)
            stack.append({
                "key": key, "name": name, "z0": z0, "z1": z1,
                "group": group, "color": color, "note": note, "on": bool(on),
                "polys": entry["polygons"] if entry else [],
                "texts": entry["texts"] if entry else [],
            })

        # A layer the writer drew but the stack table does not list would be
        # silently dropped from the viewer -- the easiest way to ship a
        # misleading picture. Surface it instead of losing it.
        claimed = {s["key"] for s in stack}
        ignored = set(cfg.get("ignore_keys", ())) | {cfg.get("boundary_key", "100/0")}
        # Numeric sort, not lexical: the result approximates stack order, so
        # the warning doubles as a checklist you can work top to bottom.
        orphans = sorted(
            (k for k, v in cell.items()
             if v["polygons"] and k not in claimed and k not in ignored),
            key=lambda k: [float(x) for x in k.split("/")],
        )
        if orphans:
            print(f"  !! {tech}: {len(orphans)} layer(s) have geometry but no "
                  f"stack entry -- they will NOT be drawn: {', '.join(orphans)}")
            print(f"     add them to layers.py, or list them in "
                  f"TECHS['{tech}']['ignore_keys'] if that is deliberate.")

        preset = read_preset(ctx.preset_mk(cfg))
        techs[tech] = {"cell": ctx.cell, "layers": stack}
        meta[tech] = {
            "obj": read_objective(run_dir / f"{ctx.cell}.res"),
            "specs": [
                ["架構", cfg["arch"]],
                ["Placement layer", cfg["placement"]],
                ["Pin access", cfg["pin_access"]],
                ["Cell 尺寸", cell_extent(cell, cfg.get("boundary_key", "100/0"))],
                ["CPP / M1P / OF", "{} / {} / {}".format(
                    preset.get("CPP", "?"), preset.get("M1P", "?"),
                    preset.get("M1OF", "?"))],
                ["LGG z 順序", " · ".join(lgg_order(ctx.layer_json(cfg)))],
                ["虛擬邊", cfg["virtual"]],
            ],
        }
        drawn = sum(1 for s in stack if s["polys"])
        print(f"  {tech:9s} {drawn}/{len(stack)} layers with geometry, "
              f"{sum(len(s['polys']) for s in stack)} polygons")

    alpha = {t: L.ALPHA.get(t, {}) for t in techs_wanted}
    return {"techs": techs, "meta": meta, "alpha": alpha}


def assemble(payload: dict, out_path: Path) -> None:
    head = (HERE / "template" / "head.html").read_text(encoding="utf-8")
    body = (HERE / "template" / "body.html").read_text(encoding="utf-8")
    app = (HERE / "template" / "app.js").read_text(encoding="utf-8")

    # The technology switcher is generated from the payload, so a newly
    # onboarded architecture appears without touching body.html.
    names = list(payload["techs"])
    default_idx = min(1, len(names) - 1)
    buttons = "\n    ".join(
        '<button type="button" data-tech="{t}" aria-pressed="{p}">{t}</button>'.format(
            t=t, p="true" if i == default_idx else "false")
        for i, t in enumerate(names)
    )
    body = re.sub(
        r'(<div class="seg" role="group" aria-label="選擇製程架構">).*?(</div>)',
        lambda m: f"{m.group(1)}\n    {buttons}\n  {m.group(2)}",
        body, count=1, flags=re.S,
    )

    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # A literal </script> inside the JSON block would close the tag early.
    assert "</script" not in data.lower(), "payload contains a closing script tag"

    page = (
        f"{head}\n{body}\n"
        f'<script id="stackdata" type="application/json">{data}</script>\n'
        f"<script>\n{app}</script>\n"
    )
    out_path.write_text(page, encoding="utf-8")
    rel = out_path.relative_to(REPO) if out_path.is_relative_to(REPO) else out_path
    print(f"  wrote {rel} ({len(page.encode()):,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solve", action="store_true",
                    help="re-run the solve (needs the engine's dependencies)")
    ap.add_argument("--gds", action="store_true",
                    help="re-run the GDS writers from the .res files")
    ap.add_argument("--tech", action="append", default=[], metavar="NAME",
                    help="only build these architectures (repeatable); "
                         "default is every entry in layers.TECH_ORDER")
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE), type=Path,
                    help=f"engine checkout root (default {DEFAULT_ENGINE})")
    ap.add_argument("--cell", default=DEFAULT_CELL,
                    help=f"cell to render (default {DEFAULT_CELL})")
    ap.add_argument("--out", default=str(HERE / "smtcell-stack.html"), type=Path)
    ap.add_argument("--payload", default=str(HERE / "data" / "stack3d.json"), type=Path,
                    help="also write the payload JSON here")
    args = ap.parse_args()

    unknown = [t for t in args.tech if t not in L.TECHS]
    if unknown:
        raise SystemExit(
            f"unknown --tech {unknown}. layers.TECHS has: {sorted(L.TECHS)}")
    wanted = args.tech or L.TECH_ORDER

    ctx = Ctx(engine=Path(args.engine).resolve(), cell=args.cell)
    if not ctx.engine.is_dir():
        raise SystemExit(f"engine not found at {ctx.engine} (pass --engine)")

    if args.solve:
        print("solving:")
        for tech in wanted:
            resolve(ctx, L.TECHS[tech],
                    HERE / "data" / "_scratch" / L.TECHS[tech]["run"])
    if args.gds:
        print("regenerating GDS:")
        for tech in wanted:
            regen_gds(ctx, L.TECHS[tech])

    print(f"building payload ({ctx.cell}, engine={ctx.engine}):")
    payload = build_payload(ctx, wanted)
    Path(args.payload).parent.mkdir(parents=True, exist_ok=True)
    Path(args.payload).write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8")
    assemble(payload, Path(args.out))


if __name__ == "__main__":
    main()
