"""Compile an engineer-written process description into a stack table.

WHY THIS EXISTS
---------------
``layers.py`` speaks the implementation's language: GDS layer/datatype keys and
absolute z intervals in nanometres. A process engineer does not think in GDS
datatypes, and asking them to invent z numbers is asking for the one thing the
repo genuinely does not know (see layers.py -- thicknesses are illustrative).

So the handover format inverts it. The engineer states two things they DO know:

    1. the ORDER of the layers, bottom to top -- that is just the process flow
    2. what each layer IS -- a diffusion, a gate, a via, a metal

and this module computes the z intervals from a per-role thickness table. The
engineer never types a coordinate.

FORMAT
------
TOML, because it takes comments, reads like an INI file, and ``tomllib`` is in
the Python standard library -- so nothing here needs installing, which is what
lets the whole tool rebuild inside an air-gapped environment.

    schema = "stack3d/tech@1"
    name = "MYCFET"

    [[tier]]
    name = "bottom"

    [[layer]]
    name = "N_ACTIVE"
    role = "diffusion"
    tier = "bottom"
    gds  = "11/2"

Layers are listed BOTTOM TO TOP in physical order. That ordering is the whole
z model -- there is no mirroring flag, because a backside tier whose contacts
reach downward is simply written with its interconnect before its diffusion.

    python tools/stack3d/techspec.py --check              # validate every techs/*.toml
    python tools/stack3d/techspec.py --export CFET        # existing tech -> TOML
    python tools/stack3d/techspec.py --show MYCFET        # compiled stack table
"""
from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TECH_DIR = HERE / "techs"
SCHEMA = "stack3d/tech@1"

# Per-role defaults. A role answers "what kind of thing is this", which is the
# question an engineer can always answer; everything visual and dimensional
# follows from it. Any field can still be overridden per layer.
#            thickness  group        colour     on
ROLES = {
    "substrate":  (24, "substrate", "#64748b", 0),
    "implant":    ( 6, "implant",   "#a78bfa", 0),
    "channel":    (42, "device",    "#22d3ee", 1),
    "diffusion":  (46, "device",    "#10b981", 1),
    "sd_trench":  (38, "device",    "#f59e0b", 1),
    "gate":       (62, "device",    "#ef4444", 1),
    "cut":        (22, "mask",      "#94a3b8", 0),
    "mol_sd":     (28, "mol",       "#fbbf24", 1),
    "mol_gate":   (12, "mol",       "#fb923c", 1),
    "via":        (16, "via",       "#e2e8f0", 1),
    "metal":      (14, "beol",      "#3b82f6", 1),
    "virtual":    ( 0, "virtual",   "#facc15", 0),
    "model_only": (40, "device",    "#7f1d1d", 1),
    "boundary":   ( 0, "mask",      "#94a3b8", 0),
}

# Layers that physically envelope others render semi-transparent, or they hide
# the very thing they exist to explain.
ROLE_ALPHA = {"gate": 0.34, "diffusion": 0.88, "cut": 0.5, "virtual": 0.3,
              "model_only": 0.5}


class SpecError(Exception):
    """A problem stated in the engineer's vocabulary, not the compiler's."""


def _require(cond, msg):
    if not cond:
        raise SpecError(msg)


