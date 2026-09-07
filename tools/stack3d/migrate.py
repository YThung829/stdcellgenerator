"""Turn a migration IR into the tween pairs the viewer animates.

A migration file (``migrations/<from>_to_<to>.json``) says which GDS layer
becomes which. This module checks that claim against the two technologies'
real stack tables and real geometry, then pairs individual boxes so the viewer
can interpolate one layout into the other.

The pairing is done HERE, at build time, rather than in JavaScript, for two
reasons: it is deterministic and inspectable (``--report`` prints every pair),
and a mismatch between the IR and the geometry becomes a build failure instead
of a silently wrong animation.

    python tools/stack3d/migrate.py --report
    python tools/stack3d/migrate.py --check      # validate only, exit non-zero on error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import layers as L                       # noqa: E402
import build as B                        # noqa: E402

MIG_DIR = HERE / "migrations"


# --------------------------------------------------------------------------
def bbox(poly: list) -> tuple:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def stack_index(tech: str) -> dict:
    """gds_key -> stack row, for one technology."""
    return {r[0]: r for r in L.TECHS[tech]["stack"]}


def x_overlap(a: tuple, b: tuple) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def role_of(box: tuple, fold: dict) -> str:
    """Which device a source box belongs to, from its position alone.

    The rule is in the IR (`fold.source_role_rule`) rather than inferred here,
    because it is a claim about the layout that a reader should be able to
    check: FinFET puts PMOS in the upper row band and NMOS in the lower one.
    A box centred exactly on the fold axis straddles both bands -- that is the
    merged local-interconnect case, where P and N share one S/D net.
    """
    cy = (box[1] + box[3]) / 2
    if abs(cy - fold["at_y"]) < 1e-6:
        return "straddle"
    return "P" if cy > fold["at_y"] else "N"


def pair_boxes(src: list, dst: list) -> list:
    """Greedily match source boxes to target boxes by X overlap.

    X is the right axis to match on: the migration moves things in Y and Z but
    a device stays in its column. Returns (src_idx|None, dst_idx|None) pairs;
    a None on either side means the box appears or disappears.
    """
    cand = sorted(
        ((x_overlap(s, d), si, di)
         for si, s in enumerate(src) for di, d in enumerate(dst)
         if x_overlap(s, d) > 0),
        key=lambda t: -t[0],
    )
    used_s, used_d, out = set(), set(), []
    for _, si, di in cand:
        if si in used_s or di in used_d:
            continue
        used_s.add(si); used_d.add(di)
        out.append((si, di))
    out += [(si, None) for si in range(len(src)) if si not in used_s]
    out += [(None, di) for di in range(len(dst)) if di not in used_d]
    return out


# --------------------------------------------------------------------------
def build_migration(ctx, mig: dict) -> dict:
    src_tech, dst_tech = mig["from"], mig["to"]
    for t in (src_tech, dst_tech):
        if t not in L.TECHS:
            raise SystemExit(f"{t!r} is not in layers.TECHS")

    src_stack, dst_stack = stack_index(src_tech), stack_index(dst_tech)
    src_geo = B.load_geometry(ctx, L.TECHS[src_tech], src_tech)
    dst_geo = B.load_geometry(ctx, L.TECHS[dst_tech], dst_tech)
    fold = mig["fold"]

    errors, pairs, rows = [], [], []

    def boxes(geo, key):
        return [bbox(p) for p in geo.get(key, {}).get("polygons", [])]

    for m in mig["masks"]:
        fk = m["from"]["key"]
        if fk not in src_stack:
            errors.append(f"{src_tech} stack has no {fk} ({m['from']['name']})")
        for t in m["to"]:
            if t["key"] not in dst_stack:
                errors.append(f"{dst_tech} stack has no {t['key']} ({t['name']})")
        if errors:
            continue

        src_boxes = boxes(src_geo, fk)
        sz = src_stack[fk]
        src_z = (sz[2], sz[3])

        # split_by_tier sends each source box to the sub-layer matching its
        # device role; everything else is a straight same-key correspondence.
        if m["kind"] == "split_by_tier":
            buckets = {t["role"]: t for t in m["to"]}
            assign = []
            for b in src_boxes:
                r = role_of(b, fold)
                if r == "straddle":
                    # A merged FinFET LISD spans both bands and becomes one
                    # LISD per tier, so it feeds BOTH targets.
                    assign += [(b, buckets[k]) for k in ("P", "N") if k in buckets]
                elif r in buckets:
                    assign.append((b, buckets[r]))
                else:
                    assign.append((b, None))
        else:
            assign = [(b, m["to"][0]) for b in src_boxes]

        for tgt in m["to"]:
            tz_row = dst_stack[tgt["key"]]
            dst_z = (tz_row[2], tz_row[3])
            mine = [b for b, t in assign if t is tgt]
            dst_boxes = boxes(dst_geo, tgt["key"])
            n_morph = n_in = n_out = 0
            for si, di in pair_boxes(mine, dst_boxes):
                if si is not None and di is not None:
                    mode, a, b = "morph", mine[si], dst_boxes[di]; n_morph += 1
                elif si is not None:
                    mode, a, b = "out", mine[si], None; n_out += 1
                else:
                    mode, a, b = "in", None, dst_boxes[di]; n_in += 1
                pairs.append({
                    "m": mode,
                    "r": (role_of(a, fold) if a else tgt["role"]),
                    "k": m["kind"],
                    "n": f"{m['from']['name']} → {tgt['name']}",
                    "ka": fk, "kb": tgt["key"],
                    "ca": sz[5], "cb": tz_row[5],
                    "a": ([*a, *src_z] if a else None),
                    "b": ([*b, *dst_z] if b else None),
                })
            rows.append((m["kind"], f"{fk} {m['from']['name']}",
                         f"{tgt['key']} {tgt['name']}",
                         len(mine), len(dst_boxes), n_morph, n_in, n_out))

    if errors:
        for e in errors:
            print(f"  FAIL {e}")
        raise SystemExit(f"{len(errors)} error(s): the migration IR does not "
                         f"match layers.py")

    # Anything the writer draws that no mapping mentions would quietly sit out
    # the animation, which is the same failure mode build.py guards against.
    ignored = set(mig.get("target_union_copies", {}).get("keys", ()))
    ignored |= {L.TECHS[dst_tech].get("boundary_key", "100/0")}
    claimed_dst = {t["key"] for m in mig["masks"] for t in m["to"]}
    unmapped = sorted(
        (k for k, v in dst_geo.items()
         if v["polygons"] and k not in claimed_dst and k not in ignored),
        key=lambda k: [float(x) for x in k.split("/")])
    if unmapped:
        print(f"  !! {dst_tech} draws {len(unmapped)} layer(s) no mapping "
              f"mentions: {', '.join(unmapped)}")

    return {
        "from": src_tech, "to": dst_tech,
        "headline": mig["headline"],
        "fold": fold,
        "invariants": mig["invariants"],
        "model": mig["model"],
        "masks": [{"kind": m["kind"], "from": m["from"], "to": m["to"],
                   "note": m["note"]} for m in mig["masks"]],
        "pairs": pairs,
    }, rows


def load(name: str) -> dict:
    p = MIG_DIR / f"{name}.json"
    if not p.is_file():
        avail = sorted(q.stem for q in MIG_DIR.glob("*.json"))
        raise SystemExit(f"no migration {name!r}. Available: {avail}")
    return json.loads(p.read_text(encoding="utf-8"))


def all_migrations(ctx, quiet: bool = False) -> dict:
    out = {}
    for p in sorted(MIG_DIR.glob("*.json")):
        mig = load(p.stem)
        payload, rows = build_migration(ctx, mig)
        out[p.stem] = payload
        if not quiet:
            morph = sum(1 for x in payload["pairs"] if x["m"] == "morph")
            print(f"  {mig['from']} → {mig['to']}: {len(mig['masks'])} mappings, "
                  f"{len(payload['pairs'])} box pairs ({morph} morph)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=None, help="one migration (default: all)")
    ap.add_argument("--report", action="store_true", help="print the pairing table")
    ap.add_argument("--check", action="store_true", help="validate only")
    ap.add_argument("--engine", default=str(B.DEFAULT_ENGINE))
    ap.add_argument("--cell", default=B.DEFAULT_CELL)
    args = ap.parse_args()

    ctx = B.Ctx(engine=Path(args.engine).resolve(), cell=args.cell)
    names = [args.name] if args.name else [p.stem for p in sorted(MIG_DIR.glob("*.json"))]
    for name in names:
        mig = load(name)
        payload, rows = build_migration(ctx, mig)
        print(f"\n=== {mig['from']} → {mig['to']} ===")
        if args.report:
            print(f"  {'kind':<16} {'from':<22} {'to':<22} "
                  f"{'src':>4} {'dst':>4} {'morph':>6} {'in':>3} {'out':>4}")
            for k, f, t, ns, nd, nm, ni, no in rows:
                print(f"  {k:<16} {f:<22} {t:<22} {ns:>4} {nd:>4} "
                      f"{nm:>6} {ni:>3} {no:>4}")
        by = {}
        for p in payload["pairs"]:
            by[p["m"]] = by.get(p["m"], 0) + 1
        print(f"  {len(mig['masks'])} mappings, {len(payload['pairs'])} box pairs "
              f"({', '.join(f'{v} {k}' for k, v in sorted(by.items()))})")
    print("\nok" if not args.check else "\nvalidated")


if __name__ == "__main__":
    main()
