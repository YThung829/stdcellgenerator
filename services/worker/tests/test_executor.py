"""Running, measuring and cancelling one solve.

Real subprocesses throughout. The behaviour that matters here -- a log that
streams while the solve is still going, a cancel that reaches CP-SAT's own
workers, a failure that does not borrow the previous run's objective -- only
exists at the process boundary, so mocking it away would test nothing.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parents[1]
ENGINE_SRC = REPO_ROOT / "engine"
sys.path.insert(0, str(WORKER_ROOT))

from cellgen_worker.executor import RunExecutor, RunSpec  # noqa: E402

TIME_CAP = 60

CAP_PLUGIN = '''
from src.cellgen.plugins import constraint


@constraint(id="w_cap", stage="post_routing", tech=["FinFET"],
            params={"max_edges": 10000}, description="Cap routing edges.")
def w_cap(inst, params):
    inst.opt.Add(sum(inst.edge_vars.values()) <= params["max_edges"])
'''


def spec(tmp_path, **kw) -> RunSpec:
    return RunSpec(
        run_id=kw.pop("run_id", "run_test"),
        cell=kw.pop("cell", "INV_X1"),
        preset=kw.pop("preset", "FinFET_4T_SH"),
        engine_src=ENGINE_SRC,
        run_root=tmp_path / "run",
        max_time=kw.pop("max_time", TIME_CAP),
        **kw,
    )


def artifact(plugins: list[tuple[str, str]], manifest=None, patch="") -> dict:
    return {
        "engine_baseline": "",
        "plugins": [{"path": f"plugins/{n}", "source": s} for n, s in plugins],
        "manifest": manifest,
        "engine_patch": patch,
    }


@pytest.fixture
def real_patch(tmp_path):
    """Produce a genuine `git diff`, rather than a hand-written approximation.

    git apply is strict about hunk headers and index lines, so a patch typed
    out by hand tests the test rather than the code.
    """
    import subprocess

    def make(filename: str, content: str) -> str:
        repo = tmp_path / "patchsrc"
        repo.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / filename).write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        return subprocess.run(
            ["git", "diff", "--cached", "--binary"],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout

    return make


class TestCommand:
    """The command is built before anything runs, so it is checkable directly."""

    def test_a_time_cap_is_always_passed(self, tmp_path):
        """The engine's own default is no limit, which would hang a queue."""
        cmd = RunExecutor().command(spec(tmp_path), None)
        assert "max_time.value=true" in cmd
        assert f"max_time.time={TIME_CAP}" in cmd

    def test_experiment_overrides_come_after_the_cap(self, tmp_path):
        """Later overrides win, so an experiment can raise its own limit."""
        s = spec(tmp_path, overrides=["max_time.time=900", "seed=7"])
        cmd = RunExecutor().command(s, None)
        assert cmd.index("max_time.time=900") > cmd.index(f"max_time.time={TIME_CAP}")
        assert "seed=7" in cmd


class TestWorkspace:
    def test_a_plugin_only_artifact_uses_the_shared_engine(self, tmp_path):
        """Copying a checkout per run would dominate a short solve."""
        s = spec(tmp_path, artifact=artifact([("cap.py", CAP_PLUGIN)]))
        engine_dir, plugin_dir = RunExecutor().prepare(s)

        assert engine_dir == ENGINE_SRC
        assert (plugin_dir / "cap.py").read_text() == CAP_PLUGIN
        assert not (s.run_root / "engine").exists()

    def test_a_patch_gets_its_own_checkout(self, tmp_path, real_patch):
        """Applying a patch to the shared engine would corrupt every other run."""
        patch = real_patch("PATCHED.md", "touched by the artifact\n")
        s = spec(tmp_path, artifact=artifact([("cap.py", CAP_PLUGIN)], patch=patch))
        engine_dir, plugin_dir = RunExecutor().prepare(s)

        assert engine_dir == s.run_root / "engine"
        assert (engine_dir / "PATCHED.md").read_text().strip() == "touched by the artifact"
        # The shared engine is untouched.
        assert not (ENGINE_SRC / "PATCHED.md").exists()

    def test_a_manifest_travels_with_the_plugins(self, tmp_path):
        manifest = {"plugins": [{"id": "w_cap", "enabled": True,
                                 "params": {"max_edges": 5}}]}
        s = spec(tmp_path, artifact=artifact([("cap.py", CAP_PLUGIN)], manifest))
        _, plugin_dir = RunExecutor().prepare(s)
        assert json.loads((plugin_dir / "manifest.json").read_text()) == manifest

    def test_a_patch_that_does_not_apply_is_reported(self, tmp_path):
        s = spec(tmp_path, artifact=artifact([], patch="not a patch at all\n"))
        result = RunExecutor().run(s)
        assert result.ok is False
        assert result.status == "ERROR"
        assert "patch did not apply" in result.error


