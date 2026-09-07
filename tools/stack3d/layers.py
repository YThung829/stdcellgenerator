"""Per-technology Z model for the stack viewer.

WHAT IS REAL AND WHAT IS NOT
----------------------------
The engine is 2D. ``input/layer/*.json`` describes each layer only in the
plane -- ``direction`` / ``pitch`` / ``offset`` / ``width`` -- and GDS is a
planar format, so nothing in the repo carries a layer THICKNESS. This module
supplies that missing axis so the viewer can draw a stack:

  * ORDER is derived, not invented. It follows the ``layer_number`` ordering
    the ``LayerStack`` sorts metals into (that ordering *is* the LGG z-index)
    plus each via's ``lower_layer -> upper_layer`` chain.
  * z0 / z1 are ILLUSTRATIVE nanometre values chosen to keep that order and
    the "which layer spans which" relationships correct. They are not PDK
    numbers and must not be read as such.

Everything else in the payload (polygon coordinates, objective values, cell
extents, LGG order) is read back from real generated files by ``build.py``.

Each stack entry is::

    (gds_key, display_name, z0, z1, group, color, note, default_on)

``gds_key`` is ``"<layer>/<datatype>"`` and must match what the GDS writers
actually emit -- an entry with no matching geometry still shows in the viewer's
rail, greyed out, which is how an empty tier stays visible instead of silently
vanishing.
"""

# --------------------------------------------------------------------------
# FinFET -- single planar tier. P and N sit in two row bands of one plane.
# --------------------------------------------------------------------------
FINFET = [
    ("1/0",   "WELL",       -28,  -4, "substrate", "#64748b", "n-well / bulk marker", 0),
    ("12/0",  "NSELECT",     -6,  -2, "implant",   "#a78bfa", "n-implant mask", 0),
    ("13/0",  "PSELECT",     -6,  -2, "implant",   "#f472b6", "p-implant mask", 0),
    ("2/0",   "FIN",           0,  42, "device",   "#22d3ee", "fin channel grid (5 fins, pitch 18 / width 6)", 1),
    ("11/0",  "ACTIVE",        0,  46, "device",   "#10b981", "diffusion (OD): PMOS band on top, NMOS below", 1),
    ("88/0",  "SDT",           8,  46, "device",   "#f59e0b", "source/drain trench (epi)", 1),
    ("7/0",   "PC / GATE",     0,  62, "device",   "#ef4444", "poly gate bar at every CPP (45nm)", 1),
    ("10/0",  "GATE_CUT",     44,  66, "mask",     "#94a3b8", "gate cut between P and N gate nets", 0),
    ("17/0",  "LISD",         46,  74, "mol",      "#fbbf24", "local interconnect to source/drain", 1),
    ("16/0",  "LIG",          62,  74, "mol",      "#fb923c", "local interconnect to gate", 1),
    ("14/0",  "CA",           74,  90, "via",      "#e2e8f0", "contact via PC -> M0", 1),
    ("15/0",  "M0",           90, 104, "beol",     "#3b82f6", "M0, horizontal, pitch 24 (also the M0BPR power rails)", 1),
    ("18/0",  "V0",          104, 118, "via",      "#e2e8f0", "via M0 -> M1", 1),
    ("19/0",  "M1",          118, 132, "beol",     "#8b5cf6", "M1, vertical, pitch 30", 1),
    ("21/0",  "V1",          132, 146, "via",      "#e2e8f0", "via M1 -> M2", 1),
    ("20/0",  "M2",          146, 160, "beol",     "#ec4899", "M2, horizontal, pitch 24", 1),
]

