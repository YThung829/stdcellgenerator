# CellGenerator worker

Runs experiments: one Celery task per cell, each solving in its own process.

```bash
pip install -e services/worker[dev]
celery -A cellgen_worker.tasks worker --loglevel=INFO
```

Needs Redis (`CELLGEN_REDIS_URL`, default `redis://127.0.0.1:6379/0`) as both
broker and result backend.

## Configuration

| variable | default | meaning |
|---|---|---|
| `CELLGEN_REDIS_URL` | `redis://127.0.0.1:6379/0` | broker and result backend |
| `CELLGEN_ENGINE_SRC` | `../../engine` | engine checkout runs solve against |
| `CELLGEN_RUN_ROOT` | `../../.cellgen/runs` | per-run output |
| `CELLGEN_DATA_ROOT` | `../../.cellgen` | the store the API also reads |
| `CELLGEN_MONGO_URL` | unset | use MongoDB; falls back to files |
| `CELLGEN_SOLVER_WORKERS` | `8` | OR-Tools search workers per solve |

## Why the Celery settings are not the defaults

* **No global time limit.** A DFF can legitimately solve for hours; a blanket
  `task_time_limit` would kill exactly the runs this exists to do. Each task
  gets a soft limit derived from its experiment's `max_time` instead.
* **Concurrency is cores ÷ `CELLGEN_SOLVER_WORKERS`.** OR-Tools runs eight
  search workers per solve by default, so one task per core has every solve
  fighting the others for the same cores.
* **`acks_late` + `reject_on_worker_lost`.** A run is expensive and idempotent
  — it writes into its own directory — so re-running it after a crash beats
  losing it.
* **No broker retries.** Celery retries both broker and result backend with a
  backoff, which turns "Redis is down" into an API request that hangs for
  minutes. Failing fast leaves the run visibly pending with the reason.

## Cancelling

The API sets `run:<id>:cancel` in Redis; the worker polls it while the solve
runs and kills the child's whole **process group** — CP-SAT spawns search
workers, and signalling only the parent would orphan them.

## Tests

```bash
cd services/worker && python -m pytest -q
```

`tests/test_executor.py` needs no broker. `tests/test_end_to_end.py` starts a
private `redis-server` and a real Celery worker, and skips when `redis-server`
is not installed.
