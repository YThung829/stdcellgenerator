"""The CP-SAT constructs this engine uses, lowered to integer linear rows.

The engine builds its model through a small, fixed slice of the CP-SAT API
(``NewBoolVar``, ``Add``, ``OnlyEnforceIf``, ``AddBoolOr`` and friends). This
module reimplements exactly that slice on top of plain linear rows, so the same
model-building code can be handed to a MILP solver -- cuOpt in particular --
without a single constraint being rewritten at the call sites.

Every lowering here is *exact*: the set of assignments to the original
variables admitted by the rows is the same set CP-SAT admits, and the objective
is the same integer expression. Nothing is relaxed, approximated or dropped.
The one construct with no linear meaning at all is ``AddDecisionStrategy``,
which is a search heuristic rather than a constraint; it is recorded and
ignored. ``tests/test_cuopt_backend.py`` proves the equivalence construct by
construct by enumerating both models.

The lowerings, for reference:

``x != k``
    one binary ``d`` and two rows, ``x <= k-1`` when ``d`` and ``x >= k+1``
    when not ``d``; each relaxed by exactly the range of the row's own
    expression, so the inactive branch is vacuous rather than merely loose.
``.OnlyEnforceIf(l1, ..., lk)``
    each row of the constraint is relaxed by ``M * (k - sum(li))``, where the
    sum is ``k`` iff every literal holds and ``M`` is the row's maximum
    possible violation. One-directional, exactly like CP-SAT.
``NewIntVarFromDomain`` over a domain with holes
    one binary per interval, ``sum == 1``, and the variable pinned between the
    selected interval's own bounds. No big-M.
``AddMaxEquality`` / ``AddMinEquality``
    ``target >= e`` for every ``e``, plus one binary per ``e`` selecting which
    one it is equal to.
``AddAllDifferent``
    a value-assignment encoding when the domains are small enough to enumerate
    (tight, and the usual case here), pairwise ``!=`` otherwise.
``AddMultiplicationEquality``
    the standard linearisation of a conjunction of literals, extended to one
    integer factor times a conjunction.

Variables are integer everywhere, exactly as in CP-SAT: nothing is relaxed to a
continuous variable, so a feasible point of these rows is a feasible point of
the original model and vice versa.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

INFINITY = float("inf")

# Row senses, in the letters the cuOpt data model uses.
LE, GE, EQ = "L", "G", "E"

# AddAllDifferent switches to pairwise `!=` once enumerating the domains would
# cost more than this many (variable, value) binaries. The value-assignment
# encoding is much tighter, so the limit is deliberately generous -- the
# engine's placement columns and packed coordinates land far below it.
ALL_DIFFERENT_ASSIGNMENT_LIMIT = 20_000


class ModelError(ValueError):
    """A model-building call the MILP backend cannot represent."""


# --------------------------------------------------------------------------- #
# expressions                                                                  #
# --------------------------------------------------------------------------- #

def _as_int(value, what="coefficient"):
    """Coerce to int, rejecting anything CP-SAT would also reject."""
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return int(value)
    raise ModelError(f"{what} must be an integer, got {value!r}")


def _as_linear(obj) -> tuple[dict, int]:
    """Flatten anything usable in a linear expression to ``(coeffs, const)``."""
    if isinstance(obj, _Expr):
        return obj._linear()
    return {}, _as_int(obj, "constant")


def _combine(left, right, sign):
    lc, lk = _as_linear(left)
    rc, rk = _as_linear(right)
    coeffs = dict(lc)
    for var, c in rc.items():
        merged = coeffs.get(var, 0) + sign * c
        if merged:
            coeffs[var] = merged
        else:
            coeffs.pop(var, None)
    return LinearExpr(coeffs, lk + sign * rk)


class _Expr:
    """Anything that flattens to ``{Var: coefficient}`` plus a constant."""

    __slots__ = ()

    def _linear(self) -> tuple[dict, int]:
        raise NotImplementedError

    # --- arithmetic ---
    def __add__(self, other):
        return _combine(self, other, 1)

    # `sum(vars)` starts at 0, so addition has to work from the right too.
    __radd__ = __add__

    def __sub__(self, other):
        return _combine(self, other, -1)

    def __rsub__(self, other):
        return _combine(other, self, -1)

    def __neg__(self):
        return self * -1

    def __pos__(self):
        return self

    def __mul__(self, factor):
        k = _as_int(factor)
        coeffs, const = self._linear()
        return LinearExpr({v: c * k for v, c in coeffs.items() if c * k}, const * k)

    __rmul__ = __mul__

    # --- comparisons build constraints, exactly as cp_model does ---
    def __le__(self, other):
        return BoundedExpr(self, other, "<=")

    def __ge__(self, other):
        return BoundedExpr(self, other, ">=")

    def __lt__(self, other):
        return BoundedExpr(self, other, "<")

    def __gt__(self, other):
        return BoundedExpr(self, other, ">")

    def __eq__(self, other):
        return BoundedExpr(self, other, "==")

    def __ne__(self, other):
        return BoundedExpr(self, other, "!=")

    # Defining __eq__ would otherwise drop __hash__, and the engine keeps
    # variables in dicts. Identity hashing, same as cp_model.IntVar.
    __hash__ = object.__hash__


class Var(_Expr):
    """An integer variable. ``intervals`` is its domain, as merged ranges."""

    __slots__ = ("index", "name", "lb", "ub", "intervals", "_negation")

    def __init__(self, index: int, name: str, lb: int, ub: int, intervals=None):
        self.index = index
        self.name = name
        self.lb = lb
        self.ub = ub
        self.intervals = tuple(intervals) if intervals else ((lb, ub),)
        self._negation = None

    @property
    def is_boolean(self) -> bool:
        return self.lb >= 0 and self.ub <= 1

    def Not(self):
        if not self.is_boolean:
            raise ModelError(f"Not() needs a boolean variable, {self.name} is [{self.lb}, {self.ub}]")
        if self._negation is None:
            self._negation = NegatedVar(self)
        return self._negation

    def _linear(self):
        return {self: 1}, 0

    # cp_model.IntVar's accessors. The engine reads variable names back out of
    # the model when it writes results, so these are part of the contract.
    def Name(self) -> str:
        return self.name

    def Index(self) -> int:
        return self.index

    def Proto(self) -> "_VarProto":
        return _VarProto(self.name, [b for interval in self.intervals for b in interval])

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"Var({self.name!r}, [{self.lb}, {self.ub}])"

    __hash__ = object.__hash__


class NegatedVar(_Expr):
    """``1 - var``, and the literal CP-SAT calls ``var.Not()``."""

    __slots__ = ("var",)

    def __init__(self, var: Var):
        self.var = var

    def Not(self):
        return self.var

    @property
    def is_boolean(self) -> bool:
        return True

    def _linear(self):
        return {self.var: -1}, 1

    def Name(self) -> str:
        return f"not({self.var.name})"

    def Index(self) -> int:
        """CP-SAT's encoding for a negated literal."""
        return -self.var.index - 1

    def Proto(self) -> "_VarProto":
        return _VarProto(self.Name(), [0, 1])

    def __str__(self):
        return f"not({self.var.name})"

    __repr__ = __str__

    __hash__ = object.__hash__


