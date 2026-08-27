"""Solver backend selection, in one place.

Two backends build the *same* model:

``cpsat``
    OR-Tools CP-SAT, on the CPU. The original, and still the default.
``cuopt``
    NVIDIA cuOpt's MILP solver, on the GPU. Every CP-SAT construct the engine
    uses is lowered to exact linear rows by
    :mod:`src.cellgen.solver.milp`; the constraints and the objective are the
    same ones, expressed differently.

Both backends are chosen per run, and both hand back objects with the same
interface, so the constraint code and the orchestrators' solve blocks do not
branch on which one is in play.
"""

from __future__ import annotations

BACKENDS = ("cpsat", "cuopt")


def make_model(backend: str, logfile: str | None = None):
    """The model object the constraint code builds into (``inst.opt``)."""
    if backend == "cpsat":
        from src.cellgen.solver.cpsat_wrapper import CPSAT

        return CPSAT(logfile=logfile)
    if backend == "cuopt":
        from src.cellgen.solver.cuopt_wrapper import CUOPT

        return CUOPT(logfile=logfile)
    raise NotImplementedError(
        f"Solver backend {backend!r} is not supported. Available: {list(BACKENDS)}."
    )


def make_solver(backend: str, cell_config: dict | None = None):
    """The solver that runs the model (``inst.solver``).

    Both return values expose ``parameters``, ``log_callback``, ``Solve``,
    ``Value``, ``ObjectiveValue`` and ``ResponseProto``, and both report the
    same integer status codes.
    """
    if backend == "cpsat":
        from ortools.sat.python import cp_model

        return cp_model.CpSolver()
    if backend == "cuopt":
        from src.cellgen.solver.cuopt_wrapper import CuOptSolver

        extra = {}
        if cell_config:
            entry = cell_config.get("cuopt_parameters")
            if isinstance(entry, dict):
                # Accept both the {"value": {...}} shape the rest of the config
                # uses and a bare mapping.
                extra = entry.get("value", entry) if "value" in entry else entry
        return CuOptSolver(cuopt_parameters=extra)
    raise NotImplementedError(
        f"Solver backend {backend!r} is not supported. Available: {list(BACKENDS)}."
    )
