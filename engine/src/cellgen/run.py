"""Single entry point for one place-and-route run, with per-run output isolation.

The Makefile drives the flow in two steps (``make config`` then ``make spnr``)
and derives ``OUT_DIR`` from the library name alone::

    OUT_DIR = ./output/$(LIBNAME)/$(HEIGHT_CONFIG)

That is fine interactively but wrong for a batch service: two runs that differ
only in a config override (a seed, ``insert_num_db``, a plugin set) resolve to
the same ``OUT_DIR`` and silently overwrite each other's ``.res``. Worse,
``make config`` keeps an existing per-cell JSON unless ``FORCE=1``, so the
second run would quietly reuse the first run's settings.

This module takes ``--output-dir`` explicitly, always regenerates the per-cell
config inside it, and does both stages in one process::

    python -m src.cellgen.run --preset FinFET_4T_SH --cell INV_X1 \
        --output-dir runs/abc123 --override max_time.value=true

Preset resolution (including the Makefile's CDL/layer fallbacks) is reproduced
here so callers never have to shell out to ``make show-config``.

Exit status: 0 when every requested cell solved, 1 if any cell failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from src.cellgen.archit.config import _parse_overrides, generate_config
from src.cellgen.core.entity import LayerStack
from src.cellgen.core.errors import SolveFailed
from src.cellgen.io.presets import parse_preset_dict
from src.cellgen import plugins

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESET_DIR = REPO_ROOT / "input" / "presets"

DEFAULT_CELL_PREFIX = "PROBE3"
# Same fallbacks the Makefile applies when a preset points at a missing file.
FALLBACK_CDL = REPO_ROOT / "input" / "cdl" / "PROBE_2F4T.cdl"

# Subdirectories `make spnr` creates; the orchestrators assume they exist.
_OUTPUT_SUBDIRS = ("config", "result", "logs", "constraint", "view", "gds")


@dataclass
class RunConfig:
    """A fully resolved preset: every value the solve stages need."""

    preset: str
    tech: str
    height_config: str
    channel: str
    track: int
    cpp: str
    m1p: str
    m1of: str
    cdl_file: Path
    layer_file: Path
    cell_names: list[str]
    overrides: list[str] = field(default_factory=list)
    cell_prefix: str = DEFAULT_CELL_PREFIX

    @property
    def lib_name(self) -> str:
        """Mirror the Makefile's LIBNAME derivation."""
        return (
            f"{self.cell_prefix}_{self.tech}_{self.channel}_"
            f"{self.track}T_{self.cpp}{self.m1p}OF{self.m1of}"
        )


def resolve_preset(preset: str, repo_root: Path = REPO_ROOT) -> RunConfig:
    """Parse ``input/presets/<preset>.mk`` and resolve its file paths.

    Reproduces the Makefile's two fallbacks: a CDL or layer path that does not
    exist on disk falls back to the bundled default for the derived LIBNAME.
    """
    path = repo_root / "input" / "presets" / f"{preset}.mk"
    if not path.is_file():
        available = sorted(p.stem for p in (repo_root / "input" / "presets").glob("*.mk"))
        raise FileNotFoundError(
            f"Unknown preset {preset!r}. Available: {', '.join(available) or '(none)'}"
        )

    raw = parse_preset_dict(path)
    missing = [k for k in ("TECH", "HEIGHT_CONFIG", "CHANNEL", "TRACK") if k not in raw]
    if missing:
        raise ValueError(f"Preset {preset!r} is missing required keys: {missing}")

    # CELL_NAME is a backslash-continued list; parse_preset_dict already
    # collapsed the continuations to spaces.
    cell_names = raw.get("CELL_NAME", "").split()
    # CONFIG_OVERRIDES is likewise a space-separated KEY=VALUE list.
    overrides = raw.get("CONFIG_OVERRIDES", "").split()

    cfg = RunConfig(
        preset=preset,
        tech=raw["TECH"],
        height_config=raw["HEIGHT_CONFIG"],
        channel=raw["CHANNEL"],
        track=int(raw["TRACK"]),
        cpp=raw.get("CPP", ""),
        m1p=raw.get("M1P", ""),
        m1of=raw.get("M1OF", ""),
        cdl_file=Path(),   # filled in below (lib_name needs the other fields)
        layer_file=Path(),
        cell_names=cell_names,
        overrides=overrides,
        cell_prefix=raw.get("CELL_PREFIX", DEFAULT_CELL_PREFIX),
    )

    cdl = repo_root / raw["CDL_FILE"] if raw.get("CDL_FILE") else Path()
    cfg.cdl_file = cdl if cdl.is_file() else FALLBACK_CDL

    layer = repo_root / raw["LAYER_FILE"] if raw.get("LAYER_FILE") else Path()
    if not layer.is_file():
        layer = repo_root / "input" / "layer" / f"{cfg.lib_name}.json"
    cfg.layer_file = layer

    if not cfg.cdl_file.is_file():
        raise FileNotFoundError(f"CDL netlist not found: {cfg.cdl_file}")
    if not cfg.layer_file.is_file():
        raise FileNotFoundError(
            f"Layer stack not found for library {cfg.lib_name!r}: {cfg.layer_file}"
        )
    return cfg


