# Writing a constraint plugin (FinFET)

Generated from the engine source by `python -m src.cellgen.plugins.context_doc`. Do not edit by hand -- regenerate it instead, so it cannot drift from the code.

## The shape of a plugin

A plugin is one file. It registers a function that receives the solve
orchestrator (`inst`) and a parameter dict, and emits CP-SAT constraints
through `inst.opt`.

```python
from src.cellgen.plugins import constraint

@constraint(
    id="max_vias_per_col",        # unique; this is how the UI refers to it
    stage="post_routing",         # one of: pre_placement, post_placement, pre_routing, post_routing, pre_solve
    tech=['FinFET', 'CFET', 'QFET'],
    params={"max_vias": 2},       # defaults; a manifest may override them
    description="Cap vias sharing a column.",
)
def max_vias_per_col(inst, params):
    inst.opt.Add(sum(...) <= params["max_vias"])
```

Return value is ignored; a plugin mutates the model in place. It may also
create variables and hang them off `inst` for later constraints to use --
that is what the built-in `diffusion_alignment` does with `inst.db_vars`.

## Stages

| stage | fires |
|---|---|
| `pre_placement` | before any placement constraint |
| `post_placement` | after placement constraints and placement injection |
| `pre_routing` | before any routing constraint |
| `post_routing` | after every routing constraint |
| `pre_solve` | after injections and the top-layer cap, before the objective |

On QFET, `pre_routing`/`post_routing` do not fire when the cell config
selects a placement-only stage -- there would be no routing constraints
to build on.

## What is on `inst`

| attribute | meaning |
|---|---|
| `inst.opt` | the CP-SAT model (logging wrapper, see below) |
| `inst.lgg` | layered grid graph; nodes are `(layer_idx, row, col)` |
| `inst.circuit` | parsed netlist: transistors, nets, IO pins |
| `inst.tech` | technology object: pitches, offsets, layer names |
| `inst.cell_config` | the per-cell config JSON, as a dict |
| `inst.plc_ci` / `inst.plc_zi` | placeable column / tier indices |

### Variable containers (56 on FinFET)

Every one of these is created up front and filled in by a later init
step, so they always exist by the time a plugin runs.


**geometric primitives (overwritten by _init_graph)**

| attribute | key -> value |
|---|---|
| `inst.canvas_width` | (initialised to `0`) |
| `inst.canvas_height` | (initialised to `0`) |

**transistor metadata (populated by _init_tech)**

| attribute | key -> value |
|---|---|
| `inst.mos_to_num_finger` | mos_name -> num_finger |
| `inst.nmos_placeable_row_indices` | (initialised to `[]`) |
| `inst.pmos_placeable_row_indices` | (initialised to `[]`) |
| `inst.nmos_pin_access_ri` | (initialised to `[]`) |
| `inst.pmos_pin_access_ri` | (initialised to `[]`) |

**transistor / net top-level maps**

| attribute | key -> value |
|---|---|
| `inst.transistor_vars` | transistor name -> TransistorVar |
| `inst.net_vars` | net name -> NetVar |

**transistor placement vars (populated by _init_transistor_vars)**

| attribute | key -> value |
|---|---|
| `inst.placed_tran_ci_vars` | (tran_name, ci) -> bool var |
| `inst.placed_tran_zi_vars` | (tran_name, zi) -> bool var |
| `inst.placed_tran_at_xzi_vars` | (tran_name, ci, zi) -> bool var (= ci AND zi) |
| `inst.has_tran_at_ci_vars` | ci -> bool var |
| `inst.has_tran_at_zi_vars` | zi -> bool var |
| `inst.has_tran_at_xzi_vars` | (ci, zi) -> bool var (OR over transistors at slot) |

**diffusion break vars (populated by _init_diffusion_break_vars)**

| attribute | key -> value |
|---|---|
| `inst.db_pmos_vars` | (ci, zi) -> bool var: PMOS DB at slot |
| `inst.db_nmos_vars` | (ci, zi) -> bool var: NMOS DB at slot |
| `inst.db_vars` | (ci, zi) -> bool var (set by plc.diffusion_alignment) |
| `inst.db_pmos_cols_vars` | ci -> bool var: PMOS DBs at all tiers of col |
| `inst.db_nmos_cols_vars` | ci -> bool var: NMOS DBs at all tiers of col |

**net source / terminal Super Inner Nodes**

