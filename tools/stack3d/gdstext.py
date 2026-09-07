"""Round-trip a generated GDS through a plain-text file.

Why this exists: GDS is binary, and some environments only accept text through
the door. This gives the viewer a text-only supply chain --

    .gds  --encode-->  .gdstxt  --decode-->  .gds

-- with `.gdstxt` as the committed artifact. `build.py` reads `.gdstxt`
directly, so rebuilding the page needs no klayout and no binary input at all;
klayout is only needed to go back to `.gds` for opening in a layout viewer.

Format (line-oriented, one record per line, `#` comments ignored):

    VERSION 1
    DBU 0.00025                 database unit in micrometres, from the source GDS
    CELL INV_X1                 every following record belongs to this cell
    BOX  15/0 0 -18 90 18       layer/datatype, then lx ly ux uy   (nanometres)
    POLY 17/0 x1 y1 x2 y2 ...   layer/datatype, then >=3 vertex pairs
    TEXT 15/0 45 0 VSS          layer/datatype, x y, then the label

Coordinates are nanometres, the same normalised space `dump_gds.py` produces,
so the three writers' differing SCALE/dbu pairs are already reconciled. A BOX
is just an axis-aligned POLY written compactly -- every shape these writers
emit today is one.

    python tools/stack3d/gdstext.py encode a.gds a.gdstxt
    python tools/stack3d/gdstext.py decode a.gdstxt a.gds
    python tools/stack3d/gdstext.py verify a.gds a.gdstxt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

VERSION = 1


# --------------------------------------------------------------------------
# reading (no klayout needed)
# --------------------------------------------------------------------------
def read_text(path: str | Path) -> dict:
    """Parse a .gdstxt into the same shape dump_gds.py returns."""
    out: dict = {"dbu": None, "cells": {}}
    cell = None
    for lineno, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        tag, *rest = line.split()
        try:
            if tag == "VERSION":
                if int(rest[0]) != VERSION:
                    raise SystemExit(
                        f"{path}:{lineno}: format version {rest[0]}, "
                        f"this tool speaks {VERSION}")
            elif tag == "DBU":
                out["dbu"] = float(rest[0])
            elif tag == "CELL":
                cell = rest[0]
                out["cells"].setdefault(cell, {})
            elif tag in ("BOX", "POLY", "TEXT"):
                if cell is None:
                    raise SystemExit(f"{path}:{lineno}: {tag} before any CELL")
                key = rest[0]
                bucket = out["cells"][cell].setdefault(
                    key, {"polygons": [], "texts": []})
                if tag == "BOX":
                    lx, ly, ux, uy = (float(v) for v in rest[1:5])
                    bucket["polygons"].append(
                        [[lx, ly], [lx, uy], [ux, uy], [ux, ly]])
                elif tag == "POLY":
                    vals = [float(v) for v in rest[1:]]
                    if len(vals) < 6 or len(vals) % 2:
                        raise SystemExit(
                            f"{path}:{lineno}: POLY needs an even count of at "
                            f"least 3 xy pairs, got {len(vals)} numbers")
                    bucket["polygons"].append(
                        [[vals[i], vals[i + 1]] for i in range(0, len(vals), 2)])
                else:  # TEXT
                    x, y = float(rest[1]), float(rest[2])
                    bucket["texts"].append(
                        {"s": " ".join(rest[3:]), "x": x, "y": y})
            else:
                raise SystemExit(f"{path}:{lineno}: unknown record {tag!r}")
        except (IndexError, ValueError) as e:
            raise SystemExit(f"{path}:{lineno}: malformed {tag} record ({e})")
    if out["dbu"] is None:
        raise SystemExit(f"{path}: no DBU record")
    if not out["cells"]:
        raise SystemExit(f"{path}: no CELL record")
    return out


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def _fmt(v: float) -> str:
    """Trim 15.5 -> '15.5' and 90.0 -> '90' so the file stays readable."""
    return f"{v:g}"


def _is_axis_aligned_rect(pts: list) -> bool:
    if len(pts) != 4:
        return False
    xs = {round(p[0], 6) for p in pts}
    ys = {round(p[1], 6) for p in pts}
    return len(xs) == 2 and len(ys) == 2


def write_text(data: dict, path: str | Path) -> int:
    """Serialise a dump_gds-shaped dict. Returns the number of shape records."""
    lines = [
        "# stack3d plain-text layout. See tools/stack3d/gdstext.py.",
        "# Coordinates are NANOMETRES (already normalised across writers).",
        f"VERSION {VERSION}",
        f"DBU {data['dbu']:g}",
    ]
    n = 0
    for cell_name in sorted(data["cells"]):
        lines.append("")
        lines.append(f"CELL {cell_name}")
        by_layer = data["cells"][cell_name]
        for key in sorted(by_layer, key=lambda k: [float(x) for x in k.split("/")]):
            v = by_layer[key]
            for poly in v["polygons"]:
                if _is_axis_aligned_rect(poly):
                    xs = [p[0] for p in poly]
                    ys = [p[1] for p in poly]
                    lines.append(f"BOX  {key} {_fmt(min(xs))} {_fmt(min(ys))} "
                                 f"{_fmt(max(xs))} {_fmt(max(ys))}")
                else:
                    flat = " ".join(_fmt(c) for p in poly for c in p)
                    lines.append(f"POLY {key} {flat}")
                n += 1
            for t in v["texts"]:
                lines.append(f"TEXT {key} {_fmt(t['x'])} {_fmt(t['y'])} {t['s']}")
                n += 1
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def write_gds(data: dict, path: str | Path) -> None:
    """Rebuild a real .gds. This is the one direction that needs klayout."""
    try:
        import klayout.db as pya
    except ImportError:
        raise SystemExit(
            "decode needs klayout (pip install klayout).\n"
            "  Encoding and building the page do not -- only going back to a\n"
            "  binary .gds for a layout viewer does.")
    layout = pya.Layout()
    layout.dbu = data["dbu"]
    nm = data["dbu"] * 1000.0           # nanometres -> database units
    for cell_name, by_layer in data["cells"].items():
        cell = layout.create_cell(cell_name)
        for key, v in by_layer.items():
            ln, dt = (int(x) for x in key.split("/"))
            li = layout.layer(ln, dt)
            for poly in v["polygons"]:
                pts = [pya.Point(int(round(x / nm)), int(round(y / nm)))
                       for x, y in poly]
                cell.shapes(li).insert(pya.Polygon(pts))
            for t in v["texts"]:
                cell.shapes(li).insert(pya.Text(
                    t["s"], int(round(t["x"] / nm)), int(round(t["y"] / nm))))
    layout.write(str(path))


# --------------------------------------------------------------------------
def _canonical(data: dict) -> list:
    """Order-independent shape set, for comparing a round-trip."""
    out = []
    for cell_name, by_layer in data["cells"].items():
        for key, v in by_layer.items():
            for poly in v["polygons"]:
                pts = tuple(sorted((round(x, 4), round(y, 4)) for x, y in poly))
                out.append((cell_name, key, "poly", pts))
            for t in v["texts"]:
                out.append((cell_name, key, "text",
                            (t["s"], round(t["x"], 4), round(t["y"], 4))))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["encode", "decode", "verify"])
    ap.add_argument("src")
    ap.add_argument("dst")
    args = ap.parse_args()

    if args.action == "encode":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dump_gds import dump
        data = dump(args.src)
        n = write_text(data, args.dst)
        print(f"{args.src} -> {args.dst}  ({n} shapes, "
              f"{Path(args.dst).stat().st_size:,} bytes of text)")
    elif args.action == "decode":
        write_gds(read_text(args.src), args.dst)
        print(f"{args.src} -> {args.dst}")
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dump_gds import dump
        a, b = _canonical(dump(args.src)), _canonical(read_text(args.dst))
        if a == b:
            print(f"identical: {len(a)} shapes match between "
                  f"{args.src} and {args.dst}")
        else:
            only_a = [x for x in a if x not in b]
            only_b = [x for x in b if x not in a]
            print(f"MISMATCH: {len(only_a)} only in {args.src}, "
                  f"{len(only_b)} only in {args.dst}")
            for x in (only_a + only_b)[:10]:
                print("   ", x)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
