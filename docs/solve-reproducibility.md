# What is reproducible about a solve (and what isn't)

**Measured on:** 2026-08-18, engine at `9c984bc`, ortools 9.15.6755, FinFET `INV_X1`.

This came out of verifying that the constraint plugin layer is transparent. The
first attempt asserted that loading an inert plugin leaves the solved layout
byte-identical. That test failed — and the reason turned out to have nothing to
do with plugins.

## The measurement

Four identical solves, same process, same config, `deterministic_solve=true`:

| run | objective | layout |
|---|---|---|
| r0 | 1021.0 | unique |
| r1 | 1021.0 | matches r3 |
| r2 | 1021.0 | unique |
| r3 | 1021.0 | matches r1 |

**Objective: identical every time. Geometry: three different answers.**

The objective has many equal-cost optima and the solver reaches them
nondeterministically. It is not a warm-up effect — the matching pair is r1/r3,
not r1/r2/r3.

## Neither obvious knob fixes it

- **`num_search_workers=1` is silently ignored.** `model_preset` 2 — the default —
  contains `num_search_workers = max(self.solver.parameters.num_search_workers, 8)`
  ([FinFET/main.py:652](../engine/src/cellgen/archit/FinFET/main.py)). Setting it
  to 1 in the cell config has no effect at all.
- **`deterministic_solve=true` does not make repeated solves layout-identical.**
  Its docstring promises "lex-ordered objective hierarchy for unique minimizer
  across num_search_workers > 1"; in practice the four runs above had it enabled.

Separate processes running the exact same command *did* reproduce byte-identically
several times, so this is not chaotic — but it is not something to rely on.

## Consequences

**For tests.** Assert on the objective value, never on placement/routing
geometry. `tests/test_plugins.py` proves plugin transparency by comparing
objectives; `tests/test_smoke.py::TestSolveReproducibility` pins this behaviour
so a future change to it is visible.

**For the experiment UI (Tab 2).** This is the important one:

- Comparing two runs by **metrics** (objective, CPP cost, walltime, status) is
  sound.
- Comparing two runs by **layout geometry** is not. Two runs of the *same*
  bundle can produce different-looking layouts at identical cost, so a visual
  diff would show spurious differences and invite wrong conclusions.
- Layout PNGs are for inspecting one result, not for diffing two.

If layout-level comparison is genuinely needed later, it requires an engine
change (a full tie-breaking order over the placement and routing variables),
not a config flag.

## Reproducing

```bash
cd engine && python -m pytest tests/test_smoke.py::TestSolveReproducibility -v
```
