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
# QFET -- frontside tier above, backside tier below, with an H0/H1 + MIV
# ladder between them. Negative z is the backside. Ordering here mirrors the
# layer_number ordering in PROBE3_QFET_2F_4T_4242OF21.json exactly:
#   BM1 < BV0 < BM0 < BCA1 < BPC1 < MIV1 < H0 < MIV2 < H1 < MIV3 < PC1 < CA1
#   < M0 < V0 < M1
# --------------------------------------------------------------------------
QFET = [
    ("519/0", "BM1",         -250, -236, "backbeol", "#a855f7", "backside M1, vertical, pitch 42 offset 21", 1),
    ("518/0", "BV0",         -236, -222, "via",      "#e2e8f0", "backside via BM1 -> BM0", 1),
    ("515/0", "BM0",         -222, -208, "backbeol", "#60a5fa", "backside M0, horizontal - carries the backside power rails", 1),
    ("514/0", "BCA1",        -208, -192, "via",      "#e2e8f0", "backside contact BM0 -> BPC1", 1),
    ("51/0",  "WELL_BACK",   -200, -176, "substrate","#64748b", "backside well marker", 0),
    ("512/0", "NSELECT_BACK",-180, -176, "implant",  "#a78bfa", "backside n-implant", 0),
    ("513/0", "PSELECT_BACK",-180, -176, "implant",  "#f472b6", "backside p-implant", 0),
    ("502/0", "FIN_BACK",    -172, -130, "back",     "#0891b2", "backside-tier fin grid", 1),
    ("511/1", "ACTIVE_BACK_P",-172,-126, "back",     "#059669", "backside PMOS diffusion (empty in this cell)", 1),
    ("511/2", "ACTIVE_BACK_N",-172,-126, "back",     "#10b981", "backside NMOS diffusion (empty in this cell)", 1),
    ("588/0", "BSDT1",       -164, -126, "back",     "#d97706", "backside source/drain trench", 1),
    ("57/0",  "BPC1",        -172, -112, "back",     "#dc2626", "BACKSIDE gate poly - the 2nd placement tier", 1),
    ("510/0", "GATE_CUT_BACK",-134,-112, "mask",     "#94a3b8", "backside gate cut", 0),
    ("517/0", "BLISD1",      -126, -106, "back",     "#f59e0b", "backside local interconnect", 1),
    ("5000/0","MIV1",        -106,  -90, "miv",      "#f0abfc", "monolithic inter-tier via BPC1 -> H0", 1),
    ("600/0", "H0",           -90,  -76, "mid",      "#2dd4bf", "inter-tier routing layer, horizontal, pitch 24", 1),
    ("5001/0","MIV2",         -76,  -62, "miv",      "#f0abfc", "MIV H0 -> H1", 1),
    ("601/0", "H1",           -62,  -48, "mid",      "#14b8a6", "inter-tier routing layer, vertical, pitch 42 offset 21", 1),
    ("5002/0","MIV3",         -48,   -4, "miv",      "#f0abfc", "MIV H1 -> PC1 (climbs into the front tier)", 1),
    ("700/0", "VL1 (virtual)",-112,   0, "virtual",  "#facc15", "GRAPH-ONLY jump BPC1 <-> PC1. No mask, no geometry - it only exists as an edge in the LayeredGridGraph.", 0),
    ("1/0",   "WELL_FRONT",   -28,   -4, "substrate","#64748b", "frontside well marker", 0),
    ("12/0",  "NSELECT_FRONT", -6,   -2, "implant",  "#a78bfa", "frontside n-implant", 0),
    ("13/0",  "PSELECT_FRONT", -6,   -2, "implant",  "#f472b6", "frontside p-implant", 0),
    ("2/0",   "FIN_FRONT",      0,   42, "device",   "#22d3ee", "frontside fin grid", 1),
    ("11/1",  "ACTIVE_FRONT_P", 0,   46, "device",   "#34d399", "frontside PMOS diffusion", 1),
    ("11/2",  "ACTIVE_FRONT_N", 0,   46, "device",   "#10b981", "frontside NMOS diffusion", 1),
    ("88/0",  "SDT1",           8,   46, "device",   "#f59e0b", "frontside source/drain trench", 1),
    ("7/0",   "PC1",            0,   62, "device",   "#ef4444", "FRONTSIDE gate poly - the 1st placement tier", 1),
    ("10/0",  "GATE_CUT_FRONT",44,   66, "mask",     "#94a3b8", "frontside gate cut", 0),
    ("17/0",  "LISD1",         46,   74, "mol",      "#fbbf24", "frontside local interconnect", 1),
    ("14/0",  "CA1",           74,   90, "via",      "#e2e8f0", "contact PC1 -> M0", 1),
    ("15/0",  "M0",            90,  104, "beol",     "#3b82f6", "M0, horizontal, pitch 24", 1),
    ("18/0",  "V0",           104,  118, "via",      "#e2e8f0", "via M0 -> M1", 1),
    ("19/0",  "M1",           118,  132, "beol",     "#8b5cf6", "M1, vertical, pitch 42 offset 21", 1),
]

# --------------------------------------------------------------------------
# Per-technology wiring. `preset` and `layer_json` point into engine/input/,
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
        "arch": "正面 + 背面 (2 tier)",
        "placement": "PC1 (正面) / BPC1 (背面)",
        "pin_access": "BM0, M0",
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
    "QFET":   {"7/0": 0.42, "57/0": 0.42, "11/1": 0.88, "11/2": 0.88,
               "10/0": 0.5, "510/0": 0.5, "700/0": 0.45},
}
