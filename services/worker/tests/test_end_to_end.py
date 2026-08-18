"""The whole Tab 2 path, with a real broker and a real worker.

Everything else is tested without Redis on purpose. This file is the opposite:
it starts `redis-server`, starts a Celery worker as its own process, queues an
experiment through the API's service, and waits for the solve to actually
happen. What it proves is the wiring -- that the payload the API sends is the
payload the task expects, that results come back into the store the API reads,
and that a cancel set by the API reaches a solve running in another process.

Skipped cleanly when `redis-server` is not installed.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parents[1]
API_ROOT = REPO_ROOT / "services" / "api"
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(API_ROOT))

requires_redis = pytest.mark.skipif(
    shutil.which("redis-server") is None, reason="redis-server is not installed")

pytestmark = [requires_redis, pytest.mark.slow]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def redis_url(tmp_path):
    """A private redis-server, so the test cannot disturb a real one."""
    port = free_port()
    proc = subprocess.Popen(
        ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no",
         "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=1)
        for _ in range(100):
            try:
                client.ping()
                break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError("redis-server did not come up")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def worker(redis_url, tmp_path):
    """A Celery worker in its own process, as it runs in production."""
    env = {
        **os.environ,
        "CELLGEN_REDIS_URL": redis_url,
        "CELLGEN_DATA_ROOT": str(tmp_path / "data"),
        "CELLGEN_RUN_ROOT": str(tmp_path / "runs"),
        "PYTHONPATH": f"{WORKER_ROOT}:{API_ROOT}",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "cellgen_worker.tasks",
         "worker", "--loglevel=WARNING", "--concurrency=2", "--pool=prefork"],
        cwd=WORKER_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )
    # Give it time to connect before anything is queued; tasks queued earlier
    # would still be picked up, but a failure here is clearer than a timeout.
    time.sleep(6)
    if proc.poll() is not None:
        pytest.fail(f"worker exited early:\n{proc.stdout.read()[-2000:]}")
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def service(redis_url, tmp_path, monkeypatch):
    """The API's experiment service, pointed at the same broker and store."""
    monkeypatch.setenv("CELLGEN_REDIS_URL", redis_url)
    monkeypatch.setenv("CELLGEN_DATA_ROOT", str(tmp_path / "data"))

    # The Celery app reads the broker URL at import time.
    for name in [m for m in list(sys.modules) if m.startswith("cellgen_worker")]:
        del sys.modules[name]

    from cellgen_api.experiments import ExperimentService
    from cellgen_api.store import FileStore

    return ExperimentService(FileStore(tmp_path / "data" / "store"))


def wait_for(predicate, timeout: float, interval: float = 1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if (value := predicate()):
            return value
        time.sleep(interval)
    return None


def test_an_experiment_runs_end_to_end(service, worker):
    """Queue two cells, and get two solved runs back."""
    from cellgen_api.experiments import ExperimentSpec

    experiment = service.create(ExperimentSpec(
        name="e2e", preset="FinFET_4T_SH", cells=["INV_X1"], max_time=120))

    # Dispatch reached the broker rather than falling back to pending.
    assert experiment["runs"][0].get("celery_task_id"), \
        experiment["runs"][0].get("dispatch_error")

    done = wait_for(
        lambda: service.get(experiment["_id"])["status"] == "done", timeout=300)
    assert done, service.get(experiment["_id"])["runs"]

    run = service.get(experiment["_id"])["runs"][0]
    assert run["status"] == "done"
    assert run["solver_status"] == "ok"
    assert run["objective"] == pytest.approx(1021.0)
    assert Path(run["artifacts"]["png"]).is_file()


def test_the_log_is_published_while_the_run_happens(service, worker, redis_url):
    """The UI follows this channel; a run that never publishes is invisible."""
    import redis

    from cellgen_api.experiments import ExperimentSpec
    from cellgen_api.runlog import END_MARKER, channel

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()

    experiment = service.create(ExperimentSpec(
        name="e2e-log", preset="FinFET_4T_SH", cells=["INV_X1"], max_time=120))
    run_id = experiment["runs"][0]["_id"]
    pubsub.subscribe(channel(run_id))

    lines: list[str] = []
    deadline = time.time() + 300
    while time.time() < deadline:
        message = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is None:
            continue
        data = message["data"]
        if data == END_MARKER:
            break
        lines.append(data)
    pubsub.close()

    assert lines, "the run published no log lines"
    assert any("INV_X1" in line for line in lines)


def test_cancelling_from_the_api_stops_a_running_solve(service, worker):
    """The API sets a flag; the worker polls it and kills its process group."""
    from cellgen_api.experiments import ExperimentSpec

    # No cap, so the solve would otherwise outlast the test by a wide margin.
    experiment = service.create(ExperimentSpec(
        name="e2e-cancel", preset="FinFET_4T_SH", cells=["AOI21_X2"],
        max_time=0, overrides=[]))
    run_id = experiment["runs"][0]["_id"]

    started = wait_for(
        lambda: service.run(run_id).get("status") == "running", timeout=120)
    assert started, service.run(run_id)

    service.cancel_run(run_id)

    stopped = wait_for(
        lambda: service.run(run_id).get("status") in {"cancelled", "failed", "done"},
        timeout=180)
    assert stopped, service.run(run_id)
    assert service.run(run_id)["status"] == "cancelled"