def prepare_output_dir(output_dir: Path) -> Path:
    """Create the per-run output tree the orchestrators write into."""
    output_dir = output_dir.resolve()
    for sub in _OUTPUT_SUBDIRS:
        (output_dir / sub).mkdir(parents=True, exist_ok=True)
    return output_dir


def solve_cell(
    cfg: RunConfig,
    cell: str,
    output_dir: Path,
    *,
    flag_log_constraints: bool = False,
) -> None:
    """Solve one cell into ``output_dir``. Raises :class:`SolveFailed` on failure.

    Imported lazily: pulling in the orchestrators costs a few seconds of
    OR-Tools import time, which callers that only resolve a preset should not pay.
    """
    from src.main import SMTCell, build_technology

    technology = build_technology(
        tech=cfg.tech,
        lib_name=cfg.lib_name,
        layer_stack=LayerStack(str(cfg.layer_file)),
        num_rt_track=cfg.track,
        height_config=cfg.height_config,
    )
    SMTCell(
        cdl_file=str(cfg.cdl_file),
        # The orchestrators accept a path and call config.read() themselves.
        cell_config=str(output_dir / "config" / f"{cell}.json"),
        technology=technology,
        circuit_names=[cell],
        output_dir=str(output_dir),
        flag_log_constraints=flag_log_constraints,
    )


class _Tee:
    """Duplicate writes to the real stream and to a log file.

    CP-SAT's search log goes through ``print`` (the orchestrators set
    ``solver.log_callback = print``), so capturing it needs a stdout wrapper;
    loguru writes to stderr and gets its own sink. Together these reproduce
    the Makefile's ``2>&1 | tee $(OUT_DIR)/logs/$(CELL).log``.
    """

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, data):
        self._stream.write(data)
        self._handle.write(data)
        return len(data)

    def flush(self):
        self._stream.flush()
        self._handle.flush()

    def isatty(self):
        return False


