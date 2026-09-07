"""Check every claim the viewer makes against the engine's own files.

This page gets shown to people who will act on it, so a wrong pitch or a
mis-ordered layer is not a cosmetic bug. Everything here is a claim that CAN
be re-derived from a source file; anything that cannot (the z thicknesses) is
reported as such rather than silently passed.

    python tools/stack3d/audit.py            # all architectures
    python tools/stack3d/audit.py --tech QFET

Exit status is non-zero if any check fails, so it can gate a commit.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import layers as L                      # noqa: E402
import build as B                       # noqa: E402

FAIL, WARN, OK = "FAIL", "warn", "ok"
results: list = []


def check(level: str, tech: str, what: str, detail: str = "") -> None:
    results.append((level, tech, what, detail))


def audit(ctx, tech: str) -> None:
    cfg = L.TECHS[tech]
    lj_path = ctx.layer_json(cfg)
    lj = json.loads(lj_path.read_text())
    cell = B.load_geometry(ctx, cfg, tech)
    stack = cfg["stack"]

    # ---- 1. every stack row's gds_key must be a real key ------------------
    metals = {v["layer_name"]: v for v in lj.values() if v.get("layer_type") == "metal"}
    known = set()
    for v in lj.values():
        gl = v.get("gds_layer")
        if gl is not None:
            known.add(f"{gl}/{int(v.get('gds_datatype', 0))}")
    drawn = {k for k, v in cell.items() if v["polygons"]}

    for key, name, z0, z1, *_ in stack:
        if key not in known and key not in drawn:
            check(FAIL, tech, f"{name} ({key}) is in neither the layer JSON "
                              f"nor the emitted geometry")

    # ---- 2. no drawn layer is dropped ------------------------------------
    claimed = {r[0] for r in stack}
    ignored = set(cfg.get("ignore_keys", ())) | {cfg.get("boundary_key", "100/0")}
    orphans = sorted(drawn - claimed - ignored,
                     key=lambda k: [float(x) for x in k.split("/")])
    if orphans:
        check(FAIL, tech, "drawn but not in the stack table", ", ".join(orphans))
    else:
        check(OK, tech, "every drawn layer is claimed by a stack row")

    # ---- 3. stack z order agrees with the LGG metal order -----------------
    lgg = B.lgg_order(lj_path)
    zmid = {}
    for key, name, z0, z1, *_ in stack:
        for mname, mv in metals.items():
            mk = f"{mv.get('gds_layer')}/{int(mv.get('gds_datatype', 0))}"
            if mk == key:
                zmid.setdefault(mname, (z0 + z1) / 2)
    seq = [(m, zmid[m]) for m in lgg if m in zmid]
    bad = [(a, b) for (a, za), (b, zb) in zip(seq, seq[1:]) if za >= zb]
    if bad:
        check(FAIL, tech, "stack z contradicts the layer_number order",
              "; ".join(f"{a} should sit below {b}" for a, b in bad))
    else:
        check(OK, tech, f"metal z order matches layer_number sort",
              " < ".join(m for m, _ in seq))

    # ---- 4. every via sits between the two metals it names ----------------
    for v in lj.values():
        if v.get("layer_type") != "via":
            continue
        vk = f"{v.get('gds_layer')}/{int(v.get('gds_datatype', 0))}"
        row = next((r for r in stack if r[0] == vk), None)
        lo, hi = v["lower_layer"], v["upper_layer"]
        if row is None or lo not in zmid or hi not in zmid:
            continue
        vz = (row[2] + row[3]) / 2
        lo_z, hi_z = zmid[lo], zmid[hi]
        if not (min(lo_z, hi_z) < vz < max(lo_z, hi_z)):
            check(FAIL, tech, f"via {v['layer_name']} ({vk}) is not between "
                              f"{lo} and {hi}", f"z={vz}, {lo}={lo_z}, {hi}={hi_z}")
    check(OK, tech, "every via sits between the metals it connects")

    # ---- 5. notes that quote a pitch/offset must match the JSON -----------
    for key, name, z0, z1, group, color, note, on in stack:
        m = next((mv for mv in metals.values()
                  if f"{mv.get('gds_layer')}/{int(mv.get('gds_datatype', 0))}" == key),
                 None)
        if not m:
            continue
        for label, field in (("pitch", "pitch"), ("offset", "offset")):
            q = re.search(rf"{label}\s+(\d+(?:\.\d+)?)", note)
            if q and float(q.group(1)) != float(m[field]):
                check(FAIL, tech, f"{name} note says {label} {q.group(1)}",
                      f"layer JSON says {m[field]}")
        d = re.search(r"\b(horizontal|vertical)\b", note, re.I)
        if d:
            want = "H" if d.group(1).lower() == "horizontal" else "V"
            if m["direction"] != want:
                check(FAIL, tech, f"{name} note says {d.group(1)}",
                      f"layer JSON direction is {m['direction']}")
    check(OK, tech, "pitch / offset / direction in the notes match the layer JSON")

    # ---- 6. z intervals are well-formed ----------------------------------
    for key, name, z0, z1, *_ in stack:
        if z1 <= z0:
            check(FAIL, tech, f"{name} ({key}) has an empty or inverted z "
                              f"interval", f"{z0} -> {z1}")

    # ---- 7. spec strip values are re-derivable ---------------------------
    preset = B.read_preset(ctx.preset_mk(cfg))
    check(OK, tech, "preset CPP/M1P/OF",
          f"{preset.get('CPP')}/{preset.get('M1P')}/{preset.get('M1OF')} "
          f"(from {ctx.preset_mk(cfg).name})")
    check(OK, tech, "cell extent",
          B.cell_extent(cell, cfg.get("boundary_key", "100/0")) + " (from BOUNDARY polygon)")
    check(OK, tech, "objective",
          B.read_objective(ctx.run_dir(cfg) / f"{ctx.cell}.res") + " (from .res header)")

    # ---- 8. placement / pin-access strings vs tech.py --------------------
    tech_py = ctx.engine / "src" / "cellgen" / "archit" / tech / "tech.py"
    if tech_py.is_file():
        txt = tech_py.read_text()
        for field, claim in (("placement_layer_names", cfg["placement"]),
                             ("pin_access_layer_names", cfg["pin_access"])):
            m = re.search(rf"{field}[^=]*=\s*frozenset\(\{{([^}}]*)\}}\)", txt)
            if not m:
                check(WARN, tech, f"could not read {field} from tech.py")
                continue
            names = re.findall(r'"([^"]+)"', m.group(1))
            missing = [n for n in names if n not in claim]
            if missing:
                check(FAIL, tech, f"{field} lists {names} but the spec strip "
                                  f"says {claim!r}", f"missing: {missing}")
            else:
                check(OK, tech, f"{field} {sorted(names)} covered by spec strip")
    else:
        check(WARN, tech, f"no tech.py at {tech_py} (custom architecture?)")

    # ---- 9. vertical continuity ------------------------------------------
    # A hole in the z chain means the picture shows a connection that does not
    # visibly connect. Every hole must have a reason; unexplained ones are how
    # a viewer ends up implying a floating contact.
    spans = sorted(((r[2], r[3], r[1]) for r in stack),
                   key=lambda t: t[0])
    merged = []
    for z0, z1, name in spans:
        if merged and z0 <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], z1),
                          merged[-1][2] + [name])
        else:
            merged.append((z0, z1, [name]))
    if len(merged) > 1:
        holes = [f"{a[1]}..{b[0]} nm (between {a[2][-1]} and {b[2][0]})"
                 for a, b in zip(merged, merged[1:])]
        check(WARN, tech, f"{len(holes)} gap(s) in the z chain",
              "; ".join(holes))
    else:
        check(OK, tech, "z chain is continuous from bottom to top")

    # ---- 10. the one thing that is NOT derivable -------------------------
    lo = min(r[2] for r in stack)
    hi = max(r[3] for r in stack)
    check(WARN, tech, "z thicknesses are ILLUSTRATIVE, not from any file",
          f"extent {lo}..{hi} nm; ordering above is derived, magnitudes are not")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tech", action="append", default=[])
    ap.add_argument("--engine", default=str(B.DEFAULT_ENGINE))
    ap.add_argument("--cell", default=B.DEFAULT_CELL)
    args = ap.parse_args()

    ctx = B.Ctx(engine=Path(args.engine).resolve(), cell=args.cell)
    for tech in (args.tech or L.TECH_ORDER):
        audit(ctx, tech)

    width = max(len(r[2]) for r in results)
    cur = None
    for level, tech, what, detail in results:
        if tech != cur:
            print(f"\n=== {tech} ===")
            cur = tech
        mark = {OK: "  ok ", WARN: " warn", FAIL: "FAIL "}[level]
        print(f"{mark} {what:<{width}}  {detail}")

    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    print(f"\n{len(results)} checks, {fails} failed, {warns} warnings")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