| attribute | key -> value |
|---|---|
| `inst.node_is_src_vars` | (net) -> (layer, row, col) -> bool var |
| `inst.node_is_term_vars` | (net) -> k -> (layer, row, col) -> bool var |

**net-level vars**

| attribute | key -> value |
|---|---|
| `inst.num_pins_for_io` | (initialised to `0`) |
| `inst.net_flow_vars` | (initialised to `{}`) |
| `inst.net_to_flow_cnt` | net -> flow count |
| `inst._int_flow_nets` | net_name -> total_k (only int-flow nets) |
| `inst.net_arc_vars` | (initialised to `{}`) |
| `inst.edge_vars` | (initialised to `{}`) |
| `inst.edge_to_cost` | used by objective |

**Super Outer Nodes (I/O pins)**

| attribute | key -> value |
|---|---|
| `inst.son_terminal_nodes` | (initialised to `{}`) |
| `inst.node_is_SON_vars` | (initialised to `{}`) |
| `inst.node_to_net_SON_vars` | (layer, row, col) -> (net) -> bool var |

**----- routing state -------------------------------------------- # Populated by the rt.X(self) free-function calls in _routing_constraints; pre-allocated here so attribute provenance stays grep-able**

| attribute | key -> value |
|---|---|
| `inst.gate_share_at_col_vars` | zi -> OrderedDict[col -> gate_share BoolVar] |
| `inst.gate_cut_window_vars` | zi -> {col_tuple -> gate_cut_window BoolVar} |

**LISD sharing (per-tier nested column map; same nesting as gate sharing)**

| attribute | key -> value |
|---|---|
| `inst.lisd_share_at_col_vars` | zi -> OrderedDict[col -> lisd_share BoolVar] |

**routing-window coords + bbox (per net.name)**

| attribute | key -> value |
|---|---|
| `inst.s_coord_x` | net.name -> IntVar |
| `inst.s_coord_y` | net.name -> IntVar |
| `inst.t_coord_x` | net.name -> [IntVar, ...] |
| `inst.t_coord_y` | net.name -> [IntVar, ...] |
| `inst.net_min_x` | net.name -> IntVar |
| `inst.net_max_x` | net.name -> IntVar |
| `inst.net_min_y` | net.name -> IntVar |
| `inst.net_max_y` | net.name -> IntVar |
| `inst.window_xmin_raw` | net.name -> IntVar |
| `inst.window_xmax_raw` | net.name -> IntVar |

**Per-tier y-window (single-tier for FinFET; shared routing_localization fans these out per placement tier)**

| attribute | key -> value |
|---|---|
| `inst.window_ymin_tier` | net.name -> ti -> IntVar |
| `inst.window_ymax_tier` | net.name -> ti -> IntVar |
| `inst.has_pins_on_tier` | net.name -> ti -> BoolVar |
| `inst.net_min_y_tier` | net.name -> ti -> IntVar |
| `inst.net_max_y_tier` | net.name -> ti -> IntVar |

**design-rule scratch**

| attribute | key -> value |
|---|---|
| `inst.geometric_vars` | node -> {left, right, front, back} |

**per-layer usage (diagnostic; feed the m2/m1/m0_usage objectives only)**

| attribute | key -> value |
|---|---|
| `inst.m2_rows_to_used` | row -> BoolVar (top-side M2) |
| `inst.m1_cols_to_used` | col -> BoolVar (M1) |
| `inst.m0_rows_to_used` | row -> BoolVar (M0) |

**pin / top-layer state**

| attribute | key -> value |
|---|---|
| `inst.net_use_top_track` | netname -> BoolVar |
| `inst.net_use_top_track_row_var` | netname -> {row -> BoolVar} |

### `inst.lgg` — the grid graph

