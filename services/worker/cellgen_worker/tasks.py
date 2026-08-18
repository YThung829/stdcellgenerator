"""The Celery task, kept thin on purpose.

Everything hard is in `executor.py`, which needs no broker to test. What lives
here is the glue: turning a task payload into a `RunSpec`, publishing the log
so the UI can follow it live, writing the result back to the store, and
honouring a cancel request that arrives while a solve is running.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from cellgen_worker.app import app
from cellgen_worker.executor import RunExecutor, RunSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINE_SRC = Path(os.environ.get("CELLGEN_ENGINE_SRC", REPO_ROOT / "engine"))
RUN_ROOT = Path(os.environ.get("CELLGEN_RUN_ROOT", REPO_ROOT / ".cellgen" / "runs"))
DATA_ROOT = Path(os.environ.get("CELLGEN_DATA_ROOT", REPO_ROOT / ".cellgen"))

RUNS = "runs"


def log_channel(run_id: str) -> str:
    return f"run:{run_id}:log"


def cancel_key(run_id: str) -> str:
    return f"run:{run_id}:cancel"


def _redis():
    """A Redis client, or None when there is none to talk to.

    Live log streaming is a convenience; a run that cannot publish its output
    still writes the same log to disk and still finishes.
    """
    try:
        import redis

        from cellgen_worker.app import BROKER_URL

        client = redis.Redis.from_url(BROKER_URL, decode_responses=True)
        client.ping()
        return client
    except Exception as exc:
        logger.warning(f"no Redis for log streaming ({exc}); logging to file only")
        return None


def _store():
    from cellgen_api.store import build_store

    return build_store(os.environ.get("CELLGEN_MONGO_URL") or None, DATA_ROOT / "store")


@app.task(name="cellgen.solve", bind=True)
def solve(self, payload: dict) -> dict:
    """Solve one cell of one experiment and record the result.

    `payload` carries the run id, the cell, the resolved preset and overrides,
    and the artifact to run (or none, for the stock engine).
    """
    run_id = payload["run_id"]
    client = _redis()
    channel = log_channel(run_id)

    def publish(line: str) -> None:
        if client is not None:
            client.publish(channel, line)

    executor = RunExecutor(on_log=publish)

    # Cancellation arrives out of band: the API sets a key rather than trying
    # to reach into this process, and a worker that never sees it simply runs
    # to completion.
    if client is not None and client.get(cancel_key(run_id)):
        executor.cancel()

    spec = RunSpec(
        run_id=run_id,
        cell=payload["cell"],
        preset=payload["preset"],
        engine_src=ENGINE_SRC,
        run_root=RUN_ROOT / run_id,
        experiment_id=payload.get("experiment_id", ""),
        overrides=payload.get("overrides", []),
        artifact=payload.get("artifact"),
        max_time=payload.get("max_time"),
    )

    store = _store()
    record = store.get(RUNS, run_id) or {"_id": run_id}
    store.put(RUNS, run_id, {**record, "status": "running",
                             "celery_task_id": self.request.id})

    watcher = _CancelWatcher(client, run_id, executor)
    watcher.start()
    try:
        result = executor.run(spec)
    finally:
        watcher.stop()
        if client is not None:
            # Tell followers the stream is over; without it a UI waits forever.
            client.publish(channel, "__END__")
            client.delete(cancel_key(run_id))

    doc = {**record, **result.to_dict(), "_id": run_id,
           "status": "cancelled" if result.cancelled
                     else ("done" if result.ok else "failed"),
           "solver_status": result.status}
    store.put(RUNS, run_id, doc)
    logger.info(f"[{run_id}] {doc['status']} ({result.status}) in {result.walltime}s")
    return doc


class _CancelWatcher:
    """Poll for a cancel flag while a solve runs.

    Polling rather than subscribing: the executor blocks on the child's output
    in this thread, and a one-second delay in noticing a cancel is irrelevant
    next to a solve measured in minutes.
    """

    def __init__(self, client, run_id: str, executor: RunExecutor, interval: float = 1.0):
        import threading

        self._client = client
        self._key = cancel_key(run_id)
        self._executor = executor
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        if self._client is not None:
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if self._client.get(self._key):
                    logger.info("cancel requested; stopping the solve")
                    self._executor.cancel()
                    return
            except Exception:
                logger.exception("cancel watcher failed; leaving the run alone")
                return

    def stop(self) -> None:
        self._stop.set()
