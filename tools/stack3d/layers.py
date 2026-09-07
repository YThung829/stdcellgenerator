"""Technology stack tables, loaded from the ``techs/*.toml`` process specs.

This module used to hold the tables as Python literals. They now live in
``techs/*.toml``, written in a format a process engineer can fill in: layers
listed bottom to top with a ROLE and a THICKNESS, never an absolute
coordinate. ``techspec.py`` compiles those into the same tuples this module
used to define, so everything downstream is unchanged.

The move was made only after checking it was lossless: exporting all three
built-in technologies to TOML and recompiling reproduced their stack tables
exactly, layer for layer and z for z.

WHAT IS REAL AND WHAT IS NOT
----------------------------
The engine is 2D. ``input/layer/*.json`` describes each layer only in the
plane -- ``direction`` / ``pitch`` / ``offset`` / ``width`` -- and GDS is a
planar format, so nothing in the repo carries a layer THICKNESS.

  * ORDER is derived, not invented. It follows the ``layer_number`` ordering
    the ``LayerStack`` sorts metals into (that ordering *is* the LGG z-index)
    plus each via's ``lower_layer -> upper_layer`` chain.
  * Thicknesses are ILLUSTRATIVE, and now come from the per-role table in
    ``techspec.ROLES`` unless a spec overrides one. They are not PDK numbers
    and must not be read as such.

Everything else in the payload (polygon coordinates, objective values, cell
extents, LGG order) is read back from real generated files by ``build.py``.

To add a technology, drop a spec in ``techs/``. Nothing here needs editing --
see ``techs/_TEMPLATE.toml`` and the ``stack3d-onboard-tech`` skill.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from techspec import load_all  # noqa: E402

_SPECS = load_all()

# tech name -> the dict build.py consumes. Each carries a compiled `stack`.
TECHS = {name: {k: v for k, v in e.items() if not k.startswith("_")}
         for name, e in _SPECS.items()}

# Per-layer opacity: layers that ENVELOPE others (a gate stack running through
# both tiers, diffusion wrapping the fins) render semi-transparent, or they
# hide the very thing they exist to explain. Derived from role in techspec.
ALPHA = {name: e["_alpha"] for name, e in _SPECS.items() if e["_alpha"]}

# Order of the viewer's technology switcher, from each spec's `order` field.
TECH_ORDER = sorted(_SPECS, key=lambda n: (_SPECS[n]["_order"], n))
