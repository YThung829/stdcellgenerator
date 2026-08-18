"""Celery configuration for the solver worker.

The defaults matter more than usual here, because a CP-SAT solve breaks most
of them:

* **No global time limit.** A DFF can solve for hours. A blanket
  ``task_time_limit`` would kill exactly the runs the tool exists to do; each
  task instead derives its own soft limit from the experiment's ``max_time``.
* **Concurrency is CPU count divided by the solver's own workers.** OR-Tools
  runs 8 search workers by default (``model_preset`` 2 forces at least that),
  so running one task per core would have every solve fighting the others for
  the same cores and finishing slower than running them in sequence.
* **``acks_late`` with ``reject_on_worker_lost``.** A run is expensive and
  idempotent -- it writes into its own directory -- so it is better re-run
  after a crash than silently lost.
"""

from __future__ import annotations

import os

from celery import Celery

BROKER_URL = os.environ.get("CELLGEN_REDIS_URL", "redis://127.0.0.1:6379/0")

# Anything above 1 is a guess about the machine; the solver's own parallelism
# is the thing actually using the cores.
SOLVER_WORKERS = int(os.environ.get("CELLGEN_SOLVER_WORKERS", 8))


def default_concurrency() -> int:
    cores = os.cpu_count() or 1
    return max(1, cores // max(1, SOLVER_WORKERS))


app = Celery("cellgen", broker=BROKER_URL, backend=BROKER_URL)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # See the module docstring: none of these are stylistic.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=None,
    task_soft_time_limit=None,
    worker_concurrency=default_concurrency(),
    # One long solve per slot; fetching more would leave them queued behind it.
    worker_prefetch_multiplier=1,
    result_expires=7 * 24 * 3600,
    # Fail fast when Redis is not there. Celery's defaults retry both the
    # broker *and* the result backend with a backoff, which turned "no Redis"
    # into 19 seconds of hanging per run inside the API request that queued it.
    # A run that cannot be dispatched should be visibly pending immediately.
    broker_connection_retry_on_startup=False,
    broker_connection_max_retries=0,
    broker_transport_options={"max_retries": 0, "socket_connect_timeout": 2},
    result_backend_transport_options={
        "retry_policy": {"max_retries": 0}, "socket_connect_timeout": 2},
)
