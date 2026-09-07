"""Build the standalone process-stack viewer page.

Pipeline
--------
    engine/input/layer/*.json  ─┐
    engine/input/presets/*.mk  ─┤
    data/solved/<tech>/*.res     ─┼─> payload JSON ─> template/{head,body,app.js} ─> smtcell-stack.html
    data/solved/<tech>/*.gds     ─┘        ▲
                                    layers.py (the z model)

By default nothing is re-solved: the three ``.res`` / ``.gds`` files under
``data/solved/`` are committed, so the page rebuilds on a checkout with only
``klayout`` installed.

    python tools/stack3d/build.py                 # rebuild the page from committed runs
    python tools/stack3d/build.py --gds           # re-run the GDS writers from the .res files
    python tools/stack3d/build.py --solve --gds   # re-solve all three cells, then rebuild

``--solve`` shells out to ``src.cellgen.run`` inside ``engine/`` and needs the
engine's own dependencies (ortools, klayout, networkx, loguru, matplotlib,
scikit-learn). ``--gds`` alone only needs klayout.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ENGINE = REPO / "engine"

sys.path.insert(0, str(HERE))
import layers as L  # noqa: E402  (path set above)
from dump_gds import dump  # noqa: E402

CELL = "INV_X1"


# --------------------------------------------------------------------------
# reading the real inputs
# --------------------------------------------------------------------------
def read_preset(name: str) -> dict:
    """Pull the scalar assignments out of a preset .mk (CPP / M1P / M1OF / ...)."""
    text = (ENGINE / "input" / "presets" / f"{name}.mk").read_text()
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Z_0-9]+)\s*[:?]?=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def lgg_order(layer_json: str) -> list[str]:
    """The LGG z order: metal layers sorted by layer_number.

    This mirrors ``LayerStack.__init__`` (engine/src/cellgen/core/entity.py),
    which sorts ``metal`` entries by ``layer_number`` and uses the resulting
    index as the graph's z-index. Deriving it here keeps the viewer honest if
    a layer JSON is edited.
    """
    data = json.loads((ENGINE / "input" / "layer" / layer_json).read_text())
    metals = [(v["layer_number"], v["layer_name"])
              for v in data.values() if v.get("layer_type") == "metal"]
    return [name for _, name in sorted(metals)]


def read_objective(res_path: Path) -> str:
    """First line of a .res is ``** Objective value: <float>``."""
    first = res_path.read_text().splitlines()[0]
    m = re.search(r"Objective value:\s*(\S+)", first)
    return f"obj = {m.group(1)}" if m else "obj = ?"


def cell_extent(cell_layers: dict) -> str:
    """Cell size straight off the BOUNDARY polygon the writers emit on 100/0."""
    b = cell_layers.get("100/0", {}).get("polygons")
    if not b:
        return "—"
    xs = [p[0] for p in b[0]]
    ys = [p[1] for p in b[0]]
    return f"{max(xs) - min(xs):g} × {max(ys) - min(ys):g} nm"


# --------------------------------------------------------------------------
# optional regeneration
# --------------------------------------------------------------------------
def run_engine(args_list: list[str]) -> None:
    print("  $ python -m " + " ".join(args_list))
    subprocess.run([sys.executable, "-m", *args_list], cwd=ENGINE, check=True)


def resolve(tech: str, cfg: dict, out_dir: Path) -> None:
    run_engine([
        "src.cellgen.run",
        "--preset", cfg["preset"],
        "--cell", CELL,
        "--output-dir", str(out_dir),
        "--override", "max_time.value=true",
        "--override", "max_time.time=300",
    ])
    src = out_dir / "result" / f"{CELL}.res"
    (HERE / "data" / "solved" / cfg["run"] / f"{CELL}.res").write_bytes(src.read_bytes())


def regen_gds(tech: str, cfg: dict) -> None:
    run_dir = HERE / "data" / "solved" / cfg["run"]
    res = run_dir / f"{CELL}.res"
    gds = run_dir / f"{CELL}.gds"
    gds.unlink(missing_ok=True)   # the writers APPEND to an existing library file
    layer = ENGINE / "input" / "layer" / cfg["layer_json"]
    mod = f"src.cellgen.postprocess.{cfg['gds_writer']}"
    if tech == "QFET":
        run_engine([mod, "--result", str(res), "--layer", str(layer),
                    "--subckt", CELL, "--gds", str(gds), "--draw-virtual"])
    else:
        run_engine([mod, "--result_file", str(res), "--subckt_name", CELL,
                    "--layer", str(layer), "--gds_file", str(gds)])


# --------------------------------------------------------------------------
# payload + page
# --------------------------------------------------------------------------
def build_payload() -> dict:
    techs, meta = {}, {}
    for tech in L.TECH_ORDER:
        cfg = L.TECHS[tech]
        run_dir = HERE / "data" / "solved" / cfg["run"]
        raw = dump(str(run_dir / f"{CELL}.gds"))
        cell = raw["cells"][CELL]

        stack = []
        for key, name, z0, z1, group, color, note, on in cfg["stack"]:
            entry = cell.get(key)
            stack.append({
                "key": key, "name": name, "z0": z0, "z1": z1,
                "group": group, "color": color, "note": note, "on": bool(on),
                "polys": entry["polygons"] if entry else [],
                "texts": entry["texts"] if entry else [],
            })

        preset = read_preset(cfg["preset"])
        techs[tech] = {"cell": CELL, "layers": stack}
        meta[tech] = {
            "obj": read_objective(run_dir / f"{CELL}.res"),
            "specs": [
                ["架構", cfg["arch"]],
                ["Placement layer", cfg["placement"]],
                ["Pin access", cfg["pin_access"]],
                ["Cell 尺寸", cell_extent(cell)],
                ["CPP / M1P / OF", "{} / {} / {}".format(
                    preset.get("CPP", "?"), preset.get("M1P", "?"), preset.get("M1OF", "?"))],
                ["LGG z 順序", " · ".join(lgg_order(cfg["layer_json"]))],
                ["虛擬邊", cfg["virtual"]],
            ],
        }
        drawn = sum(1 for s in stack if s["polys"])
        print(f"  {tech:7s} {drawn}/{len(stack)} layers with geometry, "
              f"{sum(len(s['polys']) for s in stack)} polygons")

    return {"techs": techs, "meta": meta, "alpha": L.ALPHA}


def assemble(payload: dict, out_path: Path) -> None:
    head = (HERE / "template" / "head.html").read_text(encoding="utf-8")
    body = (HERE / "template" / "body.html").read_text(encoding="utf-8")
    app = (HERE / "template" / "app.js").read_text(encoding="utf-8")

    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # A literal </script> inside the JSON block would close the tag early.
    assert "</script" not in data.lower(), "payload contains a closing script tag"

    page = (
        f"{head}\n{body}\n"
        f'<script id="stackdata" type="application/json">{data}</script>\n'
        f"<script>\n{app}</script>\n"
    )
    out_path.write_text(page, encoding="utf-8")
    print(f"  wrote {out_path.relative_to(REPO)} ({len(page.encode()):,} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solve", action="store_true",
                    help="re-run all three solves (needs the engine's dependencies)")
    ap.add_argument("--gds", action="store_true",
                    help="re-run the GDS writers from the committed .res files")
    ap.add_argument("--out", default=str(HERE / "smtcell-stack.html"))
    ap.add_argument("--payload", default=str(HERE / "data" / "stack3d.json"),
                    help="also write the payload JSON here")
    args = ap.parse_args()

    if args.solve:
        print("solving:")
        for tech in L.TECH_ORDER:
            resolve(tech, L.TECHS[tech], HERE / "data" / "_scratch" / L.TECHS[tech]["run"])
    if args.gds:
        print("regenerating GDS:")
        for tech in L.TECH_ORDER:
            regen_gds(tech, L.TECHS[tech])

    print("building payload:")
    payload = build_payload()
    Path(args.payload).write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    assemble(payload, Path(args.out))


if __name__ == "__main__":
    main()
