"""The routing view of a process: the minimum a router needs to be told.

WHY A SECOND, NARROWER IR
-------------------------
``tools/stack3d/techs/*.toml`` describes the full mask stack -- 34 layers for
QFET -- because a picture needs wells, implants, fins and cut masks. A router
needs almost none of that. Physical design abstracts a process down to roughly

    OD · POLY · MD · VIA · M0 · M1

and that handful, plus the grid each one sits on, is the whole routing view.
This module defines that view, validates it against the engine's own layer
JSON so it cannot drift, and can emit the routing half of a new one.

THE DISTINCTION THAT MATTERS MOST
---------------------------------
Not every layer in the abstraction is a layer in the router's graph. In this
engine:

  * POLY and the metals ARE graph layers -- the LayeredGridGraph puts nodes on
    them, and a route is a path through those nodes.
  * OD and MD are NOT. `ACTIVE` and `LISD` are `gds`-only entries the solver
    never sees. "Routing on MD" is modelled as PERMISSION on the POLY<->M0 via
    (the `lisd_routing` / `lig_routing` flags decide whether that via may be
    taken on source/drain columns or on gate columns), not as a separate layer
    to route along.

An IR with a flat list of six layers would quietly imply MD is routable the
same way M0 is, and a process engineer filling it in would reasonably expect a
track pitch for it. So every layer here declares `in_graph`, and a layer with
`in_graph: false` declares instead how the router reaches it: through which
graph layer, and behind which capability flag.

EXTENSIBILITY
-------------
Stated as rules rather than hopes, because the format will outlive this file:

  1. `schema` carries a major version. A reader MUST refuse a major it does not
     know; minor additions are additive and safe to ignore.
  2. Any key beginning `x_` is a vendor extension. Readers preserve and ignore
     it. Use it before inventing a new standard key.
  3. Unknown `class` values are a warning, not an error, and fall back to being
     treated as opaque geometry. A new process can name something this file has
     never heard of without waiting for this file to change.
  4. `tiers` is a list of any length. Nothing assumes one or two.
  5. Every section except `name`, `schema` and `layers` is optional with a
     documented default.

    python tools/routing-ir/routing_ir.py --check
    python tools/routing-ir/routing_ir.py --show CFET
    python tools/routing-ir/routing_ir.py --derive FinFET   # from the engine files
    python tools/routing-ir/routing_ir.py --emit MYTECH     # -> engine layer JSON
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC_DIR = HERE / "specs"
REPO = HERE.parents[1]
DEFAULT_ENGINE = Path(os.environ.get("STACK3D_ENGINE", REPO / "engine"))

SCHEMA_MAJOR = 1
SCHEMA = f"smtcell/routing-ir@{SCHEMA_MAJOR}"

# The routing abstraction. `graph` says whether the solver puts nodes on it --
# the single most consequential field, and the one most likely to be assumed
# wrong.
CLASSES = {
    "od":      {"graph": False, "what": "diffusion / active. Where devices are. "
                                        "Never routed along; the router only needs "
                                        "to know where it is."},
    "poly":    {"graph": True,  "what": "gate. Also a placement tier: transistors "
                                        "sit on it. Vertical, on the CPP grid."},
    "md":      {"graph": False, "what": "source/drain local interconnect. In THIS "
                                        "engine it is not a graph layer -- reaching "
                                        "it is a permission on the poly<->metal via."},
    "metal":   {"graph": True,  "what": "a routing layer proper. Needs direction, "
                                        "pitch, offset, width."},
    "via":     {"graph": True,  "what": "connects two named layers; declared in "
                                        "`connect`, not here."},
    "virtual": {"graph": True,  "what": "graph-only shortcut with no mask. Declared "
                                        "in `shortcuts`."},
}

REQUIRED_IF_METAL = ("direction", "pitch", "offset", "width")


class IRError(Exception):
    """A problem phrased for whoever filled in the form."""


def _req(cond, msg):
    if not cond:
        raise IRError(msg)


# --------------------------------------------------------------------------
def load(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    where = path.name
    got = spec.get("schema", "")
    _req(got.startswith("smtcell/routing-ir@"),
         f"{where}: `schema` must start with 'smtcell/routing-ir@', got {got!r}")
    major = got.rsplit("@", 1)[-1].split(".")[0]
    _req(major == str(SCHEMA_MAJOR),
         f"{where}: schema major version {major} but this reader speaks "
         f"{SCHEMA_MAJOR}. A major bump means the format changed incompatibly.")
    _req(spec.get("name"), f"{where}: missing `name`")
    _req(spec.get("layers"), f"{spec.get('name', where)}: no `layers`")
    return spec


def validate(spec: dict) -> list:
    """Return warnings; raise IRError on anything that makes the spec unusable."""
    name = spec["name"]
    warn = []
    tiers = {t["id"] for t in spec.get("tiers", [])}
    ids = {}

    for i, ly in enumerate(spec["layers"]):
        lid = ly.get("id")
        _req(lid, f"{name}: layer #{i + 1} has no `id`")
        _req(lid not in ids, f"{name}: duplicate layer id {lid!r}")
        cls = ly.get("class")
        _req(cls, f"{name}: layer {lid!r} has no `class`. One of: "
                  f"{', '.join(sorted(CLASSES))}")
        if cls not in CLASSES:
            # Rule 3: an unknown class is survivable, so a new process is not
            # blocked on this file learning about it.
            warn.append(f"{name}: layer {lid!r} has unknown class {cls!r}; "
                        f"treated as opaque geometry (not in the routing graph)")
        if "tier" in ly:
            _req(ly["tier"] in tiers,
                 f"{name}: layer {lid!r} names tier {ly['tier']!r}, which is not "
                 f"declared in `tiers`. Declared: {sorted(tiers) or '(none)'}")
        in_graph = ly.get("in_graph", CLASSES.get(cls, {}).get("graph", False))
        if in_graph and cls == "metal":
            missing = [k for k in REQUIRED_IF_METAL if k not in ly]
            _req(not missing,
                 f"{name}: layer {lid!r} is a routing metal, so it needs "
                 f"{missing} -- those define the track grid the router uses.")
        if not in_graph and cls in ("md", "od"):
            _req("access_via" in ly,
                 f"{name}: layer {lid!r} is not in the routing graph, so it must "
                 f"say `access_via`: which graph layer the router reaches it "
                 f"through. In this engine that is the poly layer.")
        ids[lid] = ly

    for ly in spec["layers"]:
        ref = ly.get("access_via")
        if ref:
            _req(ref in ids,
                 f"{name}: layer {ly['id']!r} has access_via {ref!r}, which is "
                 f"not a layer in this spec")

    for c in spec.get("connect", []):
        for end in ("lower", "upper"):
            _req(c.get(end) in ids,
                 f"{name}: via {c.get('via', '?')!r} names {end} "
                 f"{c.get(end)!r}, which is not a layer in this spec")
    for s in spec.get("shortcuts", []):
        for end in ("a", "b"):
            _req(s.get(end) in ids,
                 f"{name}: shortcut names {s.get(end)!r}, which is not a layer "
                 f"in this spec")
    for group in ("access", "io"):
        for p in spec.get("pins", {}).get(group, []):
            _req(p in ids, f"{name}: pins.{group} names {p!r}, not a layer here")
    return warn


def cross_check(spec: dict, engine: Path) -> list:
    """Compare against the engine's own layer JSON.

    This is what stops the IR becoming a parallel document that drifts. Every
    grid number here also exists in engine/input/layer/*.json, and the two must
    agree -- if they do not, one of them is lying to somebody.
    """
    name = spec["name"]
    src = spec.get("source", {}).get("layer_json")
    if not src:
        return [f"{name}: no source.layer_json, so nothing to cross-check "
                f"against. Add it once the engine files exist."]
    p = engine / "input" / "layer" / src
    if not p.is_file():
        return [f"{name}: source.layer_json {src!r} not found under {p.parent}"]

    real = json.loads(p.read_text())
    by_name = {v.get("layer_name"): v for v in real.values()}
    problems = []
    for ly in spec["layers"]:
        if not ly.get("in_graph", CLASSES.get(ly["class"], {}).get("graph", False)):
            continue
        r = by_name.get(ly["id"])
        if r is None:
            problems.append(f"{name}: {ly['id']!r} is in the routing graph here "
                            f"but absent from {src}")
            continue
        for key, jkey in (("direction", "direction"), ("pitch", "pitch"),
                          ("offset", "offset"), ("width", "width")):
            if key in ly and jkey in r and float_ne(ly[key], r[jkey]):
                problems.append(f"{name}: {ly['id']}.{key} is {ly[key]!r} here "
                                f"but {r[jkey]!r} in {src}")
    # via chain
    real_vias = {(v["lower_layer"], v["upper_layer"]): v.get("layer_name")
                 for v in real.values() if v.get("layer_type") == "via"}
    for c in spec.get("connect", []):
        pair = (c["lower"], c["upper"])
        if pair not in real_vias:
            problems.append(f"{name}: connect {c['lower']}->{c['upper']} has no "
                            f"matching via in {src}")
    for pair, vname in real_vias.items():
        if not any((c["lower"], c["upper"]) == pair for c in spec.get("connect", [])):
            problems.append(f"{name}: {src} declares via {vname} "
                            f"{pair[0]}->{pair[1]} that this spec does not list")
    return problems


def float_ne(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) > 1e-9
    except (TypeError, ValueError):
        return str(a) != str(b)


# --------------------------------------------------------------------------
def derive(tech: str, engine: Path) -> dict:
    """Draft a spec from the engine's files, as a starting point.

    Classification is a guess from the layer name and is meant to be reviewed;
    the point is to save transcription, not to be authoritative.
    """
    sys.path.insert(0, str(REPO / "tools" / "stack3d"))
    import layers as L
    _req(tech in L.TECHS, f"{tech!r} is not a known technology")
    cfg = L.TECHS[tech]
    p = engine / "input" / "layer" / cfg["layer_json"]
    real = json.loads(p.read_text())
    metals = sorted(((v["layer_number"], v) for v in real.values()
                     if v.get("layer_type") == "metal"))

    def classify(n):
        return "poly" if ("PC" in n) else "metal"

    layers = []
    for _, v in metals:
        n = v["layer_name"]
        cls = classify(n)
        e = {"id": n, "class": cls, "in_graph": True,
             "direction": v["direction"], "pitch": v["pitch"],
             "offset": v["offset"], "width": v["width"],
             "gds": f"{v.get('gds_layer')}/{v.get('gds_datatype', 0)}"}
        if v.get("io_pin"):
            e["io_pin"] = True
        layers.append(e)

    connect = [{"lower": v["lower_layer"], "upper": v["upper_layer"],
                "via": v.get("layer_name"),
                "gds": (f"{v['gds_layer']}/{v.get('gds_datatype', 0)}"
                        if v.get("gds_layer") is not None else None)}
               for v in real.values() if v.get("layer_type") == "via"]
    connect.sort(key=lambda c: [m[1]["layer_name"] for m in metals].index(c["lower"])
                 if c["lower"] in [m[1]["layer_name"] for m in metals] else 99)
    shortcuts = [{"a": v["lower_layer"], "b": v["upper_layer"],
                  "method": v.get("method", "overlap")}
                 for v in real.values() if v.get("layer_type") == "virtual"]

    return {
        "schema": SCHEMA,
        "name": tech,
        "summary": cfg.get("arch", ""),
        "source": {"layer_json": cfg["layer_json"], "preset": cfg["preset"]},
        "tiers": [], "layers": layers, "connect": connect,
        "shortcuts": shortcuts,
        "pins": {"access": [], "io": [m[1]["layer_name"] for m in metals
                                      if m[1].get("io_pin")]},
    }


def emit_layer_json(spec: dict) -> dict:
    """The routing half of an engine layer JSON.

    Only metal / via / virtual entries -- the `gds`-only layers (well, implant,
    fin, cut) are not part of the routing view by definition and must be added
    separately. The caller is told this rather than getting a file that looks
    complete and is not.
    """
    out, n = {}, 0
    for ly in spec["layers"]:
        if not ly.get("in_graph", CLASSES.get(ly["class"], {}).get("graph", False)):
            continue
        n += 10
        gl, gd = (ly.get("gds") or "0/0").split("/")
        e = {"layer_type": "metal", "layer_number": n, "layer_name": ly["id"],
             "direction": ly["direction"], "offset": float(ly["offset"]),
             "pitch": float(ly["pitch"]), "width": float(ly["width"]),
             "gds_layer": int(gl), "gds_datatype": int(gd)}
        if ly.get("io_pin"):
            e["io_pin"] = True
        if "note" in ly:
            e["info"] = ly["note"]
        out[ly["id"]] = e
    for c in spec.get("connect", []):
        n += 1
        e = {"layer_type": "via", "layer_number": n,
             "layer_name": c.get("via") or f"V_{c['lower']}_{c['upper']}",
             "lower_layer": c["lower"], "upper_layer": c["upper"]}
        if c.get("gds"):
            gl, gd = c["gds"].split("/")
            e["gds_layer"], e["gds_datatype"] = int(gl), int(gd)
        out[e["layer_name"]] = e
    for s in spec.get("shortcuts", []):
        n += 1
        out[f"VL_{s['a']}_{s['b']}"] = {
            "layer_type": "virtual", "layer_number": n,
            "layer_name": f"VL_{s['a']}_{s['b']}",
            "lower_layer": s["a"], "upper_layer": s["b"],
            "method": s.get("method", "overlap")}
    return out


# --------------------------------------------------------------------------
def all_specs() -> dict:
    out = {}
    for p in sorted(SPEC_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        spec = load(p)
        out[spec["name"]] = (spec, p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", metavar="NAME")
    ap.add_argument("--derive", metavar="TECH")
    ap.add_argument("--emit", metavar="NAME")
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE), type=Path)
    args = ap.parse_args()
    engine = Path(args.engine).resolve()

    try:
        if args.derive:
            print(json.dumps(derive(args.derive, engine), indent=2, ensure_ascii=False))
            return
        specs = all_specs()
        if args.emit:
            _req(args.emit in specs, f"{args.emit!r} not found. Have: {sorted(specs)}")
            print(json.dumps(emit_layer_json(specs[args.emit][0]), indent=4,
                             ensure_ascii=False))
            print("\n// NOTE: routing layers only. The gds-only layers (well, "
                  "implant, fin, cut) are\n// not part of the routing view and "
                  "must be added before the GDS writer can run.",
                  file=sys.stderr)
            return
        if args.show:
            _req(args.show in specs, f"{args.show!r} not found. Have: {sorted(specs)}")
            spec, p = specs[args.show]
            print(f"{spec['name']}  ({p.name})   {spec.get('summary', '')}")
            print(f"\n  {'id':<8} {'class':<8} {'graph':<6} {'tier':<6} "
                  f"{'dir':<4} {'pitch':>6} {'offset':>7} {'width':>6}  gds")
            for ly in spec["layers"]:
                g = ly.get("in_graph", CLASSES.get(ly["class"], {}).get("graph", False))
                print(f"  {ly['id']:<8} {ly['class']:<8} {'yes' if g else 'no':<6} "
                      f"{ly.get('tier', '-'):<6} {ly.get('direction', '-'):<4} "
                      f"{ly.get('pitch', '-')!s:>6} {ly.get('offset', '-')!s:>7} "
                      f"{ly.get('width', '-')!s:>6}  {ly.get('gds', '-')}")
            if spec.get("connect"):
                print("\n  via chain")
                for c in spec["connect"]:
                    print(f"    {c.get('via', '?'):<6} {c['lower']:>7} -> "
                          f"{c['upper']:<7} gds {c.get('gds') or '(none)'}")
            for s in spec.get("shortcuts", []):
                print(f"\n  shortcut  {s['a']} <-> {s['b']}  method={s.get('method')}")
            return

        if not specs:
            print("specs/ has no routing IR files yet")
            return
        bad = 0
        for nm, (spec, p) in specs.items():
            warns = validate(spec)
            probs = cross_check(spec, engine)
            n_graph = sum(1 for l in spec["layers"]
                          if l.get("in_graph", CLASSES.get(l["class"], {}).get("graph", False)))
            status = "ok  " if not probs else "FAIL"
            bad += bool(probs)
            print(f"  {status} {nm:<8} {len(spec['layers'])} layers "
                  f"({n_graph} in the routing graph)  ({p.name})")
            for w in warns:
                print(f"       warn  {w}")
            for x in probs:
                print(f"       FAIL  {x}")
        print(f"\n{len(specs)} spec(s), {bad} with problems")
        if bad:
            raise SystemExit(1)
    except IRError as e:
        print(f"\nFAIL  {e}\n", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
