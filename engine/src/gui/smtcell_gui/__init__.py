"""SMTCell 2.0 - minimal control GUI.

A small, production-focused PySide6 front end for the SMTCell flow:
pick a CONFIG preset, pick cell(s), run ``make config -> spnr -> gds -> lef``
with a live log, then view solve status + the result layout PNG.

Launch with::

    cd src/gui && python -m smtcell_gui
"""

import sys as _sys
from pathlib import Path as _Path

# The launcher runs this package from ``src/gui``, so the repo root is not on
# sys.path and ``import src.cellgen...`` would fail. The shared parsers live in
# ``src/cellgen/io`` (one canonical copy, also used by the run driver and the
# web backend), so put the repo root on the path before any submodule imports.
_REPO_ROOT = _Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

__version__ = "2.0.0"