class _VarProto:
    """Stand-in for ``IntegerVariableProto``: a name and a flattened domain."""

    __slots__ = ("name", "domain")

    def __init__(self, name: str, domain: list[int]):
        self.name = name
        self.domain = domain


class LinearExpr(_Expr):
    __slots__ = ("coeffs", "const")

    def __init__(self, coeffs: dict, const: int = 0):
        self.coeffs = coeffs
        self.const = const

    def _linear(self):
        return self.coeffs, self.const

    def __str__(self):
        if not self.coeffs:
            return str(self.const)
        parts = []
        for var, c in self.coeffs.items():
            if c == 1:
                parts.append(f"+ {var.name}")
            elif c == -1:
                parts.append(f"- {var.name}")
            elif c < 0:
                parts.append(f"- {-c}*{var.name}")
            else:
                parts.append(f"+ {c}*{var.name}")
        if self.const > 0:
            parts.append(f"+ {self.const}")
        elif self.const < 0:
            parts.append(f"- {-self.const}")
        text = " ".join(parts)
        return text[2:] if text.startswith("+ ") else text

    __repr__ = __str__


class BoundedExpr:
    """``coeffs . x <op> rhs``, with everything moved to the left-hand side."""

    __slots__ = ("coeffs", "op", "rhs")

    def __init__(self, left, right, op):
        lc, lk = _as_linear(left)
        rc, rk = _as_linear(right)
        coeffs = dict(lc)
        for var, c in rc.items():
            merged = coeffs.get(var, 0) - c
            if merged:
                coeffs[var] = merged
            else:
                coeffs.pop(var, None)
        rhs = rk - lk
        # Every variable and coefficient is integral, so a strict bound is just
        # the next integer -- the same rewrite CP-SAT applies.
        if op == "<":
            op, rhs = "<=", rhs - 1
        elif op == ">":
            op, rhs = ">=", rhs + 1
        self.coeffs = coeffs
        self.op = op
        self.rhs = rhs

    def __bool__(self):
        raise NotImplementedError(
            "a constraint has no truth value; pass it to Add() instead"
        )

    def __str__(self):
        return f"{LinearExpr(self.coeffs, 0)} {self.op} {self.rhs}"

    __repr__ = __str__


