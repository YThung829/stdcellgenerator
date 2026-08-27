"""The cuOpt backend builds the same model CP-SAT does.

Every test here states one model twice -- once through ``CPSAT``, once through
``CUOPT`` -- and then compares what the two admit:

* for constraints, the *set of assignments* to the model's own variables, with
  the auxiliaries the lowering introduces projected away. Equal sets means the
  lowering neither lost a restriction nor added one.
* for objectives, the optimal value and the set of optimal assignments.

The MILP side is enumerated by handing the exported rows back to CP-SAT as
anonymous linear inequalities (see ``tests/milp_reference.py``), so the check
never trusts the lowering to describe itself.

Run with::

    cd engine && python -m pytest tests/test_cuopt_backend.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cellgen.solver import milp  # noqa: E402
from src.cellgen.solver.backends import make_model, make_solver  # noqa: E402
from src.cellgen.solver.cpsat_wrapper import CPSAT  # noqa: E402
from src.cellgen.solver.cuopt_wrapper import CUOPT, INFEASIBLE, OPTIMAL  # noqa: E402
from tests.milp_reference import (  # noqa: E402
    ReferenceMilpSolver, cpsat_solutions, milp_solutions,
)


def assert_same_solutions(build, tracked_names, cap=5000):
    """``build(opt)`` on both backends must admit the same assignments.

    ``build`` creates its variables through ``opt`` and returns them keyed by
    name; those are the variables the two solution sets are compared over.
    """
    reference = CPSAT()
    ref_vars = build(reference)
    expected = cpsat_solutions(reference, [ref_vars[n] for n in tracked_names], cap)

    lowered = CUOPT()
    build(lowered)
    actual = milp_solutions(lowered, tracked_names, cap)

    assert actual == expected, (
        f"the lowered model admits a different set of assignments over "
        f"{tracked_names}\n  only CP-SAT: {sorted(expected - actual)[:10]}\n"
        f"  only MILP:   {sorted(actual - expected)[:10]}"
    )
    return expected


def assert_same_optimum(build, tracked_names):
    """Both backends must reach the same objective value.

    Equal values alone would not rule out the lowered model having reached them
    at a point CP-SAT forbids, so the winning assignment is also replayed
    against the CP-SAT model.
    """
    reference = CPSAT()
    build(reference)
    solver = cp_model.CpSolver()
    ref_status = solver.Solve(reference)

    lowered = CUOPT()
    low_vars = build(lowered)
    milp_solver = ReferenceMilpSolver()
    low_status = milp_solver.Solve(lowered)

    if ref_status == cp_model.INFEASIBLE:
        assert low_status == INFEASIBLE, "CP-SAT says infeasible, the lowering does not"
        return None
    assert ref_status == cp_model.OPTIMAL, solver.StatusName(ref_status)
    assert low_status == OPTIMAL
    assert milp_solver.ObjectiveValue() == solver.ObjectiveValue(), (
        f"objective differs: CP-SAT {solver.ObjectiveValue()} "
        f"vs MILP {milp_solver.ObjectiveValue()}"
    )

    replay = CPSAT()
    replay_vars = build(replay)
    for name in tracked_names:
        replay.Add(replay_vars[name] == milp_solver.Value(low_vars[name]))
    check = cp_model.CpSolver()
    assert check.Solve(replay) == cp_model.OPTIMAL, (
        "the lowered model's optimum is not a point CP-SAT admits"
    )
    assert check.ObjectiveValue() == solver.ObjectiveValue()
    return milp_solver.ObjectiveValue()


# --------------------------------------------------------------------------- #
# linear constraints                                                           #
# --------------------------------------------------------------------------- #

class TestLinearConstraints:
    @pytest.mark.parametrize("op", ["<=", ">=", "==", "<", ">", "!="])
    def test_every_comparison(self, op):
        def build(opt):
            x = opt.NewIntVar(0, 5, "x")
            y = opt.NewIntVar(0, 5, "y")
            expr = x + 2 * y - 3
            if op == "<=":
                opt.Add(expr <= 4)
            elif op == ">=":
                opt.Add(expr >= 4)
            elif op == "==":
                opt.Add(expr == 4)
            elif op == "<":
                opt.Add(expr < 4)
            elif op == ">":
                opt.Add(expr > 4)
            else:
                opt.Add(expr != 4)
            return {"x": x, "y": y}

        assert_same_solutions(build, ["x", "y"])

    def test_not_equal_between_two_variables(self):
        def build(opt):
            x = opt.NewIntVar(0, 4, "x")
            y = opt.NewIntVar(0, 4, "y")
            opt.Add(x != y)
            opt.Add(x + y != 4)
            return {"x": x, "y": y}

        assert_same_solutions(build, ["x", "y"])

    def test_not_equal_against_an_unreachable_value(self):
        """The disjunction must not become infeasible when one side is empty."""
        def build(opt):
            x = opt.NewIntVar(0, 3, "x")
            opt.Add(x != 9)
            opt.Add(x != -2)
            return {"x": x}

        assert len(assert_same_solutions(build, ["x"])) == 4

    def test_sum_over_an_empty_list_is_a_plain_bool(self):
        """`Add(sum([]) == 0)` collapses to True before the model ever sees it."""
        def build(opt):
            x = opt.NewIntVar(0, 2, "x")
            opt.Add(sum([]) == 0)
            opt.Add(x >= 1)
            return {"x": x}

        assert assert_same_solutions(build, ["x"]) == {(1,), (2,)}

    def test_a_false_constraint_is_infeasible(self):
        lowered = CUOPT()
        lowered.NewIntVar(0, 2, "x")
        lowered.Add(False)
        assert milp.export(lowered).infeasible

    def test_linear_constraint_with_two_bounds(self):
        def build(opt):
            x = opt.NewIntVar(-4, 9, "x")
            opt.AddLinearConstraint(2 * x + 1, 3, 8)
            return {"x": x}

        assert_same_solutions(build, ["x"])


# --------------------------------------------------------------------------- #
# enforcement literals                                                         #
# --------------------------------------------------------------------------- #

class TestOnlyEnforceIf:
    @pytest.mark.parametrize("op", ["<=", ">=", "==", "!="])
    def test_single_literal(self, op):
        def build(opt):
            b = opt.NewBoolVar("b")
            x = opt.NewIntVar(0, 5, "x")
            expr = x + 1
            ct = {
                "<=": lambda: opt.Add(expr <= 3),
                ">=": lambda: opt.Add(expr >= 3),
                "==": lambda: opt.Add(expr == 3),
                "!=": lambda: opt.Add(expr != 3),
            }[op]()
            ct.OnlyEnforceIf(b)
            return {"b": b, "x": x}

        assert_same_solutions(build, ["b", "x"])

    def test_negated_literal(self):
        def build(opt):
            b = opt.NewBoolVar("b")
            x = opt.NewIntVar(0, 5, "x")
            opt.Add(x == 2).OnlyEnforceIf(b.Not())
            opt.Add(x >= 4).OnlyEnforceIf(b)
            return {"b": b, "x": x}

        assert_same_solutions(build, ["b", "x"])

    def test_several_literals_are_a_conjunction(self):
        def build(opt):
            p = opt.NewBoolVar("p")
            q = opt.NewBoolVar("q")
            x = opt.NewIntVar(0, 5, "x")
            opt.Add(x <= 1).OnlyEnforceIf([p, q.Not()])
            return {"p": p, "q": q, "x": x}

        assert_same_solutions(build, ["p", "q", "x"])

    def test_repeated_calls_accumulate(self):
        """Two calls on one constraint conjoin, they do not replace."""
        def build(opt):
            p = opt.NewBoolVar("p")
            q = opt.NewBoolVar("q")
            x = opt.NewIntVar(0, 5, "x")
            ct = opt.Add(x >= 4)
            ct.OnlyEnforceIf(p)
            ct.OnlyEnforceIf(q)
            return {"p": p, "q": q, "x": x}

        assert_same_solutions(build, ["p", "q", "x"])

    def test_enforced_bool_constraints(self):
        def build(opt):
            g = opt.NewBoolVar("g")
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            opt.AddBoolOr([a, b]).OnlyEnforceIf(g)
            opt.AddBoolAnd([a.Not(), b.Not()]).OnlyEnforceIf(g.Not())
            return {"g": g, "a": a, "b": b}

        assert_same_solutions(build, ["g", "a", "b"])

    def test_enforced_implication(self):
        def build(opt):
            g = opt.NewBoolVar("g")
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            opt.AddImplication(a, b).OnlyEnforceIf(g)
            return {"g": g, "a": a, "b": b}

        assert_same_solutions(build, ["g", "a", "b"])

    def test_enforced_false_forbids_the_literals(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            opt.Add(False).OnlyEnforceIf([a, b.Not()])
            return {"a": a, "b": b}

        assert_same_solutions(build, ["a", "b"])

    def test_enforcement_on_a_sum_of_many_terms(self):
        """The relaxation has to cover the widest violation the row allows."""
        def build(opt):
            g = opt.NewBoolVar("g")
            xs = [opt.NewBoolVar(f"x{i}") for i in range(4)]
            opt.Add(sum(xs) == 0).OnlyEnforceIf(g)
            opt.Add(sum(xs) >= 3).OnlyEnforceIf(g.Not())
            return {"g": g, **{f"x{i}": x for i, x in enumerate(xs)}}

        assert_same_solutions(build, ["g", "x0", "x1", "x2", "x3"])


# --------------------------------------------------------------------------- #
# boolean constraints                                                          #
# --------------------------------------------------------------------------- #

class TestBooleanConstraints:
    def test_or_and_atmostone_exactlyone_implication(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            c = opt.NewBoolVar("c")
            d = opt.NewBoolVar("d")
            opt.AddBoolOr([a, b])
            opt.AddAtMostOne([b, c])
            opt.AddExactlyOne([c, d])
            opt.AddImplication(a, d)
            return {"a": a, "b": b, "c": c, "d": d}

        assert_same_solutions(build, ["a", "b", "c", "d"])

    def test_bool_and(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            opt.AddBoolAnd([a, b.Not()])
            return {"a": a, "b": b}

        assert assert_same_solutions(build, ["a", "b"]) == {(1, 0)}

    def test_negated_literals_everywhere(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            c = opt.NewBoolVar("c")
            opt.AddBoolOr([a.Not(), b.Not(), c])
            opt.AddAtMostOne([a.Not(), c.Not()])
            opt.AddImplication(b.Not(), a.Not())
            return {"a": a, "b": b, "c": c}

        assert_same_solutions(build, ["a", "b", "c"])

    def test_double_negation_returns_the_variable(self):
        opt = CUOPT()
        b = opt.NewBoolVar("b")
        assert b.Not().Not() is b


# --------------------------------------------------------------------------- #
# max / min / all-different / products                                         #
# --------------------------------------------------------------------------- #

class TestExtremumEquality:
    def test_max_equality(self):
        def build(opt):
            x = opt.NewIntVar(0, 4, "x")
            y = opt.NewIntVar(0, 4, "y")
            m = opt.NewIntVar(0, 4, "m")
            opt.AddMaxEquality(m, [x, y])
            return {"x": x, "y": y, "m": m}

        assert_same_solutions(build, ["x", "y", "m"])

    def test_min_equality_over_expressions(self):
        def build(opt):
            x = opt.NewIntVar(0, 4, "x")
            y = opt.NewIntVar(0, 4, "y")
            m = opt.NewIntVar(-4, 4, "m")
            opt.AddMinEquality(m, [x - 1, 2 * y - 3, 2])
            return {"x": x, "y": y, "m": m}

        assert_same_solutions(build, ["x", "y", "m"])

    def test_max_equality_with_a_narrower_target(self):
        """The target's own domain still restricts, exactly as in CP-SAT."""
        def build(opt):
            x = opt.NewIntVar(0, 4, "x")
            y = opt.NewIntVar(0, 4, "y")
            m = opt.NewIntVar(0, 2, "m")
            opt.AddMaxEquality(m, [x, y])
            return {"x": x, "y": y, "m": m}

        assert_same_solutions(build, ["x", "y", "m"])


