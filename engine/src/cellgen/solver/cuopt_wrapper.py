"""cuOpt backend: the same model-building surface as ``CPSAT``, solved on the GPU.

Two objects live here, and between them they let the engine's constraint code
run on NVIDIA cuOpt without a single call site changing:

``CUOPT``
    stands in for ``CPSAT``. Same method names, same constraint log, same return
    values. It is :class:`~src.cellgen.solver.milp.MilpModel` -- which lowers
    every CP-SAT construct the engine uses to exact linear rows -- plus the
    logging wrapper.

``CuOptSolver``
    stands in for ``cp_model.CpSolver``. Same ``parameters`` attribute, same
    ``Solve`` / ``Value`` / ``ObjectiveValue`` / ``ResponseProto``, and the same
    integer status codes, so ``status in (cp_model.OPTIMAL, cp_model.FEASIBLE)``
    keeps working unchanged.

CP-SAT search parameters have no cuOpt equivalent one for one. The ones that
describe *what to solve* -- the time limit and the relative gap -- are carried
across. The ones that describe *how CP-SAT searches* (subsolver selection,
probing and symmetry levels, linearisation level, the branching strategy, the
random seed) have no meaning for cuOpt's GPU branch-and-bound and are recorded
and dropped. None of them is part of the model, so the feasible set and the
objective are untouched either way. Anything cuOpt-specific goes through
``cell_config["cuopt_parameters"]``, which is passed to ``set_parameter`` as is.

cuOpt returns doubles. Every variable here is integral, so the result is rounded
and then checked against the rows exactly; a solution that does not satisfy them
is reported as UNKNOWN rather than written out as a layout.
"""

from __future__ import annotations

import time

from loguru import logger

from src.cellgen.solver import milp
from src.cellgen.solver.milp import Constraint, MilpModel, Var
from src.cellgen.solver.oplog import OperationLog

# Mirrors of cp_model's status codes. They compare equal to the CpSolverStatus
# enum members, which is what the orchestrators test against.
UNKNOWN = 0
MODEL_INVALID = 1
FEASIBLE = 2
INFEASIBLE = 3
OPTIMAL = 4

_STATUS_NAMES = {
    UNKNOWN: "UNKNOWN",
    MODEL_INVALID: "MODEL_INVALID",
    FEASIBLE: "FEASIBLE",
    INFEASIBLE: "INFEASIBLE",
    OPTIMAL: "OPTIMAL",
}


def _logged(op_type: str, fmt):
    """Log the call, delegate, and wire the log hook onto any constraint handle.

    ``fmt`` receives ``(self, *args, **kwargs)`` and returns the readable
    constraint string, exactly as in ``cpsat_wrapper``.
    """
    def deco(method):
        def wrapper(self, *args, **kwargs):
            # Rendering a constraint costs more than adding one, and a real cell
            # adds hundreds of thousands. Nothing is formatted unless the run
            # actually asked for the log.
            if not self._log.enabled:
                self._log.operation_count += 1
                return method(self, *args, **kwargs)
            ct_str = fmt(self, *args, **kwargs)
            self._log.operation(op_type, ct_str)
            result = method(self, *args, **kwargs)
            if isinstance(result, Constraint):
                result._on_enforce = lambda lits, s=ct_str: self._log.operation(
                    "constraint", f"{s} (only_enforce_if) {self._literal_to_str(lits)}"
                )
            return result
        return wrapper
    return deco


