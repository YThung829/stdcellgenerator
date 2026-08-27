"""Buffered constraint log shared by the solver backends.

``flag_log_constraints`` mirrors every model-building call into
``constraint/<cell>.log``. The file runs to hundreds of megabytes on a real
cell, so writes are batched rather than flushed per line.

``cpsat_wrapper.CPSAT`` still carries its own copy of this logic; it predates
this module and is deliberately left untouched so the CP-SAT path is bit-for-bit
what it always was. New backends use this.
"""

from __future__ import annotations

from loguru import logger


class OperationLog:
    """Numbered log of model-building operations, written in batches."""

    def __init__(self, logfile: str | None = None, cache_limit: int = 10_000, banner: str = ""):
        self._logfile = logfile
        self._cache_limit = cache_limit
        self._cache: list[str] = []
        self.operation_count = 0
        self.comment_count = 0

        if self._logfile:
            with open(self._logfile, "w") as handle:
                handle.write(f"{banner}\n")

    @property
    def enabled(self) -> bool:
        return self._logfile is not None

    def operation(self, operation_type: str, details: str) -> None:
        self.operation_count += 1
        if not self._logfile:
            return
        self._cache.append(
            f"[CP #{self.operation_count}] Adding {operation_type}:\t{details}"
        )
        if len(self._cache) >= self._cache_limit:
            self.flush()

    def comment(self, text: str) -> None:
        self.comment_count += 1
        if not self._logfile:
            return
        self._cache.extend(["", f"[Comment #{self.comment_count}] {text}"])
        if len(self._cache) >= self._cache_limit:
            self.flush()

    def flush(self) -> None:
        if not self._logfile or not self._cache:
            return
        try:
            with open(self._logfile, "a") as handle:
                handle.write("\n".join(self._cache) + "\n")
            self._cache.clear()
        except IOError as exc:
            logger.error(f"Error flushing log cache: {exc}")