# --------------------------------------------------------------------------
def compile_stack(spec: dict, where: str = "<spec>") -> list:
    """Turn a parsed spec into layers.py's stack-table tuples.

    Walks the layer list bottom to top keeping a z cursor. Four placements:

        (default)            starts where the previous layer ended
        gap = N              ... plus N nm of empty space first (tier isolation)
        align = "OTHER"      starts at the same height as OTHER, because real
                             flows have layers that co-occupy a level -- the
                             fins and the diffusion around them, or a
                             source/drain trench sitting inside the diffusion.
                             `offset` shifts up from that point.
        span = ["A", "B"]    stretches from A's bottom to B's top instead of
                             having a height of its own. This is how one gate
                             stack runs through two device tiers.

    The cursor always advances to the highest z reached so far, so an aligned
    layer never leaves the next sequential one overlapping it.
    """
    _require(spec.get("schema") == SCHEMA,
             f"{where}: schema must be {SCHEMA!r}, got {spec.get('schema')!r}")
    name = spec.get("name")
    _require(name, f"{where}: missing top-level `name`")

    layer_list = spec.get("layer", [])
    _require(layer_list, f"{name}: no [[layer]] entries -- nothing to build")

    tiers = [t["name"] for t in spec.get("tier", [])]
    seen, spans, out, z = {}, [], [], 0.0

    # Pass 1: everything with its own extent. Spanning layers wait for pass 2,
    # since they are defined in terms of layers that may come later.
    for i, ly in enumerate(layer_list):
        ln = ly.get("name")
        _require(ln, f"{name}: [[layer]] #{i + 1} has no `name`")
        _require(ln not in seen, f"{name}: duplicate layer name {ln!r}")
        role = ly.get("role")
        _require(role in ROLES,
                 f"{name}: layer {ln!r} has role {role!r}; known roles are "
                 f"{', '.join(sorted(ROLES))}")
        if "tier" in ly:
            _require(ly["tier"] in tiers,
                     f"{name}: layer {ln!r} names tier {ly['tier']!r}, which is "
                     f"not declared. Declared tiers: {tiers or '(none)'}")

        d_thick, d_group, d_color, d_on = ROLES[role]
        rec = {
            "name": ln, "role": role,
            "gds": ly.get("gds"),
            "group": ly.get("group", d_group),
            "color": ly.get("color", d_color),
            "on": int(ly.get("visible", bool(d_on))),
            "note": ly.get("note", ""),
            "tier": ly.get("tier"),
        }
        if "span" in ly:
            rec["span"] = ly["span"]
            spans.append(rec)
        else:
            thick = float(ly.get("thickness", d_thick))
            _require(thick > 0,
                     f"{name}: layer {ln!r} has thickness {thick}; a drawn "
                     f"layer needs a positive thickness")
            if "align" in ly:
                ref = ly["align"]
                if ref == "origin":
                    base = 0.0
                else:
                    _require(ref in seen,
                             f"{name}: layer {ln!r} aligns to {ref!r}, which is "
                             f"not a layer listed before it")
                    _require("z0" in seen[ref],
                             f"{name}: layer {ln!r} aligns to {ref!r}, but that "
                             f"layer is a span and has no height of its own")
                    base = seen[ref]["z0"]
                rec["z0"] = base + float(ly.get("offset", 0))
            else:
                rec["z0"] = z + float(ly.get("gap", 0))
            rec["z1"] = rec["z0"] + thick
            z = max(z, rec["z1"])
        seen[ln] = rec
        out.append(rec)

    # Pass 2: spans resolve against the extents just computed.
    for rec in spans:
        a, b = rec["span"][0], rec["span"][-1]
        for ref in (a, b):
            _require(ref in seen,
                     f"{name}: layer {rec['name']!r} spans {ref!r}, which is "
                     f"not a layer in this spec")
            _require("z0" in seen[ref],
                     f"{name}: layer {rec['name']!r} spans {ref!r}, but that "
                     f"layer is itself a span -- spans cannot chain")
        rec["z0"] = min(seen[a]["z0"], seen[b]["z0"])
        rec["z1"] = max(seen[a]["z1"], seen[b]["z1"])

    # layers.py order is display order; keep the file's own bottom-to-top order
    # so what the engineer wrote is what a reader sees.
    table = []
    for rec in out:
        _require(rec.get("gds") or rec["role"] in ("virtual", "model_only"),
                 f"{name}: layer {rec['name']!r} has no `gds`. Every drawn "
                 f"layer needs its GDS layer/datatype, e.g. gds = \"11/2\". "
                 f"If it is deliberately never drawn, set role = \"model_only\".")
        table.append((
            rec["gds"] or f"model/{rec['name']}", rec["name"],
            rec["z0"], rec["z1"], rec["group"], rec["color"],
            rec["note"], rec["on"],
        ))
    return table


