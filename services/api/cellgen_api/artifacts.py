"""Getting work out of a sandbox and into a form experiments can run.

A sandbox is a disposable working copy. Nothing in it may reach the upstream
engine, and it has no push target -- the only way work leaves is as **files**,
captured here, stored in MongoDB, and materialised again when an experiment
runs.

Two kinds of change have to survive that trip:

* **Constraint plugins** (``plugins/*.py`` plus ``manifest.json``). Extracted
  individually so an experiment can enable, disable and re-parameterise them
  one at a time -- which is the whole reason constraints became plugins.
* **Everything else the user edited** -- a tweak to the objective, a change to
  an existing built-in constraint. That cannot be expressed as a plugin, so it
  travels as a unified diff against the sandbox's baseline commit.

Keeping the patch separate from the plugins matters: the plugins stay
individually addressable, while arbitrary edits are still carried faithfully
instead of being silently dropped.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from cellgen_api.backends.base import SandboxBackend, SandboxHandle

PLUGIN_DIR = "plugins"
MANIFEST = "manifest.json"


@dataclass
class PluginFile:
    path: str
    source: str


@dataclass
class Artifact:
    """A self-contained snapshot of what a sandbox session produced."""

    sandbox_id: str
    name: str
    description: str = ""
    engine_baseline: str = ""
    plugins: list[PluginFile] = field(default_factory=list)
    manifest: dict | None = None
    engine_patch: str = ""
    changed_files: list[str] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        return len(json.dumps(self.to_dict()).encode())

    def to_dict(self) -> dict:
        return {
            "sandbox_id": self.sandbox_id,
            "name": self.name,
            "description": self.description,
            "engine_baseline": self.engine_baseline,
            "plugins": [{"path": p.path, "source": p.source} for p in self.plugins],
            "manifest": self.manifest,
            "engine_patch": self.engine_patch,
            "changed_files": self.changed_files,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Artifact":
        return cls(
            sandbox_id=d.get("sandbox_id", ""),
            name=d.get("name", ""),
            description=d.get("description", ""),
            engine_baseline=d.get("engine_baseline", ""),
            plugins=[PluginFile(**p) for p in d.get("plugins", [])],
            manifest=d.get("manifest"),
            engine_patch=d.get("engine_patch", ""),
            changed_files=d.get("changed_files", []),
        )


class ArtifactError(Exception):
    pass


async def capture(backend: SandboxBackend, handle: SandboxHandle,
                  name: str, description: str = "") -> Artifact:
    """Collect everything the user changed in this sandbox."""
    baseline = handle.meta.get("baseline_commit", "")
    if not baseline:
        raise ArtifactError(
            "sandbox has no baseline commit; cannot determine what changed"
        )

    # Stage everything so new files show up in the diff too. The sandbox is
    # disposable and nothing else depends on its index, so this is safe.
    await backend.exec(handle, ["git", "add", "-A"], timeout=60)

    code, changed_raw, err = await backend.exec(
        handle, ["git", "diff", "--cached", "--name-only", baseline], timeout=60)
    if code != 0:
        raise ArtifactError(f"git diff --name-only failed: {err[-300:]}")
    changed = [line.strip() for line in changed_raw.splitlines() if line.strip()]

    plugins: list[PluginFile] = []
    manifest: dict | None = None
    for rel in changed:
        if not rel.startswith(f"{PLUGIN_DIR}/"):
            continue
        try:
            content = await backend.read_file(handle, rel)
        except Exception:
            logger.exception(f"could not read {rel}; skipping")
            continue
        if rel == f"{PLUGIN_DIR}/{MANIFEST}":
            try:
                manifest = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ArtifactError(f"{rel} is not valid JSON: {exc}") from exc
        elif rel.endswith(".py"):
            plugins.append(PluginFile(path=rel, source=content))

    # Everything outside plugins/ becomes a patch. `--` separates the revision
    # from the pathspec, and `:(exclude)` drops the plugin tree.
    code, patch, err = await backend.exec(
        handle,
        ["git", "diff", "--cached", baseline, "--",
         ".", f":(exclude){PLUGIN_DIR}/*"],
        timeout=120,
    )
    if code != 0:
        raise ArtifactError(f"git diff failed: {err[-300:]}")

    artifact = Artifact(
        sandbox_id=handle.id,
        name=name,
        description=description,
        engine_baseline=baseline,
        plugins=plugins,
        manifest=manifest,
        engine_patch=patch,
        changed_files=changed,
    )
    logger.info(
        f"[{handle.id}] captured artifact {name!r}: {len(plugins)} plugin(s), "
        f"{len(patch)} B patch, {len(changed)} changed file(s)"
    )
    return artifact


def materialize(artifact: Artifact, engine_dir: Path,
                plugin_dir: Path | None = None) -> Path:
    """Apply an artifact onto a fresh engine checkout, ready to solve.

    ``engine_dir`` must already contain the engine at (or compatible with) the
    artifact's baseline. Returns the plugin directory to pass to
    ``python -m src.cellgen.run --plugin-dir``.
    """
    import subprocess

    engine_dir = Path(engine_dir)
    plugin_dir = Path(plugin_dir) if plugin_dir else engine_dir / PLUGIN_DIR
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if artifact.engine_patch.strip():
        # `git apply` works on a plain directory; it needs no repository, which
        # keeps experiment runners free of git bookkeeping.
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=engine_dir, input=artifact.engine_patch,
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise ArtifactError(
                f"engine patch did not apply cleanly onto {engine_dir}: "
                f"{proc.stderr[-500:]}\n"
                f"The checkout probably does not match the artifact's baseline "
                f"({artifact.engine_baseline[:12]})."
            )

    for plugin in artifact.plugins:
        target = engine_dir / plugin.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(plugin.source)

    if artifact.manifest is not None:
        (plugin_dir / MANIFEST).write_text(json.dumps(artifact.manifest, indent=2))

    logger.info(
        f"materialised artifact {artifact.name!r} into {engine_dir} "
        f"({len(artifact.plugins)} plugin(s))"
    )
    return plugin_dir


def run_command(engine_dir: Path, plugin_dir: Path, preset: str,
                cell: str, output_dir: Path, overrides: list[str] | None = None) -> str:
    """The exact command an experiment runs for a materialised artifact."""
    parts = [
        "python", "-m", "src.cellgen.run",
        "--preset", preset, "--cell", cell,
        "--output-dir", str(output_dir),
        "--plugin-dir", str(plugin_dir),
    ]
    for override in overrides or []:
        parts += ["--override", override]
    return " ".join(shlex.quote(p) for p in parts)
