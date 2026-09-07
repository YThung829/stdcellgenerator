"""Read a generated GDS and dump every shape, grouped by (layer, datatype).

Coordinates come back in NANOMETRES regardless of which writer produced the
file. The three writers use different SCALE/dbu pairs that happen to multiply
out the same:

    gds_FinFET_SH.py   SCALE 4   dbu 0.00025   ->  0.001 um per writer unit
    gds_CFET_SH.py     SCALE 10  dbu 0.0001    ->  0.001 um per writer unit
    gds_QFET_SH.py     SCALE 4   dbu 0.00025   ->  0.001 um per writer unit

Normalising through ``dbu * 1000`` therefore lands all three in one shared nm
space, which is what lets the viewer put them side by side.
"""
from __future__ import annotations

import argparse
import json

import klayout.db as pya


def dump(gds_path: str) -> dict:
    """Return ``{"dbu": float, "cells": {cell: {"L/D": {polygons, texts}}}}``."""
    layout = pya.Layout()
    layout.read(gds_path)
    dbu = layout.dbu                      # micrometres per database unit
    nm = dbu * 1000.0                     # database units -> nanometres

    out = {"dbu": dbu, "cells": {}}
    for cell in layout.top_cells():
        by_layer: dict[str, dict] = {}
        for li in layout.layer_indexes():
            info = layout.get_info(li)
            key = f"{info.layer}/{info.datatype}"
            polys, texts = [], []
            for sh in cell.shapes(li).each():
                if sh.is_box() or sh.is_polygon() or sh.is_path():
                    poly = sh.polygon
                    if poly is None:
                        continue
                    polys.append([
                        [round(pt.x * nm, 3), round(pt.y * nm, 3)]
                        for pt in poly.each_point_hull()
                    ])
                elif sh.is_text():
                    t = sh.text
                    texts.append({
                        "s": t.string,
                        "x": round(t.x * nm, 3),
                        "y": round(t.y * nm, 3),
                    })
            if polys or texts:
                by_layer[key] = {"polygons": polys, "texts": texts}
        out["cells"][cell.name] = by_layer
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("gds", help="input .gds")
    ap.add_argument("out", help="output .json")
    args = ap.parse_args()

    data = dump(args.gds)
    with open(args.out, "w") as f:
        json.dump(data, f, indent=1)
    n = sum(len(v["polygons"]) for c in data["cells"].values() for v in c.values())
    print(f"{args.gds} -> {args.out}  ({n} polygons, dbu={data['dbu']})")


if __name__ == "__main__":
    main()
