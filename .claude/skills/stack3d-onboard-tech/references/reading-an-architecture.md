# Reading an architecture in this engine

Everything the stack3d viewer needs is spread across five files. This is what
each one contributes and where the traps are.

## Contents

- [The five files](#the-five-files)
- [The layer JSON is the backbone](#the-layer-json-is-the-backbone)
- [tech.py: which layers hold devices](#techpy-which-layers-hold-devices)
- [_init_graph: the routing space](#_init_graph-the-routing-space)
- [The GDS writer: where shapes land](#the-gds-writer-where-shapes-land)
- [The .res file in between](#the-res-file-in-between)
- [Traps that have actually bitten](#traps-that-have-actually-bitten)

## The five files

| File | Contributes |
|---|---|
| `input/layer/<STACK>.json` | every layer, its plane geometry, its GDS number, the z **order** |
| `archit/<NAME>/tech.py` | which layers are placement tiers vs pin access; stacking config |
| `archit/<NAME>/main.py` → `_init_graph()` | how the routing graph is built; hardcoded virtual edges |
| `postprocess/gds_<NAME>_SH.py` | which GDS layer/datatype each drawn shape lands on |
| `input/presets/<NAME>_*.mk` | CPP / M1P / offset / track count / cell list |

## The layer JSON is the backbone

`LayerStack.__init__` (`core/entity.py`) splits entries by `layer_type`:

- **`metal`** — enters the graph. Sorted by `layer_number`; **the resulting
  index is the LGG z-index**, and it is also the `MET` column in a `.res`
  routing table. This sort is the single most useful fact for building a stack
  table: it is the process order, already computed for you.
- **`via`** — never a graph node. Declares `lower_layer` / `upper_layer`,
  which is what makes those two metals z-adjacent. A via whose two metals are
  more than one z step apart is a red flag worth asking about.
- **`virtual`** — a graph-only shortcut with no physical geometry. It has a
  `gds_layer` purely so a debug overlay can be drawn. Do not give it a real
  slab in the stack; render it spanning the two layers it joins, default off.
- **`gds`** — the solver never sees these (active, select, well, fin,
  boundary). They exist only for the writers, and they are most of what makes
  the picture legible.

## tech.py: which layers hold devices

Read these as constructor defaults, not data:

- `placement_layer_names` — the tiers a transistor can sit on. One name means
  planar; two or more means stacked, and the count is the headline fact about
  the architecture.
- `pin_access_layer_names` — where pins can be reached.
- `default_placement_layer` — the canonical tier for column/pitch queries.
- `stacking_config` (CFET-style) — `P_on_N` / `N_on_P`, i.e. which device is
  on top. This decides which datatype is the upper tier in your stack table.

These live in Python, not in any data file, so they cannot be read back the
way CPP or cell size can. `inspect_tech.py` greps them; if it reports
`(not found)` for something you expect, open the file.

## `_init_graph`: the routing space

Not needed for the stack table, but it is where the architecture's real
character shows, and reviewers ask about it.

- **Canvas**: `width = num_col × placement pitch`,
  `height = num_rt_track × M0 pitch × 2`.
- **Doubled-resolution coordinates**: gates sit on integer columns and
  source/drain on the half-grid between them. To keep both integral the model
  doubles the coordinate space — placement layers keep their native pitch,
  every other layer uses `pitch × 2`. Downstream divides by 2, which is the
  `SOLVER_RESCALE = 2.0` at the top of each writer. Consequence: **even column
  index = gate, odd = source/drain**.
- **Edges**: H layers connect left/right neighbours, V layers up/down; a
  cross-layer via edge exists only where *the same `(row, col)` exists on both
  layers*. Pitch alignment is therefore what decides whether two layers can be
  connected at all.
- **Virtual edges** come either from the layer JSON's `virtual` entries or
  hardcoded in `_init_graph` — grep `virtual_connect_pairs` to be sure.
  Methods: `overlap`, `colwise`, `sdcolwise`, `overlapGate`, `boundary`.

## The GDS writer: where shapes land

Two styles exist in this repo, and which one an architecture uses changes how
you find its GDS numbers:

- **Hardcoded** (FinFET, CFET): `draw()` is a wall of
  `self.x_layer_idx = self.layout.layer(15, 0)`. The numbers are in the code.
- **JSON-driven** (QFET): numbers come from each layer's `gds_layer` /
  `gds_datatype`, with a `GDS_KEYS` tuple naming the GDS-only entries the
  writer requires. Changing the PDK is a JSON edit.

Either way, the reliable source of truth is what the writer *actually emitted*
— run `inspect_tech.py --gds` on a generated file. Code inspection alone
misses conditional paths.

Geometry is anchored by a few constants shared across writers:

```
SOLVER_RESCALE = 2.0    doubled-resolution coords / 2 = physical nm
_ROUTE_X_OFFSET = -7    wires and vias anchor at the landing via's lower-left
_ROUTE_Y_OFFSET = +29
_VIA_SIZE = 14          every cross-layer landing is a 14×14 contact box
```

## The .res file in between

The solver does not emit GDS. It writes a plain-text `.res` (a placement table
plus a merged routing-segment table) which the writer parses. Its header
differs per architecture in a way that mirrors the architecture itself:

```
FinFET  Name  X  Y      Flip  Width Height  SrcCol ...
CFET    Name  X  Y      Flip  Width Height  SrcRow SrcCol ...
QFET    Name  X  Y  Z   Flip  Width Height  SrcCol ...
```

A `Z` column carries the placement tier's layer name directly. If the
architecture you are onboarding has one, it tells you which tier each
transistor actually landed on — worth checking, because a small cell may leave
a tier entirely empty and that is a real result, not a bug.

## Traps that have actually bitten

**Canvas width from `metal_layers[0]`.** FinFET and CFET use
`metal_layers[0].pitch` because their lowest metal happens to be the placement
layer. QFET's lowest metal is `BM1` (backside), so copying that line makes the
canvas 2/3 too narrow and drops the last valid column. QFET asks
`get_pitch(default_placement_layer)` instead. Any architecture with backside
metal below the placement tier has this hazard.

**Datatype-0 union copies.** A writer may emit both per-tier shapes
(`11/1`, `11/2`) *and* a datatype-0 union (`11/0`) covering the same area.
Drawing all three double-renders the tiers. List the unions in `ignore_keys`.

**Per-writer SCALE and dbu.** `gds_FinFET_SH` uses `SCALE 4 / dbu 0.00025`;
`gds_CFET_SH` uses `SCALE 10 / dbu 0.0001`. They multiply to the same
0.001 µm per unit, so normalising through `dbu × 1000` (what `dump_gds.py`
does) puts every architecture in one nm space. A new writer with a different
pair still works — but check it, because a mismatch silently scales one
architecture relative to the others.

**Text-only layers.** `15/251`-style layers carry net labels, not mask
geometry. They have texts and no polygons, so the orphan warning ignores them.
Do not give them stack rows.

**An empty tier is not a bug.** If a small cell puts every device on one tier,
the other tier's layers legitimately have no geometry. Keep the rows so the
rail shows them greyed, and say so in the write-up.