class CUOPT(MilpModel):
    """``MilpModel`` with the constraint log ``CPSAT`` writes.

    Buffers log messages in memory and flushes in batches. Use as a context
    manager to guarantee a final flush on exit.
    """

    def __init__(self, logfile: str = None, cache_limit: int = 10_000):
        super().__init__()
        self._logfile = logfile
        self._log = OperationLog(
            logfile,
            cache_limit,
            banner="CUOPT initialized. Operations will be logged.",
        )
        logger.info(
            f"CUOPT initialized. Logging to '{logfile or 'stdout'}'. "
            f"Cache limit: {cache_limit}."
        )

    # --- logging plumbing ---------------------------------------------------
    def log_comment(self, comment: str):
        self._log.comment(comment)

    def flush(self):
        """Manually flush buffered logs. Prefer `with` for automatic flushing."""
        if self._logfile:
            logger.info(f"Manual flush requested. Flushing {len(self._log._cache)} logs...")
            self._log.flush()
            logger.info("Flush complete.")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.flush()

    def _literal_to_str(self, literal_arg) -> str:
        if isinstance(literal_arg, (list, tuple)):
            return str([str(literal) for literal in literal_arg])
        if isinstance(literal_arg, Var) and literal_arg.lb == literal_arg.ub:
            if literal_arg.lb == 0:
                return "False"
            if literal_arg.lb == 1:
                return "True"
        return str(literal_arg)

    def Proto(self):
        self._log.operation("model_proto", "<raw Proto()>")
        return super().Proto()

    # NOTE: `CPSAT`'s equivalent logging is currently inert on recent OR-Tools
    # releases -- `cp_model.CpModel.__init__` installs its pre-PEP8 aliases
    # (`Add`, `NewBoolVar`, ...) as *instance* attributes, which shadow the
    # subclass methods, so `--flag-log-constraints` writes only its banner
    # there. This backend is not a CpModel subclass and does not have that
    # problem, so its log is complete.

    # --- variables ----------------------------------------------------------
    @_logged("IntVar", lambda self, lb, ub, name: f"name='{name}', domain=[{lb}, {ub}]")
    def NewIntVar(self, lb: int, ub: int, name: str) -> Var:
        return super().NewIntVar(lb, ub, name)

    @_logged("BoolVar", lambda self, name: f"name='{name}'")
    def NewBoolVar(self, name: str) -> Var:
        return super().NewBoolVar(name)

    @_logged("IntVarFromDomain", lambda self, domain, name: f"name='{name}', domain={domain}")
    def NewIntVarFromDomain(self, domain, name: str) -> Var:
        return super().NewIntVarFromDomain(domain, name)

    @_logged("Constant", lambda self, value: f"value={value}")
    def NewConstant(self, value: int) -> Var:
        return super().NewConstant(value)

    # --- constraints --------------------------------------------------------
    @_logged("constraint", lambda self, ct: str(ct))
    def Add(self, ct) -> Constraint:
        return super().Add(ct)

    @_logged("AllDifferent constraint",
             lambda self, variables: f"AllDifferent({[str(v) for v in variables]})")
    def AddAllDifferent(self, variables) -> Constraint:
        return super().AddAllDifferent(variables)

    @_logged("Implication constraint",
             lambda self, b1, b2: f"{self._literal_to_str(b1)} => {self._literal_to_str(b2)}")
    def AddImplication(self, b1, b2) -> Constraint:
        return super().AddImplication(b1, b2)

    @_logged("AtMostOne constraint",
             lambda self, literals: f"AtMostOne{self._literal_to_str(literals)}")
    def AddAtMostOne(self, literals) -> Constraint:
        return super().AddAtMostOne(literals)

    @_logged("ExactlyOne constraint",
             lambda self, literals: f"ExactlyOne{self._literal_to_str(literals)}")
    def AddExactlyOne(self, literals) -> Constraint:
        return super().AddExactlyOne(literals)

    @_logged("BoolOr constraint",
             lambda self, literals: f"BoolOr{self._literal_to_str(literals)}")
    def AddBoolOr(self, literals) -> Constraint:
        return super().AddBoolOr(literals)

    @_logged("BoolAnd constraint",
             lambda self, literals: f"BoolAnd{self._literal_to_str(literals)}")
    def AddBoolAnd(self, literals) -> Constraint:
        return super().AddBoolAnd(literals)

    @_logged("MaxEquality constraint",
             lambda self, mv, exprs: f"{mv} == max({', '.join(str(e) for e in exprs)})")
    def AddMaxEquality(self, max_var, exprs) -> Constraint:
        return super().AddMaxEquality(max_var, exprs)

    @_logged("MinEquality constraint",
             lambda self, mv, exprs: f"{mv} == min({', '.join(str(e) for e in exprs)})")
    def AddMinEquality(self, min_var, exprs) -> Constraint:
        return super().AddMinEquality(min_var, exprs)

    @_logged("Linear constraint", lambda self, expr, lb, ub: f"{lb} <= {expr} <= {ub}")
    def AddLinearConstraint(self, expr, lb: int, ub: int) -> Constraint:
        return super().AddLinearConstraint(expr, lb, ub)

    @_logged("MultiplicationEquality constraint",
             lambda self, target, factors: f"{target} == {' * '.join(str(f) for f in factors)}")
    def AddMultiplicationEquality(self, target, factors) -> Constraint:
        return super().AddMultiplicationEquality(target, factors)

    @_logged("DecisionStrategy",
             lambda self, vars_, vs, ds: f"vars={[str(v) for v in vars_]}, "
                                         f"var_strategy={vs}, domain_strategy={ds} "
                                         f"(recorded; cuOpt does not take a branching order)")
    def AddDecisionStrategy(self, variables, var_strategy: int, domain_strategy: int):
        return super().AddDecisionStrategy(variables, var_strategy, domain_strategy)

    # --- objective ----------------------------------------------------------
    def Minimize(self, expr):
        self._log.operation("Objective", f"Minimize({expr})")
        super().Minimize(expr)
        self.flush()

    def Maximize(self, expr):
        self._log.operation("Objective", f"Maximize({expr})")
        super().Maximize(expr)
        self.flush()

    # --- hints --------------------------------------------------------------
    @_logged("Hint", lambda self, var, value: f"{var} = {value}")
    def AddHint(self, var: Var, value: int):
        return super().AddHint(var, value)


