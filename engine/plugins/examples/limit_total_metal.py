"""Cap the total number of routing edges the solver may use.

A deliberately blunt example, useful because one parameter spans the whole
range of outcomes:

* a large ``max_edges`` is slack, so the result matches an unconstrained run;
* a small one is binding and the cell becomes INFEASIBLE.

That makes it the natural fixture for testing that a plugin can change the
model, and that an unsatisfiable plugin surfaces as a contained failure rather
than taking the process down.

``inst.edge_vars`` maps ``(u, v) -> BoolVar`` for every edge on the layered grid
graph, where a node is ``(layer_idx, row, col)``. Summing them counts the routing
segments in the solution.
"""

from loguru import logger

from src.cellgen.plugins import constraint


@constraint(
    id="limit_total_metal",
    stage="post_routing",
    tech=["FinFET", "CFET", "QFET"],
    params={"max_edges": 10_000},
    description="Cap the total number of active routing edges.",
)
def limit_total_metal(inst, params):
    max_edges = params["max_edges"]
    edges = list(inst.edge_vars.values())
    if not edges:
        logger.warning("limit_total_metal: no edge variables; nothing to constrain")
        return
    logger.info(f"limit_total_metal: {len(edges)} edge vars, cap = {max_edges}")
    inst.opt.Add(sum(edges) <= max_edges)