def expr_bounds(coeffs: dict, const: int = 0) -> tuple[int, int]:
    """Interval hull of ``coeffs . x + const`` over the variables' domains."""
    lo = hi = const
    for var, c in coeffs.items():
        if c > 0:
            lo += c * var.lb
            hi += c * var.ub
        else:
            lo += c * var.ub
            hi += c * var.lb
    return lo, hi


def _merge(base: dict, extra: dict, scale: int) -> dict:
    out = dict(base)
    for var, c in extra.items():
        merged = out.get(var, 0) + scale * c
        if merged:
            out[var] = merged
        else:
            out.pop(var, None)
    return out


def evaluate(expr, values) -> int:
    """Value of ``expr`` under ``values`` (indexed by ``Var.index``)."""
    coeffs, const = _as_linear(expr)
    total = const
    for var, c in coeffs.items():
        total += c * values[var.index]
    return total


# --------------------------------------------------------------------------- #
# rows                                                                         #
# --------------------------------------------------------------------------- #

class Row:
    """One linear row, plus the literals that switch it on.

    Enforcement is applied at materialisation rather than when
    ``OnlyEnforceIf`` is called, so the literals can accumulate across repeated
    calls the way CP-SAT accumulates them.
    """

    __slots__ = ("coeffs", "sense", "rhs", "enforce")

    def __init__(self, coeffs: dict, sense: str, rhs: int):
        self.coeffs = coeffs
        self.sense = sense
        self.rhs = rhs
        self.enforce: list = []

    def materialize(self) -> list[tuple[dict, float, float]]:
        """Expand to ``(coeffs, lower, upper)`` rows ready for the solver."""
        if not self.enforce:
            if self.sense == LE:
                return [(self.coeffs, -INFINITY, self.rhs)]
            if self.sense == GE:
                return [(self.coeffs, self.rhs, INFINITY)]
            return [(self.coeffs, self.rhs, self.rhs)]

        # sum(literals) == k iff every literal holds, so `k - sum(literals)`
        # is 0 when the constraint is on and >= 1 when it is off.
        k = len(self.enforce)
        lit_coeffs: dict = {}
        lit_const = 0
        for literal in self.enforce:
            lc, lk = _as_linear(literal)
            for var, c in lc.items():
                merged = lit_coeffs.get(var, 0) + c
                if merged:
                    lit_coeffs[var] = merged
                else:
                    lit_coeffs.pop(var, None)
            lit_const += lk

        out = []
        senses = (LE, GE) if self.sense == EQ else (self.sense,)
        lo_e, hi_e = expr_bounds(self.coeffs)
        for sense in senses:
            if sense == LE:
                # coeffs.x <= rhs + M*(k - lit) with M the largest violation
                big_m = max(0, hi_e - self.rhs)
                merged = _merge(self.coeffs, lit_coeffs, big_m)
                out.append((merged, -INFINITY, self.rhs + big_m * (k - lit_const)))
            else:
                big_m = max(0, self.rhs - lo_e)
                merged = _merge(self.coeffs, lit_coeffs, -big_m)
                out.append((merged, self.rhs - big_m * (k - lit_const), INFINITY))
        return out


def _flatten_literals(args) -> list:
    """``OnlyEnforceIf`` takes literals as varargs, as a list, or as both."""
    out = []
    for arg in args:
        if isinstance(arg, _Expr):
            out.append(arg)
        elif isinstance(arg, Iterable):
            out.extend(_flatten_literals(tuple(arg)))
        else:
            raise ModelError(f"not a literal: {arg!r}")
    return out