class TestAllDifferent:
    def _build(self, opt):
        xs = [opt.NewIntVar(0, 3, f"x{i}") for i in range(3)]
        opt.AddAllDifferent(xs)
        opt.Add(xs[0] <= 1)
        return {f"x{i}": x for i, x in enumerate(xs)}

    def test_assignment_encoding(self):
        assert_same_solutions(self._build, ["x0", "x1", "x2"])

    def test_pairwise_encoding(self, monkeypatch):
        """Same constraint, taken through the fallback formulation."""
        monkeypatch.setattr(milp, "ALL_DIFFERENT_ASSIGNMENT_LIMIT", 0)
        assert_same_solutions(self._build, ["x0", "x1", "x2"])

    def test_enforced_all_different(self):
        def build(opt):
            g = opt.NewBoolVar("g")
            xs = [opt.NewIntVar(0, 1, f"x{i}") for i in range(2)]
            opt.AddAllDifferent(xs).OnlyEnforceIf(g)
            return {"g": g, **{f"x{i}": x for i, x in enumerate(xs)}}

        assert_same_solutions(build, ["g", "x0", "x1"])

    def test_over_a_domain_with_holes(self):
        def build(opt):
            domain = cp_model.Domain.FromValues([0, 2, 5])
            xs = [opt.NewIntVarFromDomain(domain, f"x{i}") for i in range(3)]
            opt.AddAllDifferent(xs)
            return {f"x{i}": x for i, x in enumerate(xs)}

        assert len(assert_same_solutions(build, ["x0", "x1", "x2"])) == 6


