"""Experiment bookkeeping: creating runs, rolling up status, cancelling.

No broker here. Dispatch to Celery is deliberately allowed to fail -- a run
then stays `pending` with the reason recorded -- so everything below exercises
the path a machine without Redis actually takes, which is also the path the
API must survive.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.experiments import (  # noqa: E402
    CANCELLED,
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    ExperimentService,
    ExperimentSpec,
)
from cellgen_api.store import FileStore  # noqa: E402


@pytest.fixture
def service(tmp_path, monkeypatch):
    # Point Redis somewhere closed, so cancellation takes its no-broker path
    # deterministically rather than depending on what is running locally.
    monkeypatch.setenv("CELLGEN_REDIS_URL", "redis://127.0.0.1:1/0")
    return ExperimentService(FileStore(tmp_path / "store"))


def spec(**kw) -> ExperimentSpec:
    return ExperimentSpec(
        name=kw.pop("name", "exp"),
        preset=kw.pop("preset", "FinFET_4T_SH"),
        cells=kw.pop("cells", ["INV_X1", "NAND2_X1"]),
        **kw,
    )


class TestCreation:
    def test_one_run_per_cell(self, service):
        experiment = service.create(spec(cells=["INV_X1", "NAND2_X1", "AOI21_X2"]))
        assert [r["cell"] for r in experiment["runs"]] == [
            "AOI21_X2", "INV_X1", "NAND2_X1"]
        assert all(r["status"] == PENDING for r in experiment["runs"])

    def test_an_experiment_needs_a_cell(self, service):
        with pytest.raises(ValueError, match="at least one cell"):
            service.create(spec(cells=[]))

    def test_an_unknown_artifact_is_rejected_before_any_run_exists(self, service):
        with pytest.raises(KeyError, match="no such artifact"):
            service.create(spec(artifact_id="art_missing"))
        assert service.list() == []

    def test_a_time_cap_is_always_recorded(self, service):
        """The engine defaults to no limit, which would hold a worker for ever."""
        experiment = service.create(spec())
        assert experiment["max_time"] > 0

    def test_an_undispatchable_run_stays_pending_with_a_reason(self, service):
        """No broker must not lose the run; it is idempotent and can be re-sent."""
        experiment = service.create(spec(cells=["INV_X1"]))
        run = experiment["runs"][0]
        assert run["status"] == PENDING
        assert run.get("dispatch_error"), "the failure to dispatch went unrecorded"
        assert "celery_task_id" not in run


class TestStatusRollUp:
    """An experiment's status is derived from its runs, never stored.

    Storing it would make every finishing worker write back to the parent --
    a race between workers, for no gain.
    """

    def _with_statuses(self, service, statuses: list[str]) -> str:
        experiment = service.create(spec(cells=[f"C{i}" for i in range(len(statuses))]))
        for run, status in zip(experiment["runs"], statuses):
            service.store.put("runs", run["_id"], {**run, "status": status})
        return service.get(experiment["_id"])["status"]

    def test_all_pending_is_pending(self, service):
        assert self._with_statuses(service, [PENDING, PENDING]) == PENDING

    def test_any_running_is_running(self, service):
        assert self._with_statuses(service, [PENDING, RUNNING, DONE]) == RUNNING

    def test_all_done_is_done(self, service):
        assert self._with_statuses(service, [DONE, DONE]) == DONE

    def test_any_failure_makes_the_experiment_failed(self, service):
        """A partly failed batch must not read as a clean success."""
        assert self._with_statuses(service, [DONE, FAILED]) == FAILED

    def test_all_cancelled_is_cancelled_not_failed(self, service):
        assert self._with_statuses(service, [CANCELLED, CANCELLED]) == CANCELLED

    def test_progress_is_counted_for_the_list_view(self, service):
        experiment = service.create(spec(cells=["A", "B", "C"]))
        runs = experiment["runs"]
        service.store.put("runs", runs[0]["_id"], {**runs[0], "status": DONE})
        service.store.put("runs", runs[1]["_id"], {**runs[1], "status": FAILED})

        listed = next(e for e in service.list() if e["_id"] == experiment["_id"])
        assert (listed["run_count"], listed["done_count"]) == (3, 2)


class TestCancellation:
    def test_a_pending_run_with_no_broker_is_cancelled_outright(self, service):
        """Nothing is running it and nothing can be told to stop."""
        experiment = service.create(spec(cells=["INV_X1"]))
        run_id = experiment["runs"][0]["_id"]

        cancelled = service.cancel_run(run_id)
        assert cancelled["status"] == CANCELLED
        assert service.get(experiment["_id"])["status"] == CANCELLED

    def test_a_finished_run_is_left_alone(self, service):
        experiment = service.create(spec(cells=["INV_X1"]))
        run = experiment["runs"][0]
        service.store.put("runs", run["_id"], {**run, "status": DONE,
                                               "objective": 1021.0})

        assert service.cancel_run(run["_id"])["status"] == DONE
        assert service.run(run["_id"])["objective"] == 1021.0

    def test_cancelling_an_experiment_touches_only_unfinished_runs(self, service):
        experiment = service.create(spec(cells=["A", "B", "C"]))
        runs = experiment["runs"]
        service.store.put("runs", runs[0]["_id"], {**runs[0], "status": DONE})

        service.cancel_experiment(experiment["_id"])
        after = {r["cell"]: r["status"] for r in service.runs_of(experiment["_id"])}
        assert after == {"A": DONE, "B": CANCELLED, "C": CANCELLED}

    def test_cancelling_an_unknown_run_says_so(self, service):
        with pytest.raises(KeyError, match="no such run"):
            service.cancel_run("run_nope")