@pytest.mark.slow
class TestRunning:
    def test_a_solve_reports_status_objective_and_artifacts(self, tmp_path):
        result = RunExecutor().run(spec(tmp_path))

        assert result.ok is True
        assert result.status == "ok"
        assert result.objective == pytest.approx(1021.0)
        assert result.walltime > 0
        assert Path(result.artifacts["res"]).is_file()
        assert Path(result.artifacts["png"]).is_file()
        assert Path(result.artifacts["log"]).is_file()

    def test_the_log_streams_while_the_solve_runs(self, tmp_path):
        """The UI follows this live; collecting it at the end would defeat that."""
        lines: list[str] = []
        result = RunExecutor(on_log=lines.append).run(spec(tmp_path))

        assert result.ok
        assert lines, "no log lines were streamed"
        assert any("INV_X1" in line for line in lines)
        # The same content lands on disk for anyone who joins late.
        assert Path(result.artifacts["log"]).read_text().strip()

    def test_a_plugin_from_an_artifact_takes_effect(self, tmp_path):
        manifest = {"plugins": [{"id": "w_cap", "enabled": True,
                                 "params": {"max_edges": 10000}}]}
        s = spec(tmp_path, artifact=artifact([("cap.py", CAP_PLUGIN)], manifest))
        result = RunExecutor().run(s)

        assert result.ok is True
        delta = next(d for d in result.plugin_deltas if d["id"] == "w_cap")
        assert delta["constraints_added"] == 1

    def test_an_unsatisfiable_run_reports_no_objective(self, tmp_path):
        """A failed solve writes no .res; reporting one would be the last run's."""
        manifest = {"plugins": [{"id": "w_cap", "enabled": True,
                                 "params": {"max_edges": 0}}]}
        s = spec(tmp_path, artifact=artifact([("cap.py", CAP_PLUGIN)], manifest),
                 max_time=30)

        executor = RunExecutor()
        # A successful run first, so a stale .res exists to be borrowed.
        good = executor.run(spec(tmp_path, artifact=None))
        assert good.objective == pytest.approx(1021.0)

        result = RunExecutor().run(s)
        assert result.ok is False
        assert result.status == "INFEASIBLE"
        assert result.objective is None
        assert result.error

    def test_a_log_subscriber_that_raises_does_not_fail_the_solve(self, tmp_path):
        def explode(_line: str) -> None:
            raise RuntimeError("subscriber is broken")

        result = RunExecutor(on_log=explode).run(spec(tmp_path))
        assert result.ok is True


@pytest.mark.slow
class TestCancellation:
    """A solve ignores politeness, so cancelling has to reach the process group."""

    def test_cancelling_a_running_solve_stops_it(self, tmp_path):
        executor = RunExecutor()
        # No time cap: this run would otherwise outlast the test.
        s = spec(tmp_path, cell="AOI21_X2", max_time=0)

        started = threading.Event()
        executor_result: list = []

        def watch(line: str) -> None:
            if not started.is_set():
                started.set()

        executor.on_log = watch
        thread = threading.Thread(
            target=lambda: executor_result.append(executor.run(s)))
        thread.start()

        assert started.wait(timeout=120), "the solve never produced output"
        time.sleep(2)
        assert executor.cancel() is True

        thread.join(timeout=60)
        assert not thread.is_alive(), "the run did not stop after cancel"

        result = executor_result[0]
        assert result.cancelled is True
        assert result.status == "CANCELLED"

    def test_no_solver_process_survives_a_cancel(self, tmp_path):
        """CP-SAT spawns search workers; the group is what has to die."""
        executor = RunExecutor()
        s = spec(tmp_path, cell="AOI21_X2", max_time=0)

        started = threading.Event()
        executor.on_log = lambda _line: started.set()
        thread = threading.Thread(target=lambda: executor.run(s))
        thread.start()
        assert started.wait(timeout=120)

        pid = executor._proc.pid
        pgid = os.getpgid(pid)
        executor.cancel()
        thread.join(timeout=60)

        with pytest.raises(ProcessLookupError):
            # Signal 0 only checks existence.
            os.killpg(pgid, 0)

    def test_cancelling_before_the_start_prevents_the_run(self, tmp_path):
        executor = RunExecutor()
        executor.cancel()
        result = executor.run(spec(tmp_path))

        assert result.cancelled is True
        assert result.status == "CANCELLED"
        # Nothing was solved, so nothing was written.
        assert list((spec(tmp_path).output_dir / "result").glob("*.res")) == []

    def test_cancelling_a_finished_run_is_harmless(self, tmp_path):
        executor = RunExecutor()
        result = executor.run(spec(tmp_path))
        assert result.ok
        assert executor.cancel() is False
