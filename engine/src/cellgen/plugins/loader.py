"""Discover and load constraint plugins from a directory.

A plugin directory holds plain ``*.py`` files, each registering one or more
constraints with :func:`src.cellgen.plugins.registry.constraint`. An optional
``manifest.json`` alongside them controls which run, in what order, and with
what parameters::

    {
      "plugins": [
        {"id": "max_vias_per_col", "enabled": true, "order": 10,
         "params": {"max_vias": 3}}
      ]
    }

Without a manifest every discovered plugin runs with its declared defaults, in
registration order -- the convenient behaviour while developing inside the
sandbox. Experiments always ship a manifest, so a recorded run is reproducible.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from src.cellgen.plugins import registry

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class PluginSelection:
    """A plugin the manifest asked for, with its resolved parameters."""

    plugin: registry.ConstraintPlugin
    params: dict
    order: int


class PluginLoadError(Exception):
    """A plugin file could not be imported, or the manifest is inconsistent."""


def load_plugin_dir(plugin_dir: Path | str) -> list[registry.ConstraintPlugin]:
    """Import every ``*.py`` in ``plugin_dir`` so its decorators register.

    Files are imported under a ``cellgen_plugin_<stem>`` module name to keep
    them out of the normal package namespace. Import errors are fatal: a
    constraint that fails to load must not silently vanish from a run whose
    results someone will later compare.
    """
    plugin_dir = Path(plugin_dir)
    if not plugin_dir.is_dir():
        raise PluginLoadError(f"Plugin directory does not exist: {plugin_dir}")

    loaded: list[registry.ConstraintPlugin] = []

    for path in sorted(plugin_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        before = {p.id for p in registry.all_constraints()}
        module_name = f"cellgen_plugin_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Could not build an import spec for {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[module_name]
            raise PluginLoadError(f"Failed to import plugin {path.name}: {exc}") from exc

        new = [p for p in registry.all_constraints() if p.id not in before]
        for p in new:
            # Record the file rather than the synthetic module name, so error
            # messages point at something the user can open.
            object.__setattr__(p, "source_file", str(path))
        loaded.extend(new)

    logger.info(
        f"Loaded {len(loaded)} constraint plugin(s) from {plugin_dir}: "
        f"{[p.id for p in loaded]}"
    )
    return loaded


def read_manifest(plugin_dir: Path | str) -> dict | None:
    """Return the parsed ``manifest.json``, or None when there isn't one."""
    path = Path(plugin_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PluginLoadError(f"Invalid JSON in {path}: {exc}") from exc


def resolve_selection(manifest: dict | None) -> list[PluginSelection]:
    """Turn a manifest into the ordered list of plugins to run.

    ``manifest is None`` means "run everything that registered", which is what
    the sandbox dev loop wants. An explicit manifest is authoritative: a plugin
    it does not mention does not run.
    """
    if manifest is None:
        return [
            PluginSelection(plugin=p, params=dict(p.params), order=i)
            for i, p in enumerate(registry.all_constraints())
        ]

    entries = manifest.get("plugins", [])
    if not isinstance(entries, list):
        raise PluginLoadError("manifest['plugins'] must be a list")

    selections: list[PluginSelection] = []
    for i, entry in enumerate(entries):
        plugin_id = entry.get("id")
        if not plugin_id:
            raise PluginLoadError(f"manifest entry {i} has no 'id'")
        if not entry.get("enabled", True):
            continue
        plugin = registry.get(plugin_id)
        if plugin is None:
            known = [p.id for p in registry.all_constraints()]
            raise PluginLoadError(
                f"Manifest references unknown constraint {plugin_id!r}. "
                f"Loaded plugins: {known}"
            )
        selections.append(
            PluginSelection(
                plugin=plugin,
                params=plugin.merged_params(entry.get("params")),
                order=entry.get("order", i),
            )
        )

    selections.sort(key=lambda s: s.order)
    return selections


def load(plugin_dir: Path | str | None) -> list[PluginSelection]:
    """Load a plugin directory and resolve its manifest in one call."""
    if plugin_dir is None:
        return []
    load_plugin_dir(plugin_dir)
    return resolve_selection(read_manifest(plugin_dir))
