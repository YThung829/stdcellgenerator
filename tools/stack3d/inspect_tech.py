"""Report everything you need to write a stack3d entry for an architecture.

Onboarding an architecture into the viewer is mostly fact-finding: which
layers exist, what order they sit in, and which GDS layer/datatype each drawn
thing lands on. Doing that by hand means reading a layer JSON, a tech.py and a
thousand-line GDS writer. This does it in one pass.

    python tools/stack3d/inspect_tech.py --name CFET
    python tools/stack3d/inspect_tech.py --name MYCFET --engine /path/to/engine
    python tools/stack3d/inspect_tech.py --name MYCFET --gds out/MYCELL.gds

Paths are found by convention from --name and overridable one by one. With
--gds it also diffs what the writer ACTUALLY emitted against layers.py, which
is the only reliable way to catch a layer you forgot, and prints a paste-ready
stack-table skeleton with the z columns left as TODO.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

DEFAULT_ENGINE = Path(os.environ.get("STACK3D_ENGINE", REPO / "engine"))

# Groups the viewer already styles. Guessed from a layer's name so the skeleton
# starts somewhere sensible; always re-check by eye.
NAME_HINTS = [
    (r"^B?WELL",                      "substrate"),
    (r"SELECT",                       "implant"),
    (r"^B?FIN",                       "device"),
    (r"ACTIVE|^B?SDT|^OD",            "device"),
    (r"GATE_CUT|CUT",                 "mask"),
    (r"^B?PC|GATE|POLY",              "device"),
    (r"LISD|LIG|^MOL",                "mol"),
    (r"^MIV",                         "miv"),
    (r"^V\d|^BV\d|^CA|^BCA|VIA",      "via"),
    (r"^H\d",                         "mid"),
    (r"^BM\d",                        "backbeol"),
    (r"^M\d",                         "beol"),
    (r"^VL",                          "virtual"),
]


def guess_group(name: str) -> str:
    for pat, group in NAME_HINTS:
        if re.search(pat, name, re.I):
            return group
    return "device"


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def find(engine: Path, name: str, args) -> dict:
    """Locate the four files by convention, honouring explicit overrides."""
    src = engine / "src" / "cellgen"
    out = {}

    out["tech_py"] = Path(args.tech_py) if args.tech_py else src / "archit" / name / "tech.py"

    if args.writer:
        out["writer"] = Path(args.writer)
    else:
        hits = sorted((src / "postprocess").glob(f"gds_{name}*.py"))
        out["writer"] = hits[0] if hits else None

    if args.preset:
        out["preset"] = Path(args.preset)
    else:
        hits = sorted((engine / "input" / "presets").glob(f"{name}*.mk"))
        out["preset"] = hits[0] if hits else None

    if args.layer:
        out["layer"] = Path(args.layer)
    elif out["preset"] and out["preset"].is_file():
        # A preset may name its own LAYER_FILE; otherwise fall back to a glob.
        m = re.search(r"^LAYER_FILE\s*[:?]?=\s*(\S+)", out["preset"].read_text(), re.M)
        out["layer"] = engine / m.group(1) if m else None
        if out["layer"] is None or not out["layer"].is_file():
            hits = sorted((engine / "input" / "layer").glob(f"*{name}*.json"))
            out["layer"] = hits[0] if hits else None
    else:
        hits = sorted((engine / "input" / "layer").glob(f"*{name}*.json"))
        out["layer"] = hits[0] if hits else None
    return out


def report_layer_json(path: Path) -> dict:
    data = json.loads(path.read_text())
    metals, vias, virtuals, gds_only = [], [], [], []
    for key, v in data.items():
        t = v.get("layer_type")
        if t == "metal":
            metals.append((v["layer_number"], v["layer_name"], v.get("direction"),
                           v.get("pitch"), v.get("offset"), v.get("width"),
                           v.get("gds_layer"), v.get("gds_datatype", 0),
                           bool(v.get("io_pin"))))
        elif t == "via":
            vias.append((v.get("layer_name", key), v["lower_layer"], v["upper_layer"],
                         v.get("gds_layer"), v.get("gds_datatype", 0)))
        elif t == "virtual":
            virtuals.append((v.get("layer_name", key), v["lower_layer"],
                             v["upper_layer"], v.get("method", "overlap"),
                             v.get("gds_layer"), v.get("gds_datatype", 0)))
        elif t == "gds":
            gds_only.append((v.get("layer_name", key), v["gds_layer"],
                             v.get("gds_datatype", 0)))

    metals.sort()
    section("LGG z order (metal layers sorted by layer_number)")
    print("  This ordering IS the graph z-index, and the MET column in a .res.")
    print(f"  {'z':>2}  {'name':<16} {'dir':<4} {'pitch':>7} {'offset':>7} "
          f"{'width':>6}  {'gds':>10}  io_pin")
    for z, (_, nm, d, p, o, w, gl, gd, io) in enumerate(metals):
        gds = f"{gl}/{gd}" if gl is not None else "-"
        print(f"  {z:>2}  {nm:<16} {str(d):<4} {p!s:>7} {o!s:>7} {w!s:>6}  "
              f"{gds:>10}  {'yes' if io else ''}")

    order = {nm: i for i, (_, nm, *_) in enumerate(metals)}
    section("Via chain (decides which layers can hold a via edge)")
    for nm, lo, hi, gl, gd in sorted(vias, key=lambda v: order.get(v[1], 99)):
        span = abs(order.get(hi, 0) - order.get(lo, 0))
        flag = "" if span == 1 else f"   <-- spans {span} z steps (not adjacent)"
        print(f"  {nm:<10} {lo:>8} -> {hi:<8}  gds {gl}/{gd}{flag}")

    if virtuals:
        section("Virtual edges (graph-only shortcuts, no physical geometry)")
        for nm, lo, hi, meth, gl, gd in virtuals:
            print(f"  {nm:<10} {lo:>8} <-> {hi:<8}  method={meth:<12} gds {gl}/{gd}")
    else:
        section("Virtual edges")
        print("  none declared in the layer JSON.")
        print("  (An architecture may still hardcode pairs in its _init_graph --")
        print("   grep for virtual_connect_pairs in archit/<NAME>/main.py.)")

    section("GDS-only layers (solver never sees these; writers draw them)")
    for nm, gl, gd in sorted(gds_only, key=lambda g: (g[1], g[2])):
        print(f"  {gl:>5}/{gd:<3} {nm}")
    by_gds = {}
    for _, nm, _, _, _, _, gl, gd, _ in metals:
        if gl is not None:
            by_gds.setdefault(f"{gl}/{gd}", nm)
    for nm, _, _, gl, gd in vias:
        if gl is not None:
            by_gds.setdefault(f"{gl}/{gd}", nm)
    for nm, _, _, _, gl, gd in virtuals:
        if gl is not None:
            by_gds.setdefault(f"{gl}/{gd}", nm)
    for nm, gl, gd in gds_only:
        by_gds.setdefault(f"{gl}/{gd}", nm)
    return {"metals": metals, "vias": vias, "virtuals": virtuals,
            "gds_only": gds_only, "by_gds": by_gds}


def _read_value(text: str, start: int) -> str:
    """Read one value starting at `start`, respecting nesting.

    A naive "up to the next comma" read truncates the interesting fields --
    `frozenset({"BPC1", "PC1"})` would come back as `frozenset({"BPC1"`. Stop
    at a comma or newline only once every bracket opened has been closed.
    """
    depth, out = 0, []
    for ch in text[start:start + 400]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and (ch == "," or ch == "\n"):
            break
        out.append(ch)
    return "".join(out).strip()


def report_tech_py(path: Path) -> None:
    section(f"tech.py defaults  ({path})")
    if not path.is_file():
        print("  not found -- pass --tech-py")
        return
    text = path.read_text()
    for field in ("TECHNOLOGY", "placement_layer_names", "pin_access_layer_names",
                  "default_placement_layer", "stacking_config", "power_config"):
        m = re.search(rf"\b{field}\b[^\n=]*=\s*", text)
        if not m:
            print(f"  {field:<26} (not found)")
            continue
        print(f"  {field:<26} {_read_value(text, m.end())}")


def report_writer(path: Path) -> set:
    section(f"GDS layer/datatype the writer references  ({path})")
    if path is None or not path.is_file():
        print("  not found -- pass --writer")
        return set()
    text = path.read_text()
    found = {}
    # Hardcoded style: self.xxx_layer_idx = self.layout.layer(15, 0)
    for m in re.finditer(r"(\w+)\s*=\s*self\.layout\.layer\(\s*(\d+)\s*,\s*(\d+)\s*\)", text):
        found.setdefault(f"{m.group(2)}/{m.group(3)}", set()).add(m.group(1))
    # JSON-driven style (QFET): named keys resolved from the layer stack.
    keys = re.search(r"GDS_KEYS\s*=\s*\(([^)]*)\)", text, re.S)
    if keys:
        names = re.findall(r'"([A-Z0-9_]+)"', keys.group(1))
        print("  writer is JSON-driven: GDS numbers come from the layer JSON's")
        print("  gds_layer / gds_datatype fields. Named GDS-only keys it requires:")
        for n in names:
            print(f"    {n}")
        print()
    if found:
        print("  hardcoded layer() calls:")
        for k in sorted(found, key=lambda s: [int(x) for x in s.split("/")]):
            print(f"    {k:>10}  {', '.join(sorted(found[k]))}")
        print("\n  NOTE: a ...text_layer_idx / ...debug_layer_idx entry usually")
        print("  carries labels or debug shapes, not real mask geometry.")
    return set(found)


def report_gds(gds_path: Path, tech_name: str, by_gds: dict) -> None:
    from dump_gds import dump
    section(f"What the writer ACTUALLY emitted  ({gds_path})")
    data = dump(str(gds_path))
    for cell_name, by_layer in data["cells"].items():
        print(f"  cell {cell_name}   (dbu={data['dbu']})")
        rows = []
        for k, v in by_layer.items():
            if not v["polygons"]:
                rows.append((k, 0, len(v["texts"]), ""))
                continue
            xs = [p[0] for poly in v["polygons"] for p in poly]
            ys = [p[1] for poly in v["polygons"] for p in poly]
            rows.append((k, len(v["polygons"]), len(v["texts"]),
                         f"x[{min(xs):g},{max(xs):g}] y[{min(ys):g},{max(ys):g}]"))
        rows.sort(key=lambda r: [float(x) for x in r[0].split("/")])
        print(f"    {'gds':>10} {'polys':>6} {'texts':>6}  bbox (nm)")
        for k, np_, nt, bb in rows:
            print(f"    {k:>10} {np_:>6} {nt:>6}  {bb}")

        # Diff against layers.py, if this tech is already registered.
        try:
            import layers as L
            cfg = L.TECHS.get(tech_name)
        except Exception:
            cfg = None
        drawn = {k for k, v in by_layer.items() if v["polygons"]}
        if cfg:
            claimed = {row[0] for row in cfg["stack"]}
            ignored = set(cfg.get("ignore_keys", ())) | {cfg.get("boundary_key", "100/0")}
            missing = sorted(drawn - claimed - ignored)
            empty = sorted(claimed - drawn)
            section(f"Coverage vs layers.TECHS['{tech_name}']")
            print(f"  drawn but NOT in the stack table : "
                  f"{', '.join(missing) if missing else '(none)'}")
            print(f"  in the table but no geometry     : "
                  f"{', '.join(empty) if empty else '(none)'}")
            print("  An empty entry is fine (an unused tier stays visible, greyed);")
            print("  a missing one silently disappears from the viewer.")
        else:
            print_skeleton(tech_name, by_layer, drawn, by_gds)


def print_skeleton(tech_name: str, by_layer: dict, drawn: set,
                   by_gds: dict) -> None:
    section(f"Paste-ready skeleton for layers.py  ({tech_name})")
    print("  z0/z1 are left as TODO on purpose -- they are the one thing that")
    print("  cannot be derived. Order the rows bottom-of-stack first and give")
    print("  each a z interval consistent with the LGG order printed above.\n")
    print(f"{tech_name.upper()} = [")
    for k in sorted(drawn, key=lambda s: [float(x) for x in s.split("/")]):
        if k == "100/0":
            continue
        # Prefer the layer JSON's own name; only fall back to the raw key for
        # something the writer emits that the JSON never declared.
        name = by_gds.get(k) or "TODO_" + k.replace("/", "_")
        n = len(by_layer[k]["polygons"])
        print(f'    ("{k}", "{name}", 0, 0, "{guess_group(name)}", '
              f'"#888888", "TODO describe", 1),   # {n} polys')
    print("]")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="architecture name as it appears in archit/<NAME>/")
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE), type=Path)
    ap.add_argument("--layer", help="override the layer JSON path")
    ap.add_argument("--tech-py", help="override archit/<NAME>/tech.py")
    ap.add_argument("--writer", help="override the GDS writer path")
    ap.add_argument("--preset", help="override the preset .mk")
    ap.add_argument("--gds", help="a generated .gds to diff against layers.py")
    args = ap.parse_args()

    engine = Path(args.engine).resolve()
    if not engine.is_dir():
        raise SystemExit(f"engine not found at {engine} (pass --engine)")
    paths = find(engine, args.name, args)

    print(f"architecture : {args.name}")
    print(f"engine       : {engine}")
    for k in ("layer", "tech_py", "writer", "preset"):
        p = paths.get(k)
        mark = "" if (p and Path(p).is_file()) else "   (MISSING)"
        print(f"{k:<13}: {p}{mark}")

    info = {}
    if paths["layer"] and Path(paths["layer"]).is_file():
        info = report_layer_json(Path(paths["layer"]))
    else:
        print("\nno layer JSON found -- pass --layer; it is the backbone of the stack.")
    report_tech_py(Path(paths["tech_py"]))
    report_writer(Path(paths["writer"]) if paths["writer"] else None)

    if args.gds:
        report_gds(Path(args.gds), args.name, info.get("by_gds", {}))
    else:
        section("Next")
        print("  Solve one cell and generate its GDS, then re-run with --gds to")
        print("  get the coverage diff and a paste-ready stack skeleton.")


if __name__ == "__main__":
    main()