| method | purpose |
|---|---|
| `lgg.arcs()` | Return the list of arcs (bidirectional edges) in the graph. |
| `lgg.back_row_in_layer(layer, row, check_site=False)` | Return the back row coordinate in the given layer. |
| `lgg.col_in_layer(layer, idx)` | Return the col-coordinate at position `idx` in the given layer. |
| `lgg.col_index_in_layer(layer, col)` | Return the index of the given column in the given layer. |
| `lgg.col_indices_in_layer(layer, parity=None)` | Return the list of column indices in the given layer. |
| `lgg.cols_in_layer(layer, parity=None)` | Return the list of column coordinates in the given layer. |
| `lgg.cols_in_layer_from(layer, col)` | Return the list of cols in the given layer starting at the specified col. |
| `lgg.draw_layered_grid_2d(G, node_size=40, edge_color='gray', node_edge_color='k', figsize_per_layer=(4, 4), outdir='')` | Draw every layer of G in its own 2D subplot, arranged side by side. |
| `lgg.draw_layered_grid_3d(G, elev=30, azim=45, node_size=40, intra_edge_alpha=0.6, inter_edge_alpha=0.3, intra_color='gray', inter_color='black')` | Draws a 3D networkx.Graph G with transparent background |
| `lgg.draw_one_layer_grid_2d(G, layer, node_size=40, edge_color='gray', node_edge_color='k', outdir='')` | Draw a single layer of G in 2D. |
| `lgg.edges()` | Return the list of edges in the graph. |
| `lgg.front_row_in_layer(layer, row, check_site=False)` | Return the front row coordinate in the given layer. |
| `lgg.get_back_neighbor(node, check_site=False)` |  |
| `lgg.get_front_neighbor(node, check_site=False)` |  |
| `lgg.get_left_neighbor(node)` |  |
| `lgg.get_right_neighbor(node)` |  |
| `lgg.is_edge_cross_site(node_1, node_2)` | Return True if an edge between node_1 and node_2 crosses the site division row. |
| `lgg.is_even_col(layer, col)` | Return True if the column is at an even index in the given layer (i.e. a gate col). |
| `lgg.is_node_in_graph(node)` | Check if the node (z, row, col) is in the graph. |
| `lgg.is_odd_col(layer, col)` | Return True if the column is at an odd index in the given layer (i.e. a source/drain col). |
| `lgg.is_place_layer(layer)` | Return True if the layer is classified as a placement layer. |
| `lgg.is_route_layer(layer)` | Return True if the layer is classified as a routing layer. |
| `lgg.layer_index(layer)` | Return the layer index (z) for the given layer (int index or str name). |
| `lgg.layer_kind(layer)` | Return the kind ("PLACE" / "ROUTE") of the given layer, or None if unclassified. |
| `lgg.layers_of_kind(kind)` | Return the list of layer names with the given kind, ordered by layer index (z). |
| `lgg.left_col_in_layer(layer, col)` | Return the left column coordinate in the given layer. |
| `lgg.max_col_in_layer(layer)` | Return the maximum column coordinate in the given layer. |
| `lgg.max_row_in_layer(layer)` | Return the maximum row coordinate in the given layer. |
| `lgg.nearest_node_in_layer(layer, row, col)` | Return the node in the given layer closest to (row, col) by Euclidean distance. |
| `lgg.node_at(layer, row, col)` | Return the node (z, row, col) in the given layer. |
| `lgg.nodes()` | Return the list of nodes in the graph. |
| `lgg.nodes_in_layer(layer, parity=None)` | Iterate over all nodes (z, row, col) in the given layer, |
| `lgg.num_cols_in_layer(layer)` | Return the number of columns in the given layer. |
| `lgg.num_rows_in_layer(layer)` | Return the number of rows in the given layer. |
| `lgg.right_col_in_layer(layer, col)` | Return the right column coordinate in the given layer. |
| `lgg.row_in_layer(layer, idx)` | Return the row-coordinate at position `idx` in the given layer. |
| `lgg.row_indices_in_layer(layer, parity=None)` | Return the list of row indices in the given layer. |
| `lgg.rows_in_layer(layer, parity=None)` | Return the list of row coordinates in the given layer. |
| `lgg.rows_in_layer_from(layer, row)` | Return the list of rows in the given layer starting at the specified row. |
| `lgg.stats()` | Print the number of nodes and edges in the graph. |

### `inst.opt` — CP-SAT calls available

A `cp_model.CpModel` subclass that mirrors every call into a readable
constraint log, which is how the UI shows what a plugin actually added.

