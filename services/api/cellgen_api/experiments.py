"""Experiments: one artifact, many cells, one run each.

An experiment is the unit a person creates; a run is the unit that occupies a
core. Keeping them separate is what lets a 20-cell experiment report progress,
have one cell cancelled, and still be comparable afterwards.

Dispatch goes through Celery when a broker is reachable. When it is not, runs
are queued as ``pending`` and nothing is lost -- the API stays usable for
browsing past results, and the plan's own note applies: a run is idempotent, so
re-dispatching later is safe.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from loguru import logger

from cellgen_api.store import Store, new_id

EXPERIMENTS = "experiments"
RUNS = "runs"

# Statuses a run moves through. `pending` covers both "queued in Celery" and
# "not dispatched yet"; the difference is `celery_task_id`.
PENDING, RUNNING, DONE, FAILED, CANCELLED = (
    "pending", "running", "done", "failed", "cancelled")
TERMINAL = {DONE, FAILED, CANCELLED}


@dataclass
class ExperimentSpec:
    name: str
    preset: str
    cells: list[str]
    artifact_id: str | None = None
    description: str = ""
    overrides: list[str] = field(default_factory=list)
    # Always set: the engine's own default is no limit, and an experiment that
    # never finishes occupies a worker slot for ever.
    max_time: int = 300


class ExperimentService:
    def __init__(self, store: Store):
        self.store = store

    # -- creation --------------------------------------------------------
    def create(self, spec: ExperimentSpec) -> dict:
        if not spec.cells:
            raise ValueError("an experiment needs at least one cell")

        artifact = None
        if spec.artifact_id:
            doc = self.store.get("artifacts", spec.artifact_id)
            if doc is None:
                raise KeyError(f"no such artifact: {spec.artifact_id}")
            artifact = doc.get("artifact")

        experiment_id = new_id("exp")
        experiment = {
            "_id": experiment_id,
            "name": spec.name,
            "description": spec.description,
            "artifact_id": spec.artifact_id,
            "preset": spec.preset,
            "cells": list(spec.cells),
            "overrides": list(spec.overrides),
            "max_time": spec.max_time,
            "status": PENDING,
            "created_at": time.time(),
        }
        self.store.put(EXPERIMENTS, experiment_id, experiment)

        for cell in spec.cells:
            run_id = new_id("run")
            self.store.put(RUNS, run_id, {
                "_id": run_id,
                "experiment_id": experiment_id,
                "cell": cell,
                "status": PENDING,
                "created_at": time.time(),
            })
            self._dispatch(run_id, experiment, cell, artifact)

        return self.get(experiment_id)

    def _dispatch(self, run_id: str, experiment: dict, cell: str,
                  artifact: dict | None) -> None:
        payload = {
            "run_id": run_id,
            "experiment_id": experiment["_id"],
            "cell": cell,
            "preset": experiment["preset"],
            "overrides": experiment["overrides"],
            "max_time": experiment["max_time"],
            "artifact": artifact,
        }
        try:
            from cellgen_worker.tasks import solve

            async_result = solve.apply_async(
                args=[payload],
                # A run may take hours; the soft limit exists only to catch a
                # solve that has stopped respecting its own cap.
                soft_time_limit=experiment["max_time"] * 4 + 600,
                # Never retry the broker here. Celery's default is to keep
                # trying with a backoff, which turns "Redis is down" into an
                # API request that hangs for minutes instead of a run that is
                # visibly pending.
                retry=False,
            )
            record = self.store.get(RUNS, run_id) or {}
            self.store.put(RUNS, run_id, {**record, "celery_task_id": async_result.id})
        except Exception as exc:
            # No broker, or no worker package installed. The run stays pending
            # rather than being lost, and says why.
            logger.warning(f"[{run_id}] not dispatched ({exc}); left pending")
            record = self.store.get(RUNS, run_id) or {}
            self.store.put(RUNS, run_id, {**record, "dispatch_error": str(exc)})

    # -- reading ---------------------------------------------------------
    def get(self, experiment_id: str) -> dict | None:
        experiment = self.store.get(EXPERIMENTS, experiment_id)
        if experiment is None:
            return None
        runs = self.runs_of(experiment_id)
        return {**experiment, "runs": runs, "status": self._roll_up(runs)}

    def list(self) -> list[dict]:
        out = []
        for experiment in self.store.list(EXPERIMENTS):
            runs = self.runs_of(experiment["_id"])
            out.append({**experiment,
                        "status": self._roll_up(runs),
                        "run_count": len(runs),
                        "done_count": sum(1 for r in runs
                                          if r.get("status") in TERMINAL)})
        return out

    def runs_of(self, experiment_id: str) -> list[dict]:
        runs = [r for r in self.store.list(RUNS)
                if r.get("experiment_id") == experiment_id]
        return sorted(runs, key=lambda r: r.get("cell", ""))

    def run(self, run_id: str) -> dict | None:
        return self.store.get(RUNS, run_id)

    @staticmethod
    def _roll_up(runs: list[dict]) -> str:
        """An experiment's status is derived, never stored.

        Storing it would need every run completion to write back to the parent,
        which is a race between workers for no benefit.
        """
        if not runs:
            return PENDING
        statuses = {r.get("status", PENDING) for r in runs}
        if statuses <= TERMINAL:
            return FAILED if (statuses & {FAILED}) else (
                CANCELLED if statuses == {CANCELLED} else DONE)
        if RUNNING in statuses:
            return RUNNING
        return PENDING

    # -- cancellation ----------------------------------------------------
    def cancel_run(self, run_id: str) -> dict:
        """Ask a run to stop.

        The flag is set in Redis rather than signalling the worker directly:
        the run may be on another machine, and the worker polls for it while
        the solve is going. A run that has not started yet is marked cancelled
        outright so it never begins.
        """
        record = self.store.get(RUNS, run_id)
        if record is None:
            raise KeyError(f"no such run: {run_id}")
        if record.get("status") in TERMINAL:
            return record

        flagged = self._flag_cancel(run_id)
        if record.get("status") == PENDING and not flagged:
            # Nothing is running it and nothing can be told to stop, so the
            # only honest outcome is to mark it cancelled here.
            record = {**record, "status": CANCELLED, "cancelled": True}
            self.store.put(RUNS, run_id, record)
        return record

    def cancel_experiment(self, experiment_id: str) -> list[dict]:
        return [self.cancel_run(r["_id"]) for r in self.runs_of(experiment_id)
                if r.get("status") not in TERMINAL]

    @staticmethod
    def _flag_cancel(run_id: str) -> bool:
        try:
            import redis

            url = os.environ.get("CELLGEN_REDIS_URL", "redis://127.0.0.1:6379/0")
            # Bounded, for the same reason dispatch does not retry: cancelling
            # must fail fast when there is no broker, not block the request.
            client = redis.Redis.from_url(
                url, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2)
            # Expires so a crashed run cannot leave a flag that cancels a
            # future re-dispatch of the same id.
            client.set(f"run:{run_id}:cancel", "1", ex=24 * 3600)
            return True
        except Exception as exc:
            logger.warning(f"[{run_id}] could not flag cancellation: {exc}")
            return False