# --------------------------------------------------------------------------
# CFET -- two device tiers stacked in the SAME column footprint. The writer
# distinguishes them only by GDS datatype (/1 top, /2 bottom); the z model is
# what pulls them apart. The gate spans both tiers on purpose: physically one
# gate stack runs through both devices, so the writer emits a single 7/0 bar.
# --------------------------------------------------------------------------
CFET = [
    ("1/0",   "WELL",       -28,  -4, "substrate", "#64748b", "bulk / well marker", 0),
    ("12/0",  "NSELECT",     -6,  -2, "implant",   "#a78bfa", "n-implant mask", 0),
    ("2/0",   "FIN",           0,  42, "bot",      "#22d3ee", "bottom-tier fin channel", 1),
    ("11/2",  "N_ACTIVE",      0,  46, "bot",      "#10b981", "BOTTOM tier diffusion (NMOS, on BPC)", 1),
    ("88/2",  "N_SDT",         8,  46, "bot",      "#f59e0b", "bottom-tier source/drain trench", 1),
    ("17/2",  "N_LISD",       46,  66, "bot",      "#fbbf24", "bottom-tier local interconnect (S/D). CFET routes on LISD/LIG", 1),
    ("13/0",  "PSELECT",      86,  90, "implant",  "#f472b6", "p-implant mask (top tier)", 0),
    ("11/1",  "P_ACTIVE",     92, 138, "top",      "#34d399", "TOP tier diffusion (PMOS, on PC) - same X/Y footprint as N_ACTIVE", 1),
    ("88/1",  "P_SDT",       100, 138, "top",      "#fbbf24", "top-tier source/drain trench", 1),
    ("17/1",  "P_LISD",      138, 158, "top",      "#fcd34d", "top-tier local interconnect (S/D)", 1),
    ("7/0",   "GATE (PC+BPC)", 0, 150, "device",   "#ef4444", "ONE gate stack runs through both tiers - the CFET signature", 1),
    ("10/0",  "GATE_CUT",    130, 154, "mask",     "#94a3b8", "gate cut", 0),
    ("16/0",  "LIG",         150, 174, "mol",      "#fb923c", "local interconnect to gate", 1),
    ("14/0",  "CA",          158, 174, "via",      "#e2e8f0", "contact via PC/BPC -> M0", 1),
    ("15/0",  "M0",          174, 188, "beol",     "#3b82f6", "M0, horizontal, pitch 24", 1),
    ("18/0",  "V0",          188, 202, "via",      "#e2e8f0", "via M0 -> M1", 1),
    ("19/0",  "M1",          202, 216, "beol",     "#8b5cf6", "M1, vertical, pitch 30", 1),
    ("20/0",  "M2",          216, 230, "beol",     "#ec4899", "M2, horizontal, pitch 24", 1),
    # 11/0, 17/0 and 88/0 (datatype 0) are the union copies the writer also
    # emits; listing them would double-render both tiers, so they are omitted.
]

