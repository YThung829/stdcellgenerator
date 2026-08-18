"""Back-compat shim: the parser now lives in :mod:`src.cellgen.io.presets`."""
from src.cellgen.io.presets import (  # noqa: F401
    parse_preset,
    parse_preset_dict,
)