def run(
    preset: str,
    cells: list[str] | None,
    output_dir: Path,
    *,
    overrides: list[str] | None = None,
    flag_log_constraints: bool = False,
    plugin_dir: Path | str | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, str]:
    """Resolve the preset, generate configs, and solve each cell.

    Returns ``{cell: "ok" | "<failure status>" | "ERROR"}``. A cell that fails
    never aborts the rest of the batch: CP-SAT failures surface as their status
    name, and any other exception (a bad config, or a user-authored constraint
    plugin raising) is recorded as ``"ERROR"`` with its traceback in that cell's
    log.
    """
    cfg = resolve_preset(preset, repo_root=repo_root)
    cells = cells or cfg.cell_names
    if not cells:
        raise ValueError(
            f"No cells to run: preset {preset!r} defines no CELL_NAME and "
            f"--cell was not passed."
        )

    out = prepare_output_dir(output_dir)

    # Load before any solve so a broken plugin fails the run immediately rather
    # than after the first cell has already burned solver time.
    plugins.set_active(plugins.load(plugin_dir))

    # Preset overrides first so an explicit --override on the command line wins.
    merged = list(cfg.overrides) + list(overrides or [])

    # force=True is the whole point of per-run isolation: never inherit a config
    # JSON that a previous run left in this directory.
    generate_config(
        track=cfg.track,
        tech=cfg.tech,
        height_config=cfg.height_config,
        circuit_names=cells,
        output_dir=str(out),
        overrides=_parse_overrides(merged),
        force=True,
    )

    (out / "run.json").write_text(
        json.dumps(
            {
                "preset": preset,
                "tech": cfg.tech,
                "height_config": cfg.height_config,
                "lib_name": cfg.lib_name,
                "track": cfg.track,
                "cdl_file": str(cfg.cdl_file),
                "layer_file": str(cfg.layer_file),
                "cells": cells,
                "overrides": merged,
                "plugins": [
                    {"id": s.plugin.id, "stage": s.plugin.stage, "params": s.params}
                    for s in plugins.active()
                ],
            },
            indent=2,
        )
    )

    results: dict[str, str] = {}
    for cell in cells:
        log_path = out / "logs" / f"{cell}.log"
        real_stdout = sys.stdout
        with open(log_path, "w", encoding="utf-8") as handle:
            sink_id = logger.add(handle, level="DEBUG")
            sys.stdout = _Tee(real_stdout, handle)
            t0 = time.time()
            try:
                solve_cell(cfg, cell, out, flag_log_constraints=flag_log_constraints)
                results[cell] = "ok"
            except SolveFailed as exc:
                logger.error(str(exc))
                results[cell] = exc.status
            except Exception:
                # Constraint construction can raise for reasons unrelated to
                # satisfiability (an out-of-range config value, or a plugin
                # with a bug). Contain it to this cell so a batch still
                # finishes, and keep the traceback in the cell's own log.
                logger.exception(f"{cell}: unhandled error while solving")
                results[cell] = "ERROR"
            finally:
                sys.stdout = real_stdout
                logger.remove(sink_id)
                logger.info(f"{cell}: {results.get(cell, 'ERROR')} in {time.time() - t0:.1f}s")
    return results


def _parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m src.cellgen.run",
        description="Run SMTCell place-and-route for one preset into an isolated output dir.",
    )
    p.add_argument("--preset", required=True,
                   help="Preset name under input/presets/ (without .mk).")
    p.add_argument("--cell", action="append", default=[], dest="cells",
                   help="Cell to solve; repeatable. Defaults to the preset's CELL_NAME.")
    # Required to solve, but not to `--list-cells`, which writes nothing; that
    # is enforced after parsing so listing does not demand a pointless path.
    p.add_argument("--output-dir", type=Path,
                   help="Per-run output directory. Always created fresh; configs are regenerated.")
    p.add_argument("--override", action="append", default=[], metavar="KEY[.SUB]=VALUE",
                   help="Cell-config override, repeatable. Applied after the preset's CONFIG_OVERRIDES.")
    p.add_argument("--plugin-dir", type=Path, default=None,
                   help="Directory of constraint plugins (*.py plus an optional "
                        "manifest.json). Without a manifest every plugin found runs.")
    p.add_argument("--flag-log-constraints", action="store_true",
                   help="Dump every CP-SAT constraint to constraint/<cell>.log (can exceed 500 MB).")
    p.add_argument("--list-cells", action="store_true",
                   help="Print the cells available in the preset's netlist and exit.")
    args = p.parse_args(argv)
    if not args.list_cells and args.output_dir is None:
        p.error("the following arguments are required: --output-dir")
    return args


def main(argv=None) -> int:
    args = _parse_args(argv)

    # config.init() reads ./input/pin_*_collection.json with hardcoded relative
    # paths, so every stage must run with the repo root as the working directory.
    os.chdir(REPO_ROOT)

    if args.list_cells:
        from src.cellgen.io.cdl import scan_cdl

        cfg = resolve_preset(args.preset)
        print("\n".join(scan_cdl(cfg.cdl_file)))
        return 0

    results = run(
        preset=args.preset,
        cells=args.cells,
        output_dir=args.output_dir,
        overrides=args.override,
        flag_log_constraints=args.flag_log_constraints,
        plugin_dir=args.plugin_dir,
    )

    failed = {c: s for c, s in results.items() if s != "ok"}
    for cell, status in results.items():
        logger.info(f"  {cell:24s} {status}")
    if failed:
        logger.error(f"{len(failed)}/{len(results)} cell(s) failed: {sorted(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
