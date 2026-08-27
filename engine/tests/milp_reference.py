"""A CPU stand-in for cuOpt, so the lowering can be tested without a GPU.

The cuOpt backend has two halves: the translation of CP-SAT constructs into
linear rows (:mod:`src.cellgen.solver.milp`), and the call into cuOpt itself.
The first half is where a mistake would silently change the model, and it is
pure Python -- so it is worth testing on every machine, GPU or not.

This module solves an exported MILP with CP-SAT instead. That sounds circular,
but it is not: the rows are consumed as anonymous linear inequalities, with no
knowledge of which CP-SAT construct produced them. If the rows admit exactly
the assignments the original model admits, the lowering is right.

:class:`ReferenceMilpSolver` mimics ``CuOptSolver``, so the whole engine can be
driven end to end through the cuOpt code path.
"""

from __future__ import annotations

import math

from ortools.sat.python import cp_model

from src.cellgen.solver import milp
from src.cellgen.solver.cuopt_wrapper import (
    FEASIBLE, INFEASIBLE, OPTIMAL, UNKNOWN, _Parameters, _ResponseProto,
)


def to_cpsat(exported: milp.ExportedModel):
    """Rebuild an exported MILP as a plain CP-SAT model of linear rows."""
    model = cp_model.CpModel()
    variables = [
        model.NewIntVar(int(lb), int(ub), name)
        for lb, ub, name in zip(
            exported.variable_lower, exported.variable_upper, exported.variable_names
        )
    ]
    values = exported.matrix_values
    indices = exported.matrix_indices
    offsets = exported.matrix_offsets
    for row in range(exported.num_rows):
        start, end = int(offsets[row]), int(offsets[row + 1])
        expr = sum(
            int(values[i]) * variables[int(indices[i])] for i in range(start, end)
        )
        low, high = exported.row_lower[row], exported.row_upper[row]
        if math.isinf(low):
            model.Add(expr <= int(high))
        elif math.isinf(high):
            model.Add(expr >= int(low))
        elif low == high:
            model.Add(expr == int(low))
        else:
            model.AddLinearConstraint(expr, int(low), int(high))
    if exported.infeasible:
        model.Add(False)
    return model, variables


def _objective_expr(exported, variables):
    return sum(
        int(coefficient) * variables[index]
        for index, coefficient in enumerate(exported.objective)
        if coefficient
    ) + int(exported.objective_offset)


class ReferenceMilpSolver:
    """``CuOptSolver``'s interface, backed by CP-SAT solving the exported rows."""

    def __init__(self, cuopt_parameters: dict | None = None):
        self.parameters = _Parameters()
        self.log_callback = None
        self.cuopt_parameters = dict(cuopt_parameters or {})
        self._values: list[int] = []
        self._objective_value = 0.0
        self._status = UNKNOWN
        self.exported: milp.ExportedModel | None = None

    def Solve(self, model: milp.MilpModel) -> int:
        exported = milp.export(model)
        self.exported = exported
        self._values = []
        if exported.infeasible:
            self._status = INFEASIBLE
            return self._status

        cp, variables = to_cpsat(exported)
        objective = _objective_expr(exported, variables)
        if exported.maximize:
            cp.Maximize(objective)
        else:
            cp.Minimize(objective)

        solver = cp_model.CpSolver()
        if self.parameters.max_time_in_seconds:
            solver.parameters.max_time_in_seconds = self.parameters.max_time_in_seconds
        solver.parameters.num_search_workers = self.parameters.num_search_workers
        status = solver.Solve(cp)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self._status = INFEASIBLE if status == cp_model.INFEASIBLE else UNKNOWN
            return self._status

        self._values = [solver.Value(v) for v in variables]
        assert not milp.violations(model, self._values), "reference solution is infeasible"
        self._objective_value = float(milp.evaluate(model.objective, self._values))
        self._status = OPTIMAL if status == cp_model.OPTIMAL else FEASIBLE
        return self._status

    def Value(self, expr) -> int:
        return milp.evaluate(expr, self._values)

    def BooleanValue(self, literal) -> bool:
        return bool(self.Value(literal))

    def ObjectiveValue(self) -> float:
        return self._objective_value

    def BestObjectiveBound(self) -> float:
        return self._objective_value

    def ResponseProto(self) -> _ResponseProto:
        return _ResponseProto(self._values)

    def StopSearch(self):
        pass

    def WallTime(self) -> float:
        return 0.0


class _Collector(cp_model.CpSolverSolutionCallback):
    def __init__(self, tracked, seen, cap):
        super().__init__()
        self._tracked = tracked
        self._seen = seen
        self._cap = cap

    def on_solution_callback(self):
        self._seen.add(tuple(self.Value(v) for v in self._tracked))
        if len(self._seen) > self._cap:
            self.StopSearch()


def _enumerate(model, tracked, cap):
    seen: set[tuple[int, ...]] = set()
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    solver.parameters.num_search_workers = 1
    solver.parameters.max_time_in_seconds = 60
    status = solver.Solve(model, _Collector(tracked, seen, cap))
    assert status != cp_model.UNKNOWN, "enumeration did not finish"
    assert len(seen) <= cap, f"more than {cap} distinct solutions; shrink the test model"
    return seen


def cpsat_solutions(model, tracked, cap=5000):
    """Every assignment of ``tracked`` a CP-SAT model admits."""
    return _enumerate(model, tracked, cap)


def milp_solutions(model: milp.MilpModel, tracked_names, cap=5000):
    """Every assignment of the named variables the lowered rows admit.

    The rows are rebuilt as anonymous linear constraints and enumerated, then
    projected onto the original variables -- auxiliaries introduced by the
    lowering are quantified away, which is exactly the claim being tested.
    """
    exported = milp.export(model)
    if exported.infeasible:
        return set()
    cp, variables = to_cpsat(exported)
    index = {name: i for i, name in enumerate(exported.variable_names)}
    tracked = [variables[index[name]] for name in tracked_names]
    return _enumerate(cp, tracked, cap)