class TestMultiplicationEquality:
    def test_conjunction_of_literals(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            c = opt.NewBoolVar("c")
            t = opt.NewBoolVar("t")
            opt.AddMultiplicationEquality(t, [a, b, c])
            return {"a": a, "b": b, "c": c, "t": t}

        assert_same_solutions(build, ["a", "b", "c", "t"])

    def test_conjunction_with_a_negated_literal(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            t = opt.NewBoolVar("t")
            opt.AddMultiplicationEquality(t, [a, b.Not()])
            return {"a": a, "b": b, "t": t}

        assert_same_solutions(build, ["a", "b", "t"])

    def test_integer_gated_by_a_literal(self):
        def build(opt):
            b = opt.NewBoolVar("b")
            x = opt.NewIntVar(-2, 3, "x")
            t = opt.NewIntVar(-3, 4, "t")
            opt.AddMultiplicationEquality(t, [x, b])
            return {"b": b, "x": x, "t": t}

        assert_same_solutions(build, ["b", "x", "t"])

    def test_integer_gated_by_two_literals(self):
        def build(opt):
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            x = opt.NewIntVar(0, 3, "x")
            t = opt.NewIntVar(0, 3, "t")
            opt.AddMultiplicationEquality(t, [x, a, b])
            return {"a": a, "b": b, "x": x, "t": t}

        assert_same_solutions(build, ["a", "b", "x", "t"])

    def test_enforced_product(self):
        """Enforcement relaxes the whole linearisation, gate rows included."""
        def build(opt):
            g = opt.NewBoolVar("g")
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            t = opt.NewBoolVar("t")
            opt.AddMultiplicationEquality(t, [a, b]).OnlyEnforceIf(g)
            return {"g": g, "a": a, "b": b, "t": t}

        assert len(assert_same_solutions(build, ["g", "a", "b", "t"])) == 12

    def test_enforced_product_with_an_integer_factor(self):
        def build(opt):
            g = opt.NewBoolVar("g")
            a = opt.NewBoolVar("a")
            b = opt.NewBoolVar("b")
            x = opt.NewIntVar(0, 2, "x")
            t = opt.NewIntVar(0, 2, "t")
            opt.AddMultiplicationEquality(t, [x, a, b]).OnlyEnforceIf(g)
            return {"g": g, "a": a, "b": b, "x": x, "t": t}

        assert_same_solutions(build, ["g", "a", "b", "x", "t"])

    def test_two_integer_factors_are_refused(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        y = opt.NewIntVar(0, 3, "y")
        t = opt.NewIntVar(0, 9, "t")
        with pytest.raises(milp.ModelError, match="non-boolean"):
            opt.AddMultiplicationEquality(t, [x, y])


class TestDomains:
    def test_domain_with_holes(self):
        def build(opt):
            domain = cp_model.Domain.FromValues([0, 3, 4, 5, 9])
            x = opt.NewIntVarFromDomain(domain, "x")
            return {"x": x}

        assert assert_same_solutions(build, ["x"]) == {(0,), (3,), (4,), (5,), (9,)}

    def test_contiguous_domain_needs_no_auxiliaries(self):
        opt = CUOPT()
        opt.NewIntVarFromDomain(cp_model.Domain.FromValues([2, 3, 4]), "x")
        assert len(opt.Proto().variables) == 1
        assert len(opt.Proto().constraints) == 0

    def test_negative_and_sparse_domain(self):
        def build(opt):
            x = opt.NewIntVarFromDomain(cp_model.Domain.FromValues([-5, -1, 4]), "x")
            y = opt.NewIntVarFromDomain(cp_model.Domain.FromValues([-2, 0, 7]), "y")
            opt.Add(x + y >= 0)
            return {"x": x, "y": y}

        assert_same_solutions(build, ["x", "y"])

    def test_domain_intervals_accepts_plain_values(self):
        assert milp.domain_intervals([1, 2, 3, 7, 9, 10]) == [(1, 3), (7, 7), (9, 10)]
        assert milp.domain_intervals([(0, 2), (5, 6)]) == [(0, 2), (5, 6)]


# --------------------------------------------------------------------------- #
# objective                                                                    #
# --------------------------------------------------------------------------- #

class TestObjective:
    def test_weighted_minimum_matches(self):
        def build(opt):
            xs = [opt.NewBoolVar(f"x{i}") for i in range(4)]
            opt.Add(sum(xs) >= 2)
            opt.AddImplication(xs[0], xs[3])
            opt.Minimize(1000 * xs[0] + 100 * xs[1] + 10 * xs[2] + xs[3])
            return {f"x{i}": x for i, x in enumerate(xs)}

        # x2 + x3 is the cheapest pair the implication leaves available.
        assert assert_same_optimum(build, ["x0", "x1", "x2", "x3"]) == 11

    def test_maximize_matches(self):
        def build(opt):
            x = opt.NewIntVar(0, 6, "x")
            y = opt.NewIntVar(0, 6, "y")
            opt.Add(2 * x + 3 * y <= 12)
            opt.Maximize(4 * x + 5 * y)
            return {"x": x, "y": y}

        assert assert_same_optimum(build, ["x", "y"]) == 24

    def test_mixed_sense_weighted_sum(self):
        """The shape the orchestrators build: min terms plus negated max terms."""
        def build(opt):
            share = [opt.NewBoolVar(f"s{i}") for i in range(3)]
            cost = opt.NewIntVar(0, 10, "cost")
            opt.AddMaxEquality(cost, [3 * share[0], 2 * share[1] + 1, 4])
            opt.Add(sum(share) <= 2)
            opt.Minimize(1000 * cost + (-1) * sum(share))
            return {"cost": cost, **{f"s{i}": s for i, s in enumerate(share)}}

        assert_same_optimum(build, ["cost", "s0", "s1", "s2"])

    def test_objective_constant_is_kept(self):
        def build(opt):
            x = opt.NewIntVar(0, 3, "x")
            opt.Minimize(2 * x + 7)
            return {"x": x}

        assert assert_same_optimum(build, ["x"]) == 7

    def test_infeasible_model_reports_infeasible(self):
        def build(opt):
            x = opt.NewIntVar(0, 3, "x")
            opt.Add(x >= 2)
            opt.Add(x <= 1)
            opt.Minimize(x)
            return {"x": x}

        assert assert_same_optimum(build, ["x"]) is None


# --------------------------------------------------------------------------- #
# things that must not change the model                                        #
# --------------------------------------------------------------------------- #

class TestNonModelCalls:
    def test_decision_strategy_is_recorded_not_applied(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        before = len(opt.Proto().constraints)
        opt.AddDecisionStrategy([x], cp_model.CHOOSE_LOWEST_MIN, cp_model.SELECT_MIN_VALUE)
        assert len(opt.Proto().constraints) == before
        assert len(opt.decision_strategies) == 1

    def test_hint_does_not_constrain(self):
        def build(opt):
            x = opt.NewIntVar(0, 3, "x")
            opt.AddHint(x, 2)
            return {"x": x}

        assert assert_same_solutions(build, ["x"]) == {(0,), (1,), (2,), (3,)}

    def test_partial_hints_are_dropped_rather_than_padded(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        opt.NewIntVar(0, 3, "y")
        opt.AddHint(x, 2)
        assert milp.export(opt).initial_solution is None

    def test_complete_hints_become_a_starting_point(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        y = opt.NewIntVar(0, 3, "y")
        opt.AddHint(x, 2)
        opt.AddHint(y, 1)
        assert list(milp.export(opt).initial_solution) == [2.0, 1.0]

    def test_unsupported_constructs_say_so(self):
        opt = CUOPT()
        for call in (
            lambda: opt.AddCircuit([]),
            lambda: opt.AddNoOverlap([]),
            lambda: opt.AddCumulative([], [], 0),
            lambda: opt.NewIntervalVar(0, 1, 1, "i"),
        ):
            with pytest.raises(milp.ModelError, match="cpsat backend"):
                call()


# --------------------------------------------------------------------------- #
# wrapper plumbing                                                             #
# --------------------------------------------------------------------------- #

class TestWrapper:
    def test_proto_counts_grow_with_the_model(self):
        opt = CUOPT()
        assert (len(opt.Proto().variables), len(opt.Proto().constraints)) == (0, 0)
        a = opt.NewBoolVar("a")
        b = opt.NewBoolVar("b")
        opt.AddBoolOr([a, b])
        assert len(opt.Proto().variables) == 2
        assert len(opt.Proto().constraints) == 1

    def test_constraint_log_records_every_call(self, tmp_path):
        logfile = tmp_path / "constraints.log"
        with CUOPT(logfile=str(logfile), cache_limit=1) as opt:
            opt.log_comment("placement")
            a = opt.NewBoolVar("a")
            x = opt.NewIntVar(0, 3, "x")
            opt.Add(x >= 2).OnlyEnforceIf(a)
            opt.Minimize(x)
        text = logfile.read_text()
        assert "[Comment #1] placement" in text
        assert "Adding BoolVar:\tname='a'" in text
        assert "only_enforce_if" in text
        assert "Minimize" in text

    def test_row_senses_agree_with_the_row_bounds(self):
        """The two views of a row the export offers must say the same thing."""
        import math

        opt = CUOPT()
        x = opt.NewIntVar(0, 5, "x")
        y = opt.NewIntVar(0, 5, "y")
        opt.Add(x + y <= 4)
        opt.Add(x - y >= -2)
        opt.Add(x == 3)
        opt.AddLinearConstraint(x + 2 * y, 1, 7)
        exported = milp.export(opt)
        for i in range(exported.num_rows):
            low, high = exported.row_lower[i], exported.row_upper[i]
            sense, rhs = exported.row_types[i], exported.row_rhs[i]
            if sense == b"E":
                assert (low, high) == (rhs, rhs)
            elif sense == b"L":
                assert math.isinf(low) and high == rhs
            else:
                assert low == rhs and math.isinf(high)

    def test_variables_carry_their_names_into_the_proto(self):
        opt = CUOPT()
        opt.NewIntVar(0, 1, "keep_this_name")
        assert opt.Proto().variables[0].name == "keep_this_name"

    def test_backend_selection(self):
        assert isinstance(make_model("cpsat"), CPSAT)
        assert isinstance(make_model("cuopt"), CUOPT)
        assert type(make_solver("cpsat")).__name__ == "CpSolver"
        assert type(make_solver("cuopt")).__name__ == "CuOptSolver"
        with pytest.raises(NotImplementedError, match="not supported"):
            make_model("gurobi")

    def test_cuopt_parameters_come_from_the_cell_config(self):
        solver = make_solver("cuopt", {"cuopt_parameters": {"value": {"time_limit": 5}}})
        assert solver.cuopt_parameters == {"time_limit": 5}
        assert make_solver("cuopt", {}).cuopt_parameters == {}

    def test_solve_without_cuopt_installed_explains_itself(self):
        import importlib.util

        if importlib.util.find_spec("cuopt") is not None:
            pytest.skip("cuopt is installed; the import-error path cannot be exercised")

        from src.cellgen.solver.cuopt_wrapper import CuOptSolver

        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        opt.Add(x >= 1)
        opt.Minimize(x)
        with pytest.raises(ImportError, match="--solver cpsat"):
            CuOptSolver().Solve(opt)

    def test_expressions_are_hashable(self):
        """The engine keeps variables in dicts; comparison operators must not break that."""
        opt = CUOPT()
        a = opt.NewBoolVar("a")
        b = opt.NewBoolVar("b")
        assert len({a, b, a}) == 2
        assert {a: 1}[a] == 1

    def test_a_constraint_has_no_truth_value(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        with pytest.raises(NotImplementedError):
            bool(x == 1)

    def test_non_integer_coefficients_are_refused(self):
        opt = CUOPT()
        x = opt.NewIntVar(0, 3, "x")
        with pytest.raises(milp.ModelError, match="integer"):
            opt.Add(x * 1.5 <= 2)


# --------------------------------------------------------------------------- #
# end to end                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.slow
class TestRealCell:
    """A whole cell, built through the cuOpt path and solved from its rows.

    This is the claim that matters: the model the engine builds for a real cell
    -- every built-in constraint, every objective term, the weighted sum -- has
    the same optimum through the lowering as it does on CP-SAT.
    """

    @pytest.fixture(autouse=True)
    def _repo_cwd(self):
        prev = Path.cwd()
        os.chdir(REPO_ROOT)
        yield
        os.chdir(prev)

    @staticmethod
    def _objective(res_path: Path) -> float:
        return float(res_path.read_text().splitlines()[0].split(":")[1])

    @staticmethod
    def _use_reference_solver(monkeypatch):
        """cuOpt needs a GPU; solve the rows it would receive on the CPU instead.

        Everything else in the cuOpt path is real -- the wrapper, the lowering,
        the export, the status mapping and the result writer.
        """
        from src.cellgen.solver import backends

        real_make_solver = backends.make_solver

        def solver_for(backend, cell_config=None):
            if backend == "cuopt":
                return ReferenceMilpSolver()
            return real_make_solver(backend, cell_config)

        monkeypatch.setattr(backends, "make_solver", solver_for)

    @pytest.mark.parametrize(
        "preset,cell",
        [
            ("FinFET_4T_SH", "INV_X1"),
            ("FinFET_4T_SH", "NAND2_X1"),
            ("CFET_4T_SH", "INV_X1"),
            ("QFET_4T_SH", "INV_X1"),
        ],
    )
    def test_same_optimum_as_cp_sat(self, preset, cell, tmp_path, monkeypatch):
        from src.cellgen.run import run

        caps = ["max_time.value=true", "max_time.time=600"]

        native = tmp_path / "cpsat"
        assert run(preset, [cell], native, overrides=caps) == {cell: "ok"}

        self._use_reference_solver(monkeypatch)
        lowered = tmp_path / "cuopt"
        assert run(preset, [cell], lowered, overrides=caps, solver="cuopt") == {cell: "ok"}

        assert self._objective(lowered / "result" / f"{cell}.res") == \
            self._objective(native / "result" / f"{cell}.res"), (
                f"{preset}/{cell}: the lowered model reached a different optimum"
            )

    def test_run_json_records_the_backend(self, tmp_path, monkeypatch):
        import json

        from src.cellgen.run import run

        self._use_reference_solver(monkeypatch)
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, solver="cuopt",
            overrides=["max_time.value=true", "max_time.time=300"])
        assert json.loads((tmp_path / "run.json").read_text())["solver"] == "cuopt"

    def test_constraint_log_is_written_for_a_real_cell(self, tmp_path, monkeypatch):
        from src.cellgen.run import run

        self._use_reference_solver(monkeypatch)
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, solver="cuopt",
            flag_log_constraints=True,
            overrides=["max_time.value=true", "max_time.time=300"])
        log = (tmp_path / "constraint" / "INV_X1.log").read_text()
        assert "Adding BoolVar" in log
        assert "only_enforce_if" in log
        assert "Adding Objective:\tMinimize(" in log