class Constraint:
    """Handle over the rows one model-building call produced."""

    __slots__ = ("rows", "_on_enforce")

    def __init__(self, rows: list[Row]):
        self.rows = rows
        self._on_enforce = None

    def only_enforce_if(self, *literals) -> "Constraint":
        lits = _flatten_literals(literals)
        for row in self.rows:
            row.enforce.extend(lits)
        if self._on_enforce is not None:
            self._on_enforce(lits)
        return self

    OnlyEnforceIf = only_enforce_if


# --------------------------------------------------------------------------- #
# the model                                                                    #
# --------------------------------------------------------------------------- #

class MilpModel:
    """CP-SAT's model-building surface, backed by linear rows.

    Method names and semantics mirror ``cp_model.CpModel`` so the engine's
    constraint code runs against either backend unchanged.
    """

    def __init__(self):
        self._vars: list[Var] = []
        self._rows: list[Row] = []
        self._objective = LinearExpr({}, 0)
        self._maximize = False
        self._hints: dict[Var, int] = {}
        self._constants: dict[int, Var] = {}
        self._aux_count = 0
        self.decision_strategies: list[tuple] = []
        self._proto = _ModelProto(self._vars, self._rows)

    # --- variables ----------------------------------------------------------
    def _new_var(self, lb: int, ub: int, name: str, intervals=None) -> Var:
        if ub < lb:
            raise ModelError(f"variable {name!r} has an empty domain [{lb}, {ub}]")
        var = Var(len(self._vars), name, lb, ub, intervals)
        self._vars.append(var)
        return var

    def _aux(self, prefix: str, lb: int = 0, ub: int = 1) -> Var:
        self._aux_count += 1
        return self._new_var(lb, ub, f"__{prefix}_{self._aux_count}")

    def NewIntVar(self, lb: int, ub: int, name: str) -> Var:
        return self._new_var(_as_int(lb, "bound"), _as_int(ub, "bound"), name)

    def NewBoolVar(self, name: str) -> Var:
        return self._new_var(0, 1, name)

    def NewConstant(self, value: int) -> Var:
        value = _as_int(value, "constant")
        var = self._constants.get(value)
        if var is None:
            var = self._new_var(value, value, f"__const_{value}")
            self._constants[value] = var
        return var

    def NewIntVarFromDomain(self, domain, name: str) -> Var:
        """A variable over a possibly non-contiguous domain.

        A domain with holes gets one binary per interval and is pinned between
        the selected interval's own bounds -- exact, and with no big-M.
        """
        intervals = domain_intervals(domain)
        if not intervals:
            raise ModelError(f"variable {name!r} has an empty domain")
        var = self._new_var(intervals[0][0], intervals[-1][1], name, intervals)
        if len(intervals) == 1:
            return var

        selectors = [self._aux(f"dom_{name}") for _ in intervals]
        self._rows.append(Row({s: 1 for s in selectors}, EQ, 1))
        # var >= sum(lo_j * s_j) and var <= sum(hi_j * s_j); exactly one s_j is
        # 1, so this is `lo_selected <= var <= hi_selected`.
        low = {var: 1}
        high = {var: 1}
        for (lo, hi), sel in zip(intervals, selectors):
            if lo:
                low[sel] = low.get(sel, 0) - lo
            if hi:
                high[sel] = high.get(sel, 0) - hi
        self._rows.append(Row({v: c for v, c in low.items() if c}, GE, 0))
        self._rows.append(Row({v: c for v, c in high.items() if c}, LE, 0))
        return var

    # --- constraints --------------------------------------------------------
    def _add(self, rows: list[Row]) -> Constraint:
        self._rows.extend(rows)
        return Constraint(rows)

    def Add(self, ct) -> Constraint:
        """A linear constraint. ``ct`` may also be a plain bool, as in CP-SAT."""
        if isinstance(ct, (bool, np.bool_)):
            # `Add(sum(empty) == 0)` collapses to True before it reaches here.
            return self._add([Row({}, GE, 0 if ct else 1)])
        if not isinstance(ct, BoundedExpr):
            raise ModelError(f"Add() expects a linear constraint, got {ct!r}")
        if ct.op == "!=":
            return self._add(self._not_equal_rows(ct.coeffs, ct.rhs))
        sense = {"<=": LE, ">=": GE, "==": EQ}[ct.op]
        return self._add([Row(dict(ct.coeffs), sense, ct.rhs)])

    def _not_equal_rows(self, coeffs: dict, rhs: int) -> list[Row]:
        """``expr != rhs`` as the disjunction ``expr <= rhs-1 or expr >= rhs+1``."""
        lo, hi = expr_bounds(coeffs)
        branch = self._aux("ne")
        # branch == 1 selects the low side, branch == 0 the high side; each
        # slack is exactly the range of its own row, so the unselected side is
        # vacuous rather than merely loose.
        low_slack = max(0, hi - (rhs - 1))
        high_slack = max(0, (rhs + 1) - lo)
        rows = [
            Row(_merge(coeffs, {branch: 1}, low_slack), LE, rhs - 1 + low_slack),
            Row(_merge(coeffs, {branch: 1}, high_slack), GE, rhs + 1),
        ]
        return rows

    def AddLinearConstraint(self, expr, lb: int, ub: int) -> Constraint:
        coeffs, const = _as_linear(expr)
        return self._add([
            Row(dict(coeffs), GE, _as_int(lb, "bound") - const),
            Row(dict(coeffs), LE, _as_int(ub, "bound") - const),
        ])

    def AddImplication(self, a, b) -> Constraint:
        """``a => b``, i.e. ``lin(a) - lin(b) <= 0``."""
        ac, ak = _literal(a)
        bc, bk = _literal(b)
        return self._add([Row(_merge(ac, bc, -1), LE, bk - ak)])

    def AddBoolOr(self, literals) -> Constraint:
        coeffs, const = _literal_sum(literals)
        return self._add([Row(coeffs, GE, 1 - const)])

    def AddBoolAnd(self, literals) -> Constraint:
        """Every literal holds; each is at most 1, so their sum reaching n is that."""
        lits = list(literals)
        coeffs, const = _literal_sum(lits)
        return self._add([Row(coeffs, GE, len(lits) - const)])

    def AddAtMostOne(self, literals) -> Constraint:
        coeffs, const = _literal_sum(literals)
        return self._add([Row(coeffs, LE, 1 - const)])

    def AddExactlyOne(self, literals) -> Constraint:
        coeffs, const = _literal_sum(literals)
        return self._add([Row(coeffs, EQ, 1 - const)])

    def AddMaxEquality(self, target, exprs) -> Constraint:
        return self._add(self._extremum_rows(target, exprs, maximum=True))

    def AddMinEquality(self, target, exprs) -> Constraint:
        return self._add(self._extremum_rows(target, exprs, maximum=False))

    def _extremum_rows(self, target, exprs, maximum: bool) -> list[Row]:
        exprs = list(exprs)
        if not exprs:
            raise ModelError("Add{Max,Min}Equality needs at least one expression")
        tc, tk = _as_linear(target)
        rows: list[Row] = []
        selectors = []
        for expr in exprs:
            ec, ek = _as_linear(expr)
            diff = _merge(tc, ec, -1)          # target - expr
            const = tk - ek
            lo, hi = expr_bounds(diff, const)
            sel = self._aux("max" if maximum else "min")
            selectors.append(sel)
            if maximum:
                rows.append(Row(dict(diff), GE, -const))            # target >= expr
                slack = max(0, hi)
                rows.append(Row(_merge(diff, {sel: 1}, slack), LE, slack - const))
            else:
                rows.append(Row(dict(diff), LE, -const))             # target <= expr
                slack = max(0, -lo)
                rows.append(Row(_merge(diff, {sel: 1}, -slack), GE, -slack - const))
        rows.append(Row({s: 1 for s in selectors}, EQ, 1))
        return rows

    def AddAllDifferent(self, variables) -> Constraint:
        variables = list(variables)
        if len(variables) < 2:
            return self._add([])
        domains = _enumerable_domains(variables, ALL_DIFFERENT_ASSIGNMENT_LIMIT)
        if domains is None:
            return self._add(self._all_different_pairwise(variables))
        return self._add(self._all_different_assignment(variables, domains))

    def _all_different_pairwise(self, variables) -> list[Row]:
        rows: list[Row] = []
        for i, first in enumerate(variables):
            fc, fk = _as_linear(first)
            for second in variables[i + 1:]:
                sc, sk = _as_linear(second)
                rows.extend(self._not_equal_rows(_merge(fc, sc, -1), sk - fk))
        return rows

    def _all_different_assignment(self, variables, domains) -> list[Row]:
        """One binary per (variable, value); each value used at most once."""
        rows: list[Row] = []
        by_value: dict[int, list[Var]] = {}
        for var, values in zip(variables, domains):
            picks = {}
            for value in values:
                pick = self._aux(f"alldiff_{var.name}_{value}")
                picks[value] = pick
                by_value.setdefault(value, []).append(pick)
            rows.append(Row({p: 1 for p in picks.values()}, EQ, 1))
            coeffs = {var: 1}
            for value, pick in picks.items():
                if value:
                    coeffs[pick] = coeffs.get(pick, 0) - value
            rows.append(Row({v: c for v, c in coeffs.items() if c}, EQ, 0))
        for picks in by_value.values():
            if len(picks) > 1:
                rows.append(Row({p: 1 for p in picks}, LE, 1))
        return rows

    def AddMultiplicationEquality(self, target, factors) -> Constraint:
        factors = list(factors)
        if not factors:
            raise ModelError("AddMultiplicationEquality needs at least one factor")
        literals = [f for f in factors if _is_literal(f)]
        others = [f for f in factors if not _is_literal(f)]
        if not others:
            return self._add(self._conjunction_rows(target, literals))
        if len(others) > 1:
            raise ModelError(
                "AddMultiplicationEquality can only linearise a product of "
                "boolean literals, optionally times one integer expression; "
                f"got {len(others)} non-boolean factors"
            )
        return self._add(self._gated_value_rows(target, others[0], literals))

    def _conjunction_rows(self, target, literals) -> list[Row]:
        """``target == l1 and ... and ln`` for a boolean target."""
        tc, tk = _as_linear(target)
        rows = [Row(dict(tc), GE, -tk)]                     # target >= 0
        for literal in literals:
            lc, lk = _literal(literal)
            rows.append(Row(_merge(tc, lc, -1), LE, lk - tk))  # target <= literal
        sum_c, sum_k = _literal_sum(literals)
        # target >= sum(literals) - (n - 1)
        rows.append(Row(_merge(tc, sum_c, -1), GE, sum_k - (len(literals) - 1) - tk))
        return rows

    def _gated_value_rows(self, target, value, literals) -> list[Row]:
        """``target == value * l1 * ... * ln`` for an integer ``value``."""
        rows: list[Row] = []
        if len(literals) == 1:
            gate = literals[0]
        elif not literals:
            gate = None
        else:
            # Collapse the literals to one gate first. Its rows go in the same
            # list, so an OnlyEnforceIf on this constraint relaxes all of it.
            gate = self._aux("gate")
            rows.extend(self._conjunction_rows(gate, literals))

        tc, tk = _as_linear(target)
        vc, vk = _as_linear(value)
        lo, hi = expr_bounds(vc, vk)
        if gate is None:
            rows.append(Row(_merge(tc, vc, -1), EQ, vk - tk))
            return rows
        gc, gk = _literal(gate)
        rows.extend([
            # 0 (or lo) <= target <= hi when the gate is on, target == 0 when off
            Row(_merge(tc, gc, -hi), LE, hi * gk - tk),
            Row(_merge(tc, gc, -lo), GE, lo * gk - tk),
            # target == value when the gate is on
            Row(_merge(_merge(tc, vc, -1), gc, -lo), LE, -lo * (1 - gk) - tk + vk),
            Row(_merge(_merge(tc, vc, -1), gc, -hi), GE, -hi * (1 - gk) - tk + vk),
        ])
        return rows

    # --- search hints (no effect on the feasible set) -----------------------
    def AddDecisionStrategy(self, variables, var_strategy, domain_strategy):
        """Recorded only: branching order is not part of a MILP model."""
        self.decision_strategies.append((list(variables), var_strategy, domain_strategy))
        return None

    def AddHint(self, var: Var, value: int):
        self._hints[var] = _as_int(value, "hint")
        return None

    # --- objective ----------------------------------------------------------
    def Minimize(self, expr):
        coeffs, const = _as_linear(expr)
        self._objective = LinearExpr(dict(coeffs), const)
        self._maximize = False

    def Maximize(self, expr):
        coeffs, const = _as_linear(expr)
        self._objective = LinearExpr(dict(coeffs), const)
        self._maximize = True

    @property
    def variables(self) -> list[Var]:
        """Every variable, in the order the solver's solution vector uses."""
        return self._vars

    @property
    def objective(self) -> LinearExpr:
        return self._objective

    @property
    def maximize(self) -> bool:
        return self._maximize

    # --- introspection ------------------------------------------------------
    def Proto(self):
        """A stand-in for ``CpModelProto``: variable names and a row count.

        The engine only ever reads ``len(proto.variables)``,
        ``len(proto.constraints)`` and each variable's ``.name``; this view is
        live, so those stay correct as the model grows.
        """
        return self._proto

    # --- unsupported CP-SAT constructs --------------------------------------
    def NewIntervalVar(self, start, size, end, name):
        raise ModelError(_UNSUPPORTED.format(what="NewIntervalVar"))

    def AddNoOverlap(self, interval_vars):
        raise ModelError(_UNSUPPORTED.format(what="AddNoOverlap"))

    def AddCumulative(self, intervals, demands, capacity):
        raise ModelError(_UNSUPPORTED.format(what="AddCumulative"))

    def AddCircuit(self, arcs):
        raise ModelError(_UNSUPPORTED.format(what="AddCircuit"))


