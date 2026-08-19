"""Pluggable constraints for the SMTCell solve.

Adding a constraint used to mean editing ``src/cellgen/archit/<TECH>/main.py``,
which made a new rule impossible to save, share, toggle or compare on its own.
A plugin is instead a standalone file that registers itself::

    from src.cellgen.plugins import constraint

    @constraint(id="my_rule", stage="post_routing", tech=["FinFET"])
    def my_rule(inst, params):
        inst.opt.Add(...)

The orchestrators call :func:`run_stage` at five fixed points, so nothing in
the existing constraint stack has to move for a plugin to take effect.
"""

from src.cellgen.plugins.hooks import (  # noqa: F401
    PluginRunRecord,
    active,
    records,
    run_stage,
    set_active,
)
from src.cellgen.plugins.loader import (  # noqa: F401
    PluginLoadError,
    PluginSelection,
    disabled_builtins,
    load,
    load_plugin_dir,
    load_plugin_file,
    read_manifest,
    resolve_selection,
)
from src.cellgen.plugins.builtins import (  # noqa: F401
    apply,
    builtin_id,
    catalogue,
    is_enabled,
    set_disabled,
)
from src.cellgen.plugins.registry import (  # noqa: F401
    STAGES,
    TECHNOLOGIES,
    ConstraintPlugin,
    all_constraints,
    clear,
    constraint,
    get,
    iter_constraints,
)
