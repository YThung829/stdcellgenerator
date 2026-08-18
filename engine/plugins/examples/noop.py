"""A constraint plugin that adds nothing.

Its only job is to prove the plugin machinery is transparent: loading it must
leave the solved result byte-identical to a run with no plugins at all. If this
example ever changes a result, the hook wiring has a bug.
"""

from src.cellgen.plugins import constraint


@constraint(
    id="noop",
    stage="post_routing",
    tech=["FinFET", "CFET", "QFET"],
    description="Adds no constraints; used to verify the plugin layer is inert.",
)
def noop(inst, params):
    inst.opt.log_comment("noop plugin: intentionally adds nothing")
