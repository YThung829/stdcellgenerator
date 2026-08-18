"""Exception types raised by the cellgen core.

Previously the solve orchestrators called ``exit(1)`` directly from library
code when CP-SAT returned UNKNOWN or INFEASIBLE. That made the package
impossible to drive in-process (a failed solve killed the caller), so the
worker and the sandbox both had to shell out. The orchestrators now raise
:class:`SolveFailed` instead and ``src/main.py`` maps it back to exit code 1,
so the CLI behaves exactly as before while the package stays importable.
"""


class CellgenError(Exception):
    """Base class for every error raised by cellgen."""


class SolveFailed(CellgenError):
    """CP-SAT finished without a usable solution.

    Attributes:
        status: CP-SAT status name, e.g. ``"INFEASIBLE"`` or ``"UNKNOWN"``.
        cell: Subcircuit name that failed, when known.
    """

    def __init__(self, status: str, cell: str | None = None):
        self.status = status
        self.cell = cell
        where = f" for cell {cell!r}" if cell else ""
        super().__init__(f"CP-SAT solve failed{where}: {status}")