_UNSUPPORTED = (
    "{what} has no linear equivalent and the MILP backend does not implement "
    "one. No built-in constraint uses it; a plugin that needs it has to run on "
    "the cpsat backend."
)


class _ModelProto:
    """Live view of a model's variables and rows, shaped like ``CpModelProto``."""

    __slots__ = ("variables", "constraints")

    def __init__(self, variables, constraints):
        self.variables = variables
        self.constraints = constraints


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _is_literal(obj) -> bool:
    if isinstance(obj, NegatedVar):
        return True
    return isinstance(obj, Var) and obj.is_boolean


def _literal(obj) -> tuple[dict, int]:
    """Flatten a literal, rejecting anything that is not 0/1-valued."""
    if isinstance(obj, (bool, np.bool_)):
        return {}, int(obj)
    if not _is_literal(obj):
        raise ModelError(f"expected a boolean literal, got {obj!r}")
    return obj._linear()


def _literal_sum(literals) -> tuple[dict, int]:
    coeffs: dict = {}
    const = 0
    for literal in literals:
        lc, lk = _literal(literal)
        for var, c in lc.items():
            merged = coeffs.get(var, 0) + c
            if merged:
                coeffs[var] = merged
            else:
                coeffs.pop(var, None)
        const += lk
    return coeffs, const


