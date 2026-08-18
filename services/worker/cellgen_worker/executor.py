"""Running one cell of one experiment.

Deliberately free of Celery and Redis: this is where the difficult behaviour
lives -- process groups, streaming a log that may run for hours, cancelling a
solve that ignores politeness -- and none of it needs a broker to be exercised.
``tasks.py`` is the thin layer that hands work to it.

Three constraints from the engine shape everything here:

* a solve can run for hours, so output is streamed rather than collected, and
  there is no overall timeout by default;
* the orchestrators read ``./input`` with relative paths, so the process must
  start from the engine root;
* CP-SAT spawns its own workers and a cancelled solve must not outlive its
  task, so the child gets its own process group and is killed as a group.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# '** Objective value: 1021.0' -- the first line of a .res file, and the only
# figure worth comparing between runs. Layout geometry is not reproducible;
# see docs/solve-reproducibility.md.
_OBJECTIVE = re.compile(r"Objective value:\s*(-?[0-9.]+)")
_ELAPSED = re.compile(r"Elapsed time:\s*([0-9.]+)\s*seconds")
# CP-SAT's own summary block, for the run table's diagnostic columns.
_CPSAT_STATS = {
    "booleans": re.compile(r"^#Bools:\s*(\d+)", re.MULTILINE),
    "conflicts": re.compile(r"^conflicts:\s*(\d+)", re.MULTILINE),
    "branches": re.compile(r"^branches:\s*(\d+)", re.MULTILINE),
}

SOLVED = {"OPTIMAL", "FEASIBLE"}


@dataclass
class RunSpec:
    """Everything one run needs, resolved before any process starts."""

    run_id: str
    cell: str
    preset: str
    engine_src: Path
    run_root: Path
    experiment_id: str = ""
    overrides: list[str] = field(default_factory=list)
    # An exported artifact, as stored by the API. None runs the stock engine.
    artifact: dict | None = None
    max_time: int | None = None

    @property
    def output_dir(self) -> Path:
        return self.run_root / "output"

    @property
    def log_path(self) -> Path:
        return self.run_root / "run.log"


@dataclass
class RunResult:
    run_id: str
    cell: str
    ok: bool
    status: str = ""
    objective: float | None = None
    walltime: float | None = None
    metrics: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    plugin_deltas: list = field(default_factory=list)
    error: str = ""
    cancelled: bool = False

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "cell": self.cell, "ok": self.ok,
            "status": self.status, "objective": self.objective,
            "walltime": self.walltime, "metrics": self.metrics,
            "artifacts": self.artifacts, "plugin_deltas": self.plugin_deltas,
            "error": self.error, "cancelled": self.cancelled,
        }


class Cancelled(Exception):
    pass


class RunExecutor:
    """Runs one cell, streaming its log, and can be cancelled from another thread."""

    def __init__(self, on_log: Callable[[str], None] | None = None):
        self.on_log = on_log
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    # -- workspace -------------------------------------------------------
    def prepare(self, spec: RunSpec) -> tuple[Path, Path | None]:
        """Lay out this run's workspace. Returns ``(engine_dir, plugin_dir)``.

        An artifact that is only plugins runs against the shared engine with a
        per-run plugin directory -- copying a checkout per run would dominate
        the cost of a short solve. An artifact carrying a patch cannot: applying
        it would mutate the engine every other run is using, so that case gets
        its own copy.
        """
        spec.run_root.mkdir(parents=True, exist_ok=True)
        spec.output_dir.mkdir(parents=True, exist_ok=True)

        artifact = spec.artifact
        if not artifact:
            return spec.engine_src, None

        # Kept verbatim. A git patch must end in a newline, so stripping it --
        # even just to test for emptiness -- makes `git apply` reject every
        # patch as corrupt at its last line.
        patch = artifact.get("engine_patch") or ""
        plugin_dir = spec.run_root / "plugins"
        plugin_dir.mkdir(exist_ok=True)

        if not patch.strip():
            self._write_plugins(artifact, plugin_dir)
            return spec.engine_src, plugin_dir

        engine_dir = spec.run_root / "engine"
        if not engine_dir.exists():
            logger.info(f"[{spec.run_id}] artifact carries a patch; copying the engine")
            shutil.copytree(
                spec.engine_src, engine_dir,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "output", "runs", ".git", ".pytest_cache"),
            )
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=engine_dir, input=patch, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"engine patch did not apply: {proc.stderr[-400:]}\n"
                f"The checkout probably does not match the artifact's baseline "
                f"({(artifact.get('engine_baseline') or '')[:12]})."
            )
        self._write_plugins(artifact, plugin_dir)
        return engine_dir, plugin_dir

    @staticmethod
    def _write_plugins(artifact: dict, plugin_dir: Path) -> None:
        for plugin in artifact.get("plugins", []):
            # Flattened: the artifact records `plugins/x.py`, and everything
            # lands in this run's own plugin directory regardless.
            (plugin_dir / Path(plugin["path"]).name).write_text(plugin["source"])
        if (manifest := artifact.get("manifest")) is not None:
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    def command(self, spec: RunSpec, plugin_dir: Path | None) -> list[str]:
        cmd = [
            sys.executable, "-m", "src.cellgen.run",
            "--preset", spec.preset, "--cell", spec.cell,
            "--output-dir", str(spec.output_dir),
        ]
        if plugin_dir is not None:
            cmd += ["--plugin-dir", str(plugin_dir)]
        if spec.max_time:
            cmd += ["--override", "max_time.value=true",
                    "--override", f"max_time.time={spec.max_time}"]
        for override in spec.overrides:
            cmd += ["--override", override]
        return cmd

    # -- execution -------------------------------------------------------
    def run(self, spec: RunSpec) -> RunResult:
        started = time.time()
        try:
            engine_dir, plugin_dir = self.prepare(spec)
        except Exception as exc:
            return RunResult(run_id=spec.run_id, cell=spec.cell, ok=False,
                             status="ERROR", error=str(exc))

        cmd = self.command(spec, plugin_dir)

        with self._lock:
            if self._cancelled.is_set():
                return RunResult(run_id=spec.run_id, cell=spec.cell, ok=False,
                                 status="CANCELLED", cancelled=True)
            # Logged only once the run is actually going ahead, so the log does
            # not show a command that was never executed.
            logger.info(f"[{spec.run_id}] {' '.join(cmd)}")
            self._proc = subprocess.Popen(
                cmd, cwd=engine_dir,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                # Its own process group: CP-SAT spawns search workers, and a
                # cancelled run must take all of them with it.
                start_new_session=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg"},
            )

        proc = self._proc
        with open(spec.log_path, "w", encoding="utf-8") as log_file:
            assert proc.stdout is not None
            for line in proc.stdout:
                log_file.write(line)
                log_file.flush()
                if self.on_log is not None:
                    try:
                        self.on_log(line.rstrip("\n"))
                    except Exception:
                        # A broken log subscriber must not fail the solve.
                        logger.exception(f"[{spec.run_id}] log callback failed")
        code = proc.wait()
        walltime = time.time() - started

        if self._cancelled.is_set():
            return RunResult(run_id=spec.run_id, cell=spec.cell, ok=False,
                             status="CANCELLED", walltime=walltime, cancelled=True)
        return self._collect(spec, code, walltime)

    def _collect(self, spec: RunSpec, code: int, walltime: float) -> RunResult:
        result = RunResult(run_id=spec.run_id, cell=spec.cell, ok=code == 0,
                           walltime=round(walltime, 2))

        run_json = spec.output_dir / "run.json"
        if run_json.is_file():
            record = json.loads(run_json.read_text())
            result.status = record.get("results", {}).get(spec.cell, "")
            result.plugin_deltas = record.get("plugin_deltas", {}).get(spec.cell, [])

        log_text = spec.log_path.read_text(errors="replace") if spec.log_path.is_file() else ""
        if (m := _ELAPSED.search(log_text)):
            result.metrics["solve_seconds"] = float(m.group(1))
        for name, pattern in _CPSAT_STATS.items():
            if (m := pattern.search(log_text)):
                result.metrics[name] = int(m.group(1))

        # The objective is read only from a run that succeeded: a failed solve
        # writes no .res, so an older one would be reported as this run's.
        res_file = spec.output_dir / "result" / f"{spec.cell}.res"
        if result.ok and res_file.is_file():
            if (m := _OBJECTIVE.search(res_file.read_text().splitlines()[0])):
                result.objective = float(m.group(1))

        for key, rel in (("res", f"result/{spec.cell}.res"),
                         ("var", f"result/{spec.cell}.var"),
                         ("png", f"view/{spec.cell}.png"),
                         ("gds", f"gds/{spec.cell}.gds"),
                         ("log", None)):
            path = spec.log_path if rel is None else spec.output_dir / rel
            if path.is_file():
                result.artifacts[key] = str(path)

        if not result.ok and not result.status:
            result.status = "ERROR"
        if not result.ok:
            result.error = "\n".join(log_text.strip().splitlines()[-20:])
        return result

    # -- cancellation ----------------------------------------------------
    def cancel(self, grace: float = 10.0) -> bool:
        """Stop the run, SIGTERM then SIGKILL, to the whole process group.

        Safe to call before the process exists (the run then never starts) and
        after it has exited. Returns whether a process was signalled.
        """
        self._cancelled.set()
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False

        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return False

        for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5.0)):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return True
            deadline = time.time() + wait
            while time.time() < deadline:
                if proc.poll() is not None:
                    return True
                time.sleep(0.1)
        return True
