"""Back-compat shim: the parser now lives in :mod:`src.cellgen.io.logparse`."""
from src.cellgen.io.logparse import (  # noqa: F401
    parse_log,
    status_is_ok,
)