def domain_intervals(domain) -> list[tuple[int, int]]:
    """Normalise a domain to sorted, merged, inclusive intervals.

    Accepts an ``ortools`` ``Domain`` (the engine builds them with
    ``cp_model.Domain.FromValues``), a flat list of values, or a list of
    ``(lo, hi)`` pairs.
    """
    flat = None
    if hasattr(domain, "FlattenedIntervals"):
        flat = list(domain.FlattenedIntervals())
    elif hasattr(domain, "flattened_intervals"):
        flat = list(domain.flattened_intervals())
    if flat is not None:
        return [(int(flat[i]), int(flat[i + 1])) for i in range(0, len(flat), 2)]

    values: list[int] = []
    for item in domain:
        if isinstance(item, (tuple, list)):
            lo, hi = int(item[0]), int(item[1])
            values.extend(range(lo, hi + 1))
        else:
            values.append(int(item))
    if not values:
        return []
    intervals = []
    for value in sorted(set(values)):
        if intervals and value == intervals[-1][1] + 1:
            intervals[-1][1] = value
        else:
            intervals.append([value, value])
    return [(lo, hi) for lo, hi in intervals]


def _enumerable_domains(variables, budget: int):
    """Every variable's domain as a value list, or None if that is too big."""
    domains = []
    for var in variables:
        if not isinstance(var, Var):
            return None
        values = []
        for lo, hi in var.intervals:
            span = hi - lo + 1
            budget -= span
            if budget < 0:
                return None
            values.extend(range(lo, hi + 1))
        domains.append(values)
    return domains


