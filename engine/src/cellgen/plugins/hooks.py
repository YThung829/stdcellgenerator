"""Run registered constraint plugins at their declared stage.

The orchestrators call :func:`run_stage` at five fixed points in the constraint
build. Each plugin's emissions are bracketed with ``opt.log_comment`` markers
and its net contribution to the model is measured, so the constraint log shows
exactly what a plugin added -- which is what the web UI reports back to whoever
asked for it in natural language.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from src.cellgen.plugins import registry
from src.cellgen.plugins.loader import PluginSelection

# Selections resolved for the current process, set once by the run driver.
# Module-level because the orchestrators construct themselves and there is no
# other channel to reach them without changing every signature.
_ACTIVE: list[PluginSelection] = []


@dataclass
class PluginRunRecord:
    """What one plugin did to the model, for reporting back to the UI."""

    id: str
    stage: str
    params: dict
    constraints_added: int
    variables_added: int


_RECORDS: list[PluginRunRecord] = []


def set_active(selections: list[PluginSelection]) -> None:
    """Install the plugin selections that :func:`run_stage` will execute."""
    global _ACTIVE
    _ACTIVE = list(selections)
    _RECORDS.clear()
    if _ACTIVE:
        logger.info(
            f"Constraint plugins active: "
            f"{[(s.plugin.id, s.plugin.stage) for s in _ACTIVE]}"
        )


def active() -> list[PluginSelection]:
    return list(_ACTIVE)


def records() -> list[PluginRunRecord]:
    """Per-plugin model deltas from the most recent build."""
    return list(_RECORDS)


def run_stage(inst, stage: str) -> None:
    """Run every active plugin registered for ``stage`` and this technology.

    Safe to call when nothing is loaded -- the common case -- in which case it
    returns immediately without touching the model.
    """
    if not _ACTIVE:
        return
    if stage not in registry.STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid: {list(registry.STAGES)}")

    tech_name = inst.tech.TECHNOLOGY
    for selection in _ACTIVE:
        plugin = selection.plugin
        if plugin.stage != stage:
            continue
        if not plugin.applies_to(tech_name):
            logger.debug(
                f"[plugin:{plugin.id}] skipped: declares {list(plugin.tech)}, "
                f"this run is {tech_name}"
            )
            continue

        proto = inst.opt.Proto()
        n_ct, n_var = len(proto.constraints), len(proto.variables)

        inst.opt.log_comment(f"[plugin:{plugin.id}] begin ({stage})")
        logger.info(f"\t==\t[plugin:{plugin.id}] {plugin.description or stage}")
        plugin.fn(inst, selection.params)
        inst.opt.log_comment(f"[plugin:{plugin.id}] end")

        proto = inst.opt.Proto()
        record = PluginRunRecord(
            id=plugin.id,
            stage=stage,
            params=dict(selection.params),
            constraints_added=len(proto.constraints) - n_ct,
            variables_added=len(proto.variables) - n_var,
        )
        _RECORDS.append(record)
        logger.info(
            f"\t==\t[plugin:{plugin.id}] added {record.constraints_added} "
            f"constraint(s), {record.variables_added} variable(s)"
        )