# --------------------------------------------------------------------------- #
# solver                                                                       #
# --------------------------------------------------------------------------- #

class _Parameters:
    """The ``CpSolver.parameters`` fields the orchestrators set.

    Present so the existing model-preset blocks run untouched. Only
    ``max_time_in_seconds`` and ``relative_gap_limit`` reach cuOpt; the rest
    describe CP-SAT's search and are reported as dropped.
    """

    __slots__ = (
        "num_search_workers", "random_seed", "log_search_progress",
        "log_to_stdout", "max_time_in_seconds", "relative_gap_limit",
        "cp_model_presolve", "cp_model_probing_level", "symmetry_level",
        "symmetry_detection_deterministic_time_limit", "ignore_subsolvers",
        "search_branching", "interleave_search", "interleave_batch_size",
        "linearization_level",
    )

    def __init__(self):
        self.num_search_workers = 8
        self.random_seed = 0
        self.log_search_progress = False
        self.log_to_stdout = True
        self.max_time_in_seconds = 0.0
        self.relative_gap_limit = 0.0
        self.cp_model_presolve = True
        self.cp_model_probing_level = 2
        self.symmetry_level = 2
        self.symmetry_detection_deterministic_time_limit = 0.0
        self.ignore_subsolvers = []
        self.search_branching = 0
        self.interleave_search = False
        self.interleave_batch_size = 0
        self.linearization_level = 1


class _ResponseProto:
    """Stand-in for ``CpSolverResponse``: the solution vector, by variable index."""

    __slots__ = ("solution",)

    def __init__(self, solution):
        self.solution = solution