# --------------------------------------------------------------------------- #
# export                                                                       #
# --------------------------------------------------------------------------- #

class ExportedModel:
    """A model flattened into the arrays a MILP solver wants.

    ``infeasible`` is set when a row is unsatisfiable on its own -- ``Add(False)``
    with no enforcement literals. There is nothing to hand a solver in that
    case, and saying so up front beats spending GPU time rediscovering it.
    """

    __slots__ = (
        "num_variables", "variable_lower", "variable_upper", "variable_names",
        "matrix_values", "matrix_indices", "matrix_offsets",
        "row_lower", "row_upper", "row_types", "row_rhs", "num_rows",
        "objective", "objective_offset", "maximize", "initial_solution",
        "infeasible",
    )


def export(model: MilpModel) -> ExportedModel:
    """Flatten ``model`` to CSR arrays plus bounds, ready for cuOpt."""
    values: list[float] = []
    indices: list[int] = []
    offsets: list[int] = [0]
    row_lower: list[float] = []
    row_upper: list[float] = []
    infeasible = False

    def emit(terms, lo, hi):
        for index, coefficient in terms:
            indices.append(index)
            values.append(float(coefficient))
        offsets.append(len(indices))
        row_lower.append(lo)
        row_upper.append(hi)

    for row in model._rows:
        for coeffs, lo, hi in row.materialize():
            terms = sorted(
                ((var.index, c) for var, c in coeffs.items() if c),
                key=lambda item: item[0],
            )
            if not terms:
                # A row with no variables left is either trivially true or a
                # flat contradiction; neither belongs in the matrix.
                if not (lo <= 0 <= hi):
                    infeasible = True
                continue
            if lo != hi and lo != -INFINITY and hi != INFINITY:
                # Nothing produces a two-sided row today, but keeping every
                # emitted row one-sided or an equality is what lets the export
                # state a sense per row -- the shape cuOpt's own model builder
                # uses.
                emit(terms, lo, INFINITY)
                emit(terms, -INFINITY, hi)
            else:
                emit(terms, lo, hi)

    num_vars = len(model._vars)
    out = ExportedModel()
    out.num_variables = num_vars
    out.variable_lower = np.array([v.lb for v in model._vars], dtype=np.float64)
    out.variable_upper = np.array([v.ub for v in model._vars], dtype=np.float64)
    out.variable_names = [v.name for v in model._vars]
    out.matrix_values = np.array(values, dtype=np.float64)
    out.matrix_indices = np.array(indices, dtype=np.int32)
    out.matrix_offsets = np.array(offsets, dtype=np.int32)
    out.row_lower = np.array(row_lower, dtype=np.float64)
    out.row_upper = np.array(row_upper, dtype=np.float64)
    out.num_rows = len(row_lower)

    # The same rows as a sense plus one right-hand side: 'E' both bounds, 'L'
    # upper only, 'G' lower only. Every emitted row is one of the three.
    equality = out.row_lower == out.row_upper
    upper_only = np.isneginf(out.row_lower)
    out.row_types = np.where(equality, b"E", np.where(upper_only, b"L", b"G")).astype("S1")
    out.row_rhs = np.where(upper_only, out.row_upper, out.row_lower)

    objective = np.zeros(num_vars, dtype=np.float64)
    for var, c in model.objective.coeffs.items():
        objective[var.index] = float(c)
    out.objective = objective
    out.objective_offset = float(model.objective.const)
    out.maximize = model.maximize
    out.infeasible = infeasible

    # cuOpt takes a complete starting point, CP-SAT takes any subset. A partial
    # hint padded with made-up values is worse than none, so it is only passed
    # through when every variable carries one.
    if model._hints and len(model._hints) == num_vars:
        start = np.zeros(num_vars, dtype=np.float64)
        for var, value in model._hints.items():
            start[var.index] = float(value)
        out.initial_solution = start
    else:
        out.initial_solution = None
    return out


def violations(model: MilpModel, values) -> list[str]:
    """Rows of ``model`` that ``values`` does not satisfy, as readable text.

    Everything is integral, so this is an exact check rather than a tolerance
    test: a solver result that fails it is not a solution to the original model.
    """
    bad = []
    for row in model._rows:
        for coeffs, lo, hi in row.materialize():
            total = sum(c * values[var.index] for var, c in coeffs.items())
            if (lo != -INFINITY and total < lo) or (hi != INFINITY and total > hi):
                bad.append(f"{LinearExpr(coeffs, 0)} = {total} not in [{lo}, {hi}]")
    return bad
