"""Registry of pluggable constraints.

A constraint plugin is an ordinary function that takes the solve orchestrator
(the "instance" that every constraint in ``src/cellgen/core`` already receives)
plus a dict of parameters, and emits CP-SAT constraints through ``inst.opt``::

    from src.cellgen.plugins import constraint

    @constraint(
        id="max_vias_per_col",
        stage="post_routing",
        tech=["FinFET", "QFET"],
        params={"max_vias": 2},
        description="Cap the number of vias sharing a column.",
    )
    def max_vias_per_col(inst, params):
        for col in inst.lgg.cols_in_layer("M1"):
            inst.opt.Add(sum(...) <= params["max_vias"])

The decorator only records metadata -- it never runs at import time. Execution
is driven by :mod:`src.cellgen.plugins.hooks` at the stage the plugin declared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# Points in the constraint build where a plugin may run. Ordered as they fire.
#
# ``pre_solve`` runs after every built-in constraint, the cluster/placement
# injections and the top-layer-usage cap -- i.e. the last chance to touch the
# model before the objective is assembled and CP-SAT starts.
STAGES = (
    "pre_placement",
    "post_placement",
    "pre_routing",
    "post_routing",
    "pre_solve",
)

TECHNOLOGIES = ("FinFET", "CFET", "QFET")


@dataclass(frozen=True)
class ConstraintPlugin:
    """One registered constraint and everything needed to run and describe it."""

    id: str
    fn: Callable
    stage: str
    tech: tuple[str, ...]
    params: dict = field(default_factory=dict)
    description: str = ""
    source_file: str | None = None

    def applies_to(self, tech_name: str) -> bool:
        return tech_name in self.tech

    def merged_params(self, overrides: dict | None) -> dict:
        """Declared defaults with the manifest's overrides layered on top."""
        merged = dict(self.params)
        merged.update(overrides or {})
        return merged


# id -> plugin. Insertion-ordered, which is the fallback run order when no
# manifest pins an explicit one.
_REGISTRY: dict[str, ConstraintPlugin] = {}


def constraint(
    *,
    id: str,
    stage: str,
    tech: list[str] | tuple[str, ...] = TECHNOLOGIES,
    params: dict | None = None,
    description: str = "",
):
    """Register a function as a constraint plugin. See the module docstring."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {list(STAGES)}")

    unknown = [t for t in tech if t not in TECHNOLOGIES]
    if unknown:
        raise ValueError(
            f"Plugin {id!r} declares unknown technology {unknown}. "
            f"Valid: {list(TECHNOLOGIES)}"
        )
    if not tech:
        raise ValueError(f"Plugin {id!r} must declare at least one technology.")

    def decorate(fn):
        if id in _REGISTRY:
            existing = _REGISTRY[id].source_file or "<unknown>"
            raise ValueError(
                f"Duplicate constraint id {id!r} (already registered from {existing})."
            )
        _REGISTRY[id] = ConstraintPlugin(
            id=id,
            fn=fn,
            stage=stage,
            tech=tuple(tech),
            params=dict(params or {}),
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
            source_file=getattr(fn, "__module__", None),
        )
        return fn

    return decorate


def iter_constraints(stage: str, tech_name: str) -> list[ConstraintPlugin]:
    """Registered plugins for one stage and technology, in registration order."""
    if stage not in STAGES:
        raise ValueError(f"Unknown stage {stage!r}. Valid stages: {list(STAGES)}")
    return [
        p for p in _REGISTRY.values()
        if p.stage == stage and p.applies_to(tech_name)
    ]


def get(plugin_id: str) -> ConstraintPlugin | None:
    return _REGISTRY.get(plugin_id)


def all_constraints() -> list[ConstraintPlugin]:
    return list(_REGISTRY.values())


def clear() -> None:
    """Drop every registration. For tests and for reloading a plugin directory."""
    _REGISTRY.clear()