def compile_alpha(spec: dict, table: list) -> dict:
    """Per-layer opacity, derived from role unless the spec overrides it."""
    by_name = {ly["name"]: ly for ly in spec.get("layer", [])}
    out = {}
    for key, nm, *_ in table:
        ly = by_name.get(nm, {})
        a = ly.get("alpha", ROLE_ALPHA.get(ly.get("role")))
        if a is not None and a < 1:
            out[key] = float(a)
    return out


def to_tech_entry(spec: dict, where: str) -> dict:
    """The dict layers.TECHS expects, plus the compiled stack."""
    table = compile_stack(spec, where)
    bind = spec.get("bind", {})
    missing = [k for k in ("preset", "layer_json", "gds_writer", "gds_argv")
               if k not in bind]
    _require(not missing,
             f"{spec['name']}: the [bind] section is missing {missing}. That "
             f"section wires the spec to the engine and is filled in by "
             f"whoever maintains the tool, not by the process engineer.")
    g = spec.get("geometry", {})
    return {
        "stack": table,
        "run": spec.get("run", spec["name"].lower()),
        "preset": bind["preset"],
        "layer_json": bind["layer_json"],
        "gds_writer": bind["gds_writer"],
        "gds_argv": bind["gds_argv"],
        "ignore_keys": bind.get("ignore_keys", []),
        "boundary_key": bind.get("boundary_key", "100/0"),
        "arch": g.get("summary", spec.get("summary", "")),
        "placement": g.get("placement", ""),
        "pin_access": g.get("pin_access", ""),
        "virtual": g.get("virtual_edges", "無"),
        "_alpha": compile_alpha(spec, table),
        "_order": spec.get("order", 999),
        "_source": where,
    }


def load_all() -> dict:
    """Every techs/*.toml, compiled. Errors name the file and the layer."""
    out = {}
    for p in sorted(TECH_DIR.glob("*.toml")):
        # A leading underscore marks a file that is not a technology --
        # _TEMPLATE.toml is a form to copy, not something to build.
        if p.name.startswith("_"):
            continue
        spec = tomllib.loads(p.read_text(encoding="utf-8"))
        entry = to_tech_entry(spec, p.name)
        nm = spec["name"]
        _require(nm not in out, f"two specs both define {nm!r}")
        out[nm] = entry
    return out