| method | purpose |
|---|---|
| `opt.log_comment(comment: str)` |  |
| `opt.flush()` | Manually flush buffered logs. Prefer `with` for automatic flushing. |
| `opt.Proto()` |  |
| `opt.NewIntVar(lb: int, ub: int, name: str) -> IntVar` |  |
| `opt.NewBoolVar(name: str) -> IntVar` |  |
| `opt.NewIntervalVar(start: LinearExpr, size: LinearExpr, end: LinearExpr, name: str) -> cp_model.IntervalVar` |  |
| `opt.NewIntVarFromDomain(domain: cp_model.Domain, name: str) -> IntVar` |  |
| `opt.NewConstant(value: int) -> IntVar` |  |
| `opt.Add(ct)` |  |
| `opt.AddAllDifferent(variables: list[IntVar])` |  |
| `opt.AddImplication(b1: LiteralT, b2: LiteralT)` |  |
| `opt.AddAtMostOne(literals: list[LiteralT])` |  |
| `opt.AddExactlyOne(literals: list[LiteralT])` |  |
| `opt.AddBoolOr(literals: list[LiteralT])` |  |
| `opt.AddBoolAnd(literals: list[LiteralT])` |  |
| `opt.AddMaxEquality(max_var: IntVar, exprs: list[LinearExpr])` |  |
| `opt.AddMinEquality(min_var: IntVar, exprs: list[LinearExpr])` |  |
| `opt.AddLinearConstraint(expr: LinearExpr, lb: int, ub: int)` |  |
| `opt.AddNoOverlap(interval_vars: list[cp_model.IntervalVar])` |  |
| `opt.AddCircuit(arcs: list[tuple[int, int, LiteralT]])` |  |
| `opt.AddCumulative(intervals, demands, capacity)` |  |
| `opt.AddMultiplicationEquality(target: IntVar, factors: list[LinearExpr])` |  |
| `opt.AddDecisionStrategy(variables, var_strategy: int, domain_strategy: int)` |  |
| `opt.Minimize(expr: LinearExpr)` |  |
| `opt.Maximize(expr: LinearExpr)` |  |
| `opt.AddHint(var: IntVar, value: int)` |  |

## Worked examples: the built-in constraints

These are the existing rules, written against the same `inst` object.
Read one before writing a new plugin -- the idioms (reified booleans,
`OnlyEnforceIf` indicators, iterating `lgg.edges()`) carry straight over.

### `src/cellgen/core/placement.py`