class CuOptSolver:
    """``cp_model.CpSolver``'s interface, backed by cuOpt's MILP solver."""

    def __init__(self, cuopt_parameters: dict | None = None):
        self.parameters = _Parameters()
        self.log_callback = None
        self.cuopt_parameters = dict(cuopt_parameters or {})
        self._status = UNKNOWN
        self._values: list[int] = []
        self._model: MilpModel | None = None
        self._objective_value = 0.0
        self._best_bound = 0.0
        self._solve_time = 0.0
        self._termination = ""
        self._mip_gap = 0.0

    # --- results ------------------------------------------------------------
    def Value(self, expr) -> int:
        if not self._values:
            raise RuntimeError("no solution available; call Solve() first")
        return milp.evaluate(expr, self._values)

    def BooleanValue(self, literal) -> bool:
        return bool(self.Value(literal))

    def ObjectiveValue(self) -> float:
        return self._objective_value

    def BestObjectiveBound(self) -> float:
        return self._best_bound

    def ResponseProto(self) -> _ResponseProto:
        return _ResponseProto(self._values)

    def StatusName(self, status: int | None = None) -> str:
        return _STATUS_NAMES.get(self._status if status is None else status, "UNKNOWN")

    def WallTime(self) -> float:
        return self._solve_time

    def StopSearch(self):
        """No-op: cuOpt owns its own termination, driven by the time limit."""

    # --- solve --------------------------------------------------------------
    def Solve(self, model: MilpModel) -> int:
        self._model = model
        self._values = []
        self._objective_value = 0.0

        exported = milp.export(model)
        logger.info(
            f"\t==\t[cuOpt] {exported.num_variables} variables, "
            f"{exported.num_rows} rows, {exported.matrix_values.size} non-zeros"
        )
        self._report_dropped_parameters(model)

        if exported.infeasible:
            logger.error("\t==\t[cuOpt] model contains an unsatisfiable constraint")
            self._status = INFEASIBLE
            return self._status

        data_model, settings, solver_module = self._build(exported)

        started = time.time()
        solution = solver_module.Solve(data_model, settings)
        self._solve_time = time.time() - started

        self._status = self._collect(model, exported, solution)
        return self._status

    # --- internals ----------------------------------------------------------
    def _build(self, exported):
        """Turn the exported arrays into a cuOpt data model and settings."""
        try:
            from cuopt.linear_programming import DataModel, SolverSettings
            from cuopt.linear_programming import solver as solver_module
        except ImportError as exc:  # pragma: no cover - depends on the host GPU stack
            raise ImportError(
                "the cuopt solver backend needs NVIDIA cuOpt and a CUDA GPU "
                "(pip install cuopt-cu12). Run with --solver cpsat to use OR-Tools "
                f"CP-SAT instead. Original error: {exc}"
            ) from exc

        import numpy as np

        data_model = DataModel()
        data_model.set_csr_constraint_matrix(
            exported.matrix_values, exported.matrix_indices, exported.matrix_offsets
        )
        # A sense per row plus one right-hand side, which is how cuOpt's own
        # model builder states constraints.
        data_model.set_row_types(exported.row_types)
        data_model.set_constraint_bounds(exported.row_rhs)
        data_model.set_variable_lower_bounds(exported.variable_lower)
        data_model.set_variable_upper_bounds(exported.variable_upper)
        # Every CP-SAT variable is an integer, so every cuOpt variable is too.
        data_model.set_variable_types(
            np.full(exported.num_variables, b"I", dtype="S1")
        )
        data_model.set_variable_names(exported.variable_names)
        data_model.set_objective_coefficients(exported.objective)
        data_model.set_objective_offset(exported.objective_offset)
        data_model.set_maximize(bool(exported.maximize))
        if exported.initial_solution is not None:
            data_model.set_initial_primal_solution(exported.initial_solution)

        settings = SolverSettings()
        self._apply_settings(settings)
        return data_model, settings, solver_module

    def _apply_settings(self, settings):
        from cuopt.linear_programming import solver_settings as cuopt_settings

        available = set(cuopt_settings.solver_params)

        def apply(candidates, value, what):
            for name in candidates:
                if name in available:
                    settings.set_parameter(name, value)
                    return True
            logger.warning(
                f"\t==\t[cuOpt] no parameter for {what}; this build offers none of "
                f"{list(candidates)}"
            )
            return False

        params = self.parameters
        if params.max_time_in_seconds:
            apply(("time_limit",), float(params.max_time_in_seconds), "the time limit")
        if params.relative_gap_limit:
            apply(
                ("mip_relative_gap", "relative_gap_tolerance", "relative_mip_gap"),
                float(params.relative_gap_limit),
                "the relative gap",
            )
        if params.num_search_workers:
            apply(
                ("num_cpu_threads", "threads"),
                int(params.num_search_workers),
                "the worker count",
            )
        if params.log_search_progress:
            apply(("log_to_console",), True, "search logging")

        # Anything the caller wants to hand cuOpt directly wins over the above.
        for name, value in self.cuopt_parameters.items():
            if name not in available:
                logger.warning(f"\t==\t[cuOpt] unknown parameter {name!r}, ignored")
                continue
            settings.set_parameter(name, value)

    def _report_dropped_parameters(self, model: MilpModel):
        """Say plainly which CP-SAT search knobs cuOpt cannot honour."""
        params = self.parameters
        dropped = []
        if params.random_seed:
            dropped.append(f"random_seed={params.random_seed}")
        if params.ignore_subsolvers:
            dropped.append(f"ignore_subsolvers={len(params.ignore_subsolvers)} entries")
        if params.search_branching:
            dropped.append(f"search_branching={params.search_branching}")
        if params.linearization_level != 1:
            dropped.append(f"linearization_level={params.linearization_level}")
        if model.decision_strategies:
            total = sum(len(v) for v, _, _ in model.decision_strategies)
            dropped.append(f"{len(model.decision_strategies)} decision strategies ({total} vars)")
        if dropped:
            logger.info(
                "\t==\t[cuOpt] CP-SAT search settings with no cuOpt equivalent, "
                f"ignored (the model itself is unchanged): {', '.join(dropped)}"
            )

    def _collect(self, model, exported, solution) -> int:
        """Round cuOpt's primal solution, check it, and map the status."""
        from cuopt.linear_programming.solver.solver_wrapper import MILPTerminationStatus

        termination = solution.get_termination_status()
        self._termination = str(solution.get_termination_reason() or termination)
        # The bound and the gap live in the MILP stats dict; a solution cuOpt
        # classified as an LP (every integer variable gone in presolve) has no
        # such dict and raises rather than returning one.
        try:
            stats = solution.get_milp_stats() or {}
        except Exception:  # pragma: no cover - depends on the cuOpt build
            stats = {}
        self._best_bound = float(stats.get("solution_bound", 0.0))
        self._mip_gap = float(stats.get("mip_gap", 0.0))

        primal = solution.get_primal_solution()
        has_solution = primal is not None and len(primal) == exported.num_variables

        if self.log_callback:
            self.log_callback(
                f"[cuOpt] {self._termination} in {self._solve_time:.2f}s "
                f"bound={self._best_bound} gap={self._mip_gap}"
            )

        if termination == MILPTerminationStatus.Infeasible:
            return INFEASIBLE
        if termination in (
            MILPTerminationStatus.Unbounded,
            MILPTerminationStatus.UnboundedOrInfeasible,
        ):
            logger.error(f"\t==\t[cuOpt] {self._termination}; the model is not bounded")
            return MODEL_INVALID
        if not has_solution:
            return UNKNOWN

        values = []
        max_drift = 0.0
        for var, raw in zip(model.variables, primal):
            rounded = int(round(float(raw)))
            max_drift = max(max_drift, abs(float(raw) - rounded))
            values.append(min(max(rounded, var.lb), var.ub))
        self._values = values

        bad = milp.violations(model, values)
        if bad:
            logger.error(
                f"\t==\t[cuOpt] the returned solution violates {len(bad)} row(s) "
                f"after rounding (largest integrality drift {max_drift:.3g}); "
                f"refusing to report it as a solution. First: {bad[0]}"
            )
            self._values = []
            return UNKNOWN

        self._objective_value = float(milp.evaluate(model.objective, values))
        if max_drift > 1e-6:
            logger.warning(
                f"\t==\t[cuOpt] integer variables came back up to {max_drift:.3g} "
                f"off an integer; rounded and re-checked against every row"
            )
        if termination == MILPTerminationStatus.Optimal:
            return OPTIMAL
        return FEASIBLE