# --------------------------------------------------------------------------
# QFET -- a FRONTSIDE and a BACKSIDE device tier, with an H0/H1 + MIV ladder
# between them and a backside BEOL below. Negative z is the backside.
#
# Two things make this different from CFET, and both are load-bearing:
#
#  * A tier here is a WAFER FACE, not a device type. Each tier carries its own
#    PMOS (row 2) and NMOS (row 0) -- see _compute_placement_row_indices in
#    archit/QFET/main.py. So 11/1 vs 11/2 differ in Y, while 11/* vs 511/*
#    differ in z. CFET's tiers, by contrast, ARE the P/N split.
#  * The backside tier is a MIRROR of the frontside. BCA1 is declared
#    BM0 -> BPC1 and BM0 sits BELOW BPC1 in the layer_number order, so the
#    back device's contacts reach DOWNWARD: BLISD1 sits under the back
#    diffusion, where LISD1 sits over the front one.
#
# Ordering mirrors the layer_number sort in PROBE3_QFET_2F_4T_4242OF21.json:
#   BM1 < BV0 < BM0 < BCA1 < BPC1 < MIV1 < H0 < MIV2 < H1 < MIV3 < PC1 < CA1
#   < M0 < V0 < M1
#
# Note there is NO LIG layer in the QFET stack (unlike FinFET/CFET), so CA1
# lands directly on PC1 and the gate bar has to reach the CA1 reference plane
# at z=74 rather than stopping at 62.
# --------------------------------------------------------------------------
QFET = [
    # ---- backside BEOL (deepest) ----
    ("519/0", "BM1",           -208, -194, "backbeol", "#a855f7", "backside M1, vertical, pitch 42 offset 21", 1),
    ("518/0", "BV0",           -194, -180, "via",      "#e2e8f0", "backside via BM1 -> BM0", 1),
    ("515/0", "BM0",           -180, -166, "backbeol", "#60a5fa", "backside M0, horizontal, pitch 24 - carries the backside power rails and backside pin access", 1),
    ("514/0", "BCA1",          -166, -150, "via",      "#e2e8f0", "backside contact BM0 -> BPC1", 1),
    # ---- backside device tier (mirrored: MOL BELOW the diffusion) ----
    ("57/0",  "BPC1",          -150,  -76, "back",     "#dc2626", "BACKSIDE gate poly - the 2nd placement tier", 1),
    ("517/0", "BLISD1",        -150, -122, "back",     "#f59e0b", "backside local interconnect to source/drain, feeding DOWN to BCA1", 1),
    ("510/0", "GATE_CUT_BACK", -142, -120, "mask",     "#94a3b8", "backside gate cut", 0),
    ("588/0", "BSDT1",         -122,  -84, "back",     "#d97706", "backside source/drain trench (epi)", 1),
    ("511/1", "ACTIVE_BACK_P", -122,  -76, "back",     "#059669", "backside diffusion, PMOS band (upper Y). Empty when every device lands on the front tier.", 1),
    ("511/2", "ACTIVE_BACK_N", -122,  -76, "back",     "#10b981", "backside diffusion, NMOS band (lower Y). Empty when every device lands on the front tier.", 1),
    ("502/0", "FIN_BACK",      -118,  -76, "back",     "#0891b2", "backside fin grid (hardcoded 502/0 in the writer, not in the layer JSON)", 1),
    # ---- inter-tier ladder ----
    ("5000/0","MIV1",           -76,  -59, "miv",      "#f0abfc", "monolithic inter-tier via BPC1 -> H0", 1),
    ("700/0", "VL1 (virtual)",  -76,    0, "virtual",  "#facc15", "GRAPH-ONLY jump BPC1 <-> PC1 (method=overlap). No mask, no geometry - it exists only as an edge in the LayeredGridGraph, and the writer draws it only under --draw-virtual.", 0),
    ("512/0", "NSELECT_BACK",   -74,  -71, "implant",  "#a78bfa", "backside n-implant mask", 0),
    ("513/0", "PSELECT_BACK",   -74,  -71, "implant",  "#f472b6", "backside p-implant mask", 0),
    ("51/0",  "WELL_BACK",      -69,  -61, "substrate","#64748b", "backside well marker", 0),
    ("600/0", "H0",             -59,  -45, "mid",      "#2dd4bf", "inter-tier routing layer, horizontal, pitch 24", 1),
    ("5001/0","MIV2",           -45,  -31, "miv",      "#f0abfc", "MIV H0 -> H1", 1),
    ("601/0", "H1",             -31,  -17, "mid",      "#14b8a6", "inter-tier routing layer, vertical, pitch 42 offset 21", 1),
    ("5002/0","MIV3",           -17,    0, "miv",      "#f0abfc", "MIV H1 -> PC1 (climbs into the front tier)", 1),
    # ---- frontside device tier ----
    ("1/0",   "WELL_FRONT",     -15,   -7, "substrate","#64748b", "frontside well marker", 0),
    ("12/0",  "NSELECT_FRONT",   -5,   -2, "implant",  "#a78bfa", "frontside n-implant mask", 0),
    ("13/0",  "PSELECT_FRONT",   -5,   -2, "implant",  "#f472b6", "frontside p-implant mask", 0),
    ("2/0",   "FIN_FRONT",        0,   42, "device",   "#22d3ee", "frontside fin grid (hardcoded 2/0 in the writer)", 1),
    ("11/1",  "ACTIVE_FRONT_P",   0,   46, "device",   "#34d399", "frontside diffusion, PMOS band (upper Y)", 1),
    ("11/2",  "ACTIVE_FRONT_N",   0,   46, "device",   "#10b981", "frontside diffusion, NMOS band (lower Y)", 1),
    ("7/0",   "PC1",              0,   74, "device",   "#ef4444", "FRONTSIDE gate poly - the 1st placement tier. Runs up to the CA1 plane because QFET has no LIG layer.", 1),
    ("88/0",  "SDT1",             8,   46, "device",   "#f59e0b", "frontside source/drain trench (epi)", 1),
    ("10/0",  "GATE_CUT_FRONT",  44,   66, "mask",     "#94a3b8", "frontside gate cut", 0),
    ("17/0",  "LISD1",           46,   74, "mol",      "#fbbf24", "frontside local interconnect to source/drain, feeding UP to CA1", 1),
    # ---- frontside BEOL ----
    ("14/0",  "CA1",             74,   90, "via",      "#e2e8f0", "contact PC1 -> M0", 1),
    ("15/0",  "M0",              90,  104, "beol",     "#3b82f6", "M0, horizontal, pitch 24", 1),
    ("18/0",  "V0",             104,  118, "via",      "#e2e8f0", "via M0 -> M1", 1),
    ("19/0",  "M1",             118,  132, "beol",     "#8b5cf6", "M1, vertical, pitch 42 offset 21", 1),
]