- `link_source_drain_gate_columns_to_transistor_placement` — Link source / drain / gate column vars to transistor (col, tier) placement.
- `diffusion_alignment` — Enforce per-tier diffusion alignment between PMOS and NMOS.
- `limit_diffusion_breaks` — Set allowable diffusion break columns.
- `placement_lexico_order_symmetry_breaking` — Break left-right reflection symmetry across all placement tiers.
- `placement_site_flip_symmetry_breaking` — Per-transistor flip symmetry break (QFET SH analog of ?FET's site-flip).
- `pairwise_diffusion_sharing` — Per-tier pairwise diffusion sharing.
- `pairwise_lisd_sharing` — Per-tier pairwise LISD sharing via biconditional reification.
- `pairwise_gate_sharing` — Per-tier pairwise gate sharing.
- `net_span_from_placement` — Enforce net spanning from placement.
- `ban_other_nets_from_using_nodes` — Ban other nets from using specified nodes.
- `ban_other_nets_on_pwr_columns` — Per-tier: ban any via on placement-tier slot (ci, zi) at the col where a
- `prohibit_CA_contact_on_non_source_term_columns` — Per-tier: at every S/D col, a CA contact (via) is only valid if at least

### `src/cellgen/core/routing.py`

- `prohibit_routing_to_left_cell_boundaries` — Ban every edge that touches the left cell boundary (col == 0).
- `prohibit_routing_to_right_cell_boundaries` — Ban every edge whose col exceeds the cell's right boundary at the chosen
- `bind_gate_sharing_to_columns` — Per-tier gate sharing reification.
- `gate_cut_window` — Per-tier sliding-window reification of "gate is cut over X consecutive cols".
- `prohibit_pc_routing_in_diffusion_break_cols` — Per-tier PC-routing prohibition at diffusion-break columns.
- `enforce_CA_pickup_for_gate_cut` — Per-tier CA-pickup enforcement at gate-cut columns.
- `limit_gate_contact` — Per-tier cap on the number of CA-style gate-contact vias at each gate col.
- `bind_lisd_sharing_to_columns` — Per-tier LISD sharing reification.
- `limit_lisd_contact` — Per-tier cap on the number of CA-style LISD-contact vias at each S/D col.
- `ban_middle_row_via_for_3T` — [3-Track SH only] Restrict PC-to-M0 via usage at the middle row based
- `link_flow_to_arc` — Biconditional link: per-net flow on (u,v) <=> the arc is active.
- `link_arc_to_edge` — Couple per-net arcs to the undirected `edge_vars`:
- `prohibit_virtual_edge_shorting` — At every (row, col) where a virtual jump (VL) lands, allow at most
- `net_has_one_src_and_k_terminals` — Per net: exactly one source node and exactly one k-th terminal node.
- `net_src_node_uniqueness` — Per node: at most one net can claim this node as its source.
- `net_term_node_uniqueness` — Per node: at most one (net, terminal-k) pair can claim this node.
- `net_SON_node_uniqueness` — Per SON node: at most one (io-net, terminal-k) pair can claim it.
- `prohibit_multiple_SONs_same_column` — Per pin track on every pin-access layer: at most one SON can land on it.
- `induce_internal_routing_flow_with_diffusion` — Per-terminal directed flow conservation with diffusion / LISD / gate
- `induce_external_routing_flow` — Per-IO-net flow conservation routing each k-th SON terminal (k beyond the
- `tree_enforcement` — Enforce tree structure on per-net arc usage: each non-source node
- `node_exclusivity` — Per LGG node: at most one net may "touch" it via arc usage.
- `routing_localization` — Per-tier routing-localization (generic N-tier).
- `routing_localization_cfet` — Enforce routing localization constraints for CFET.
- `cfet_cross_device_via_lower_bound` — For each flow (net, k) where the source and the k-th terminal are on
- `cfet_hpwl_via_cost_tightening` — Tighten the HPWL lower bound by adding mandatory via costs for cross-device

### `src/cellgen/core/rule.py`

- `eol_rules_in_horizontal_layers` — Enforce EOL (End-of-Line) design rule checking for horizontal layers.
- `eol_rules_in_vertical_layers` — Enforce EOL (End-of-Line) design rule checking for vertical layers.
- `mar_rules_in_horizontal_layers` — Enforce MAR (Minimum Area Rule) design rule checking for horizontal layers.
- `mar_rules_in_vertical_layers` — Enforce MAR (Minimum Area Rule) design rule checking for vertical layers.
- `via_induce_vertical_metal` — For every V-direction NON-placement layer: an active via at a node on
- `via_induce_horizontal_metal` — For every H-direction NON-placement layer: an active via at a node on
- `geometric_vars_in_horizontal_layers` — Create geometric variables for horizontal layers to track wire segment boundaries.
- `geometric_vars_in_vertical_layers` — Create geometric variables for vertical layers to track wire segment boundaries.
- `vertical_metal_must_be_connected_to_via` — For each vertical layer, if a node is connected to a vertical metal edge,
- `horizontal_metal_must_be_connected_to_via` — For each horizontal layer, if a node is connected to a horizontal metal edge,
- `metal_endpoint_must_have_via` — Strengthen metal-via connectivity: if ANY metal edge is active at a node
- `via_separation_rules` — Enforce via separation rules to ensure vias maintain minimum L1 (Manhattan) distance.

### `src/cellgen/core/pin.py`

- `m1_minimum_pin_opening` — Enforce minimum pin opening for M1 pins.
- `pin_separation_by_minimum_gap` — Separate SON terminals by enforcing a minimum gap between pins.
- `pin_separation_by_partition` — Separate SON terminals into partitions based on mode.
- `top_layer_net_usage` — Bind net usage on top layer.
- `one_top_layer_track_per_net` — Enforce that each net uses one top layer track at most.
- `one_net_per_top_layer_track` — Enforce that each top layer track can be used by one net at most.
- `m0_pin` — Enforce SON entry point for M0 pins.
- `m0_pin_separation` — Enforce that no two IO nets can have M0 routing on the same M0 row.
- `m0_pin_extension` — Ensure that M0 pins have vacant edges at their end-of-line positions.

## Verifying a plugin

```bash
# fast: INV_X1 on FinFET solves in well under a second
python -m src.cellgen.run --preset FinFET_4T_SH --cell INV_X1 \
    --output-dir /tmp/check --plugin-dir plugins

# the engine's own safety net
python -m pytest tests/ -q
```

The run prints how many constraints and variables each plugin added.
Zero usually means the plugin matched nothing and is silently inert.

Compare runs by **objective value**, never by layout geometry: repeated
identical solves agree on cost but can return different equal-cost
layouts. See `docs/solve-reproducibility.md`.
