"""Making the engine's own constraints switchable, one call site at a time.

Phase 1 gave *new* rules a home. This is the other half: the ~60 constraints
the engine has always applied unconditionally become addressable too, so an
experiment can turn one off and see what it was buying. That is the question
the whole tool exists to answer, and until now it could only be asked by
editing `archit/<TECH>/main.py`.

The call sites change as little as possible::

    plc.diffusion_alignment(self)          ->  apply(self, plc.diffusion_alignment)

Deliberately *not* a manifest-ordered `run_stage()` over the whole list. The
orchestrators are not flat sequences -- there are conditionals
(`if self.use_break_symmetry`), interleaved logging, and an order that took
work to get right. Rewriting them into data would put all of that at risk to
buy an ordering nobody has asked to change. Gating each call in place keeps
the sequence byte-identical when nothing is disabled, which is exactly what
the regression asserts.

A constraint is identified by module and function -- `placement.diffusion_alignment`
-- because that pair is stable, unique across the four core modules, and
greppable back to the source.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

# Ids the current run must skip. Set once per process by the run driver, from
# the manifest; empty means the engine behaves exactly as it always has.
_DISABLED: set[str] = set()


@dataclass
class BuiltinRecord:
    """What one built-in did to the model, for reporting alongside plugins."""

    id: str
    constraints_added: int
    variables_added: int
    skipped: bool = False


_RECORDS: list[BuiltinRecord] = []


def builtin_id(fn) -> str:
    """`placement.diffusion_alignment` for a function in `core/placement.py`."""
    return f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}"


def set_disabled(ids) -> None:
    """Install the set of built-ins this run must skip."""
    global _DISABLED
    _DISABLED = set(ids or ())
    _RECORDS.clear()
    if _DISABLED:
        logger.warning(
            f"Built-in constraints disabled for this run: {sorted(_DISABLED)}"
        )


def disabled() -> set[str]:
    return set(_DISABLED)


def records() -> list[BuiltinRecord]:
    """Per-built-in model deltas from the current build."""
    return list(_RECORDS)


def is_enabled(constraint_id: str) -> bool:
    return constraint_id not in _DISABLED


def apply(inst, fn, *args, **kwargs) -> bool:
    """Run one built-in constraint unless this run has switched it off.

    Returns whether it ran. Measuring the model before and after costs one
    proto access per constraint, which is nothing next to building it, and it
    is what lets the UI say how much of the model each rule is responsible
    for.
    """
    constraint_id = builtin_id(fn)
    if constraint_id in _DISABLED:
        logger.warning(f"\t==\t[builtin:{constraint_id}] SKIPPED (disabled)")
        inst.opt.log_comment(f"[builtin:{constraint_id}] skipped (disabled)")
        _RECORDS.append(BuiltinRecord(constraint_id, 0, 0, skipped=True))
        return False

    proto = inst.opt.Proto()
    before = (len(proto.constraints), len(proto.variables))
    fn(inst, *args, **kwargs)
    proto = inst.opt.Proto()

    _RECORDS.append(BuiltinRecord(
        id=constraint_id,
        constraints_added=len(proto.constraints) - before[0],
        variables_added=len(proto.variables) - before[1],
    ))
    return True


def catalogue() -> list[dict]:
    """Every built-in that can be switched off, read from the core modules.

    Derived rather than hand-listed, so a constraint added to the engine shows
    up in the UI without anyone maintaining a second list of names.
    """
    import inspect

    from src.cellgen.core import pin, placement, routing, rule

    out: list[dict] = []
    for module in (placement, routing, rule, pin):
        short = module.__name__.rsplit(".", 1)[-1]
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            doc = (inspect.getdoc(fn) or "").strip().split("\n")[0]
            out.append({
                "id": f"{short}.{name}",
                "module": short,
                "name": name,
                "description": doc,
                "enabled": is_enabled(f"{short}.{name}"),
            })
    return sorted(out, key=lambda d: d["id"])