# --------------------------------------------------------------------------
# Per-technology wiring. Adding an architecture means adding one entry here
# plus a stack table above -- build.py needs no changes.
#
#   stack        list of stack rows (above)
#   run          subdirectory name under data/solved/
#   preset       <engine>/input/presets/<preset>.mk
#   layer_json   <engine>/input/layer/<layer_json>
#   gds_writer   module under src.cellgen.postprocess
#   gds_argv     argv template for that writer; {res} {gds} {cell} {layer}
#                are substituted. Flag names differ between writers, which is
#                why each tech spells its own out.
#   arch/placement/pin_access/virtual
#                spec-strip text. These live in the tech classes as Python
#                defaults (archit/*/tech.py), not in any data file, so unlike
#                CPP / cell size / LGG order they cannot be read back.
#   boundary_key optional, default "100/0" -- the cell outline layer.
#   ignore_keys  optional -- keys with geometry that are deliberately NOT
#                drawn. Anything else with geometry and no stack row makes
#                build.py print a warning rather than silently vanish. `preset` and `layer_json` point into engine/input/,
# so build.py reads CPP / M1P / OF and the LGG layer order from the real files
# instead of repeating them here.
#
# `arch`, `placement` and `pin_access` ARE repeated here because they live in
# the tech classes as Python defaults (archit/*/tech.py), not in any data file.
# --------------------------------------------------------------------------
TECHS = {
    "FinFET": {
        "stack": FINFET,
        "run": "finfet",
        "preset": "FinFET_4T_SH",
        "layer_json": "PROBE3_FinFET_2F_4T_4530OF0.json",
        "gds_writer": "gds_FinFET_SH",
        "gds_argv": ["--result_file", "{res}", "--subckt_name", "{cell}",
                     "--layer", "{layer}", "--gds_file", "{gds}"],
        "arch": "單層平面 (1 tier)",
        "placement": "PC",
        "pin_access": "M0",
        "virtual": "無",
    },
    "CFET": {
        "stack": CFET,
        "run": "cfet",
        "preset": "CFET_4T_SH",
        "layer_json": "PROBE3_CFET_2F_4T_4530OF0.json",
        "gds_writer": "gds_CFET_SH",
        "gds_argv": ["--result_file", "{res}", "--subckt_name", "{cell}",
                     "--layer", "{layer}", "--gds_file", "{gds}"],
        # 11/0, 17/0 and 88/0 are the datatype-0 union copies of the per-tier
        # shapes already listed above; drawing them would double-render.
        "ignore_keys": ["11/0", "17/0", "88/0"],
        "arch": "垂直堆疊 (2 tier, P_on_N)",
        "placement": "PC (上) / BPC (下)",
        "pin_access": "BPC, M0",
        "virtual": "BPC↔M0 (boundary)",
    },
    "QFET": {
        "stack": QFET,
        "run": "qfet",
        "preset": "QFET_4T_SH",
        "layer_json": "PROBE3_QFET_2F_4T_4242OF21.json",
        "gds_writer": "gds_QFET_SH",
        "gds_argv": ["--result", "{res}", "--layer", "{layer}",
                     "--subckt", "{cell}", "--gds", "{gds}", "--draw-virtual"],
        "arch": "正面 + 背面雙面 (2 tier)",
        "placement": "PC1 (正面) / BPC1 (背面)",
        "pin_access": "M0 (正面) / BM0 (背面)",
        "virtual": "VL1: BPC1↔PC1 (overlap)",
    },
}

# Order the viewer's technology switcher uses.
TECH_ORDER = ["FinFET", "CFET", "QFET"]

# Layers that physically ENVELOPE other layers render semi-transparent, or
# they hide the very thing they exist to explain -- the gate stack runs the
# full cell height (through both CFET tiers), and diffusion wraps the fins.
# Keyed by the same gds_key as the stack tables above.
ALPHA = {
    "FinFET": {"7/0": 0.42, "11/0": 0.86, "10/0": 0.5},
    "CFET":   {"7/0": 0.30, "11/1": 0.88, "11/2": 0.88, "10/0": 0.5},
    "QFET":   {"7/0": 0.34, "57/0": 0.34,
               "11/1": 0.88, "11/2": 0.88, "511/1": 0.88, "511/2": 0.88,
               "10/0": 0.5, "510/0": 0.5, "700/0": 0.3},
}