# --------------------------------------------------------------------------
def export(tech: str) -> str:
    """Render an existing Python-defined tech as a spec, for use as an example.

    Thicknesses and gaps are recovered from the absolute z intervals, so the
    exported file is in the same relative style an engineer would write by
    hand rather than a dump of coordinates.
    """
    sys.path.insert(0, str(HERE))
    import layers as L
    _require(tech in L.TECHS, f"{tech!r} is not in layers.TECHS")
    cfg = L.TECHS[tech]
    rows = sorted(cfg["stack"], key=lambda r: (r[2], r[3]))
    alpha = L.ALPHA.get(tech, {})

    inv = {v: k for k, v in {
        "substrate": "substrate", "implant": "implant", "device": "device",
    }.items()}
    def guess_role(name, group, z0, z1):
        n = name.upper()
        if group == "substrate": return "substrate"
        if group == "implant":   return "implant"
        if group in ("via", "miv"): return "via"
        if group in ("beol", "backbeol", "mid"): return "metal"
        if group == "mask":      return "cut"
        if group == "mol":       return "mol_gate" if "LIG" in n else "mol_sd"
        if group == "virtual":   return "virtual"
        if "(model)" in name:    return "model_only"
        if "FIN" in n:           return "channel"
        if "ACTIVE" in n:        return "diffusion"
        if "SDT" in n:           return "sd_trench"
        if "LISD" in n:          return "mol_sd"
        return "gate"

    lines = [
        "# Exported from layers.py by `techspec.py --export`. This is a worked",
        "# example of the handover format: layers are listed BOTTOM TO TOP and",
        "# carry a thickness, never an absolute coordinate.",
        f'schema = "{SCHEMA}"',
        f'name = "{tech}"',
        f'summary = "{cfg.get("arch", "")}"',
        "",
        "[geometry]",
        f'summary = "{cfg.get("arch", "")}"',
        f'placement = "{cfg.get("placement", "")}"',
        f'pin_access = "{cfg.get("pin_access", "")}"',
        f'virtual_edges = "{cfg.get("virtual", "")}"',
        "",
        "[bind]  # wiring to the engine -- not the process engineer's half",
        f'preset = "{cfg["preset"]}"',
        f'layer_json = "{cfg["layer_json"]}"',
        f'gds_writer = "{cfg["gds_writer"]}"',
        "gds_argv = [" + ", ".join(f'"{a}"' for a in cfg["gds_argv"]) + "]",
    ]
    if cfg.get("ignore_keys"):
        lines.append("ignore_keys = [" +
                     ", ".join(f'"{k}"' for k in cfg["ignore_keys"]) + "]")
    lines.append("")

    cursor, placed = 0.0, []
    for key, name, z0, z1, group, color, note, on in rows:
        role = guess_role(name, group, z0, z1)
        lines.append("[[layer]]")
        lines.append(f'name = "{name}"')
        lines.append(f'role = "{role}"')
        lines.append(f'gds  = "{key}"')
        gap = z0 - cursor
        if abs(gap) < 1e-9:
            pass                                   # simple sequential
        elif gap > 0:
            lines.append(f"gap = {gap:g}")
        else:
            # Starts below the cursor: it shares a level with something
            # already placed. Name that layer rather than emit a negative gap.
            ref = next((n for n, a in placed if abs(a - z0) < 1e-9), None)
            if ref:
                lines.append(f'align = "{ref}"')
            else:
                ref = next((n for n, a in placed if a < z0), None)
                if ref:
                    base = dict(placed)[ref]
                    lines.append(f'align = "{ref}"')
                    lines.append(f"offset = {z0 - base:g}")
                else:
                    lines.append('align = "origin"')
                    lines.append(f"offset = {z0:g}")
        lines.append(f"thickness = {z1 - z0:g}")
        d_thick, d_group, d_color, d_on = ROLES[role]
        if group != d_group: lines.append(f'group = "{group}"')
        if color != d_color: lines.append(f'color = "{color}"')
        if bool(on) != bool(d_on): lines.append(f"visible = {str(bool(on)).lower()}")
        if key in alpha and alpha[key] != ROLE_ALPHA.get(role):
            lines.append(f"alpha = {alpha[key]}")
        if note:
            lines.append(f'note = "{note.replace(chr(34), chr(39))}"')
        lines.append("")
        placed.append((name, z0))
        cursor = max(cursor, z1)
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate every techs/*.toml")
    ap.add_argument("--export", metavar="TECH", help="render an existing tech as TOML")
    ap.add_argument("--show", metavar="TECH", help="print a compiled stack table")
    args = ap.parse_args()

    try:
        if args.export:
            print(export(args.export))
            return
        specs = load_all()
        if args.show and args.show not in specs:
            for p in sorted(TECH_DIR.glob("_*.toml")):
                sp = tomllib.loads(p.read_text(encoding="utf-8"))
                if sp.get("name") == args.show:
                    specs[args.show] = to_tech_entry(sp, p.name)
        if args.show:
            _require(args.show in specs,
                     f"{args.show!r} not found. techs/ defines: {sorted(specs)}")
            e = specs[args.show]
            print(f"{args.show}  ({e['_source']})")
            print(f"  {'gds':>10} {'name':<18} {'z':>14}  {'group':<10} on")
            for k, n, z0, z1, g, c, note, on in e["stack"]:
                print(f"  {k:>10} {n:<18} {z0:>6g}→{z1:<7g} {g:<10} {'●' if on else '○'}")
            return
        if not specs:
            print("techs/ has no .toml specs yet (that is fine -- the built-in "
                  "technologies still live in layers.py)")
        for nm, e in specs.items():
            print(f"  ok  {nm:<12} {len(e['stack'])} layers  ({e['_source']})")
        print(f"\n{len(specs)} spec(s) validated")
    except SpecError as e:
        print(f"\nFAIL  {e}\n", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
