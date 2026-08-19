"""CellGenerator Studio API.

Tab 1 (the constraint development area) needs three things from the backend:
sandboxes it can create and throw away, conversation state that survives that,
and a same-origin route to the sandbox's opencode UI.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from cellgen_api.artifacts import ArtifactError
from cellgen_api.config import settings
from cellgen_api.experiments import ExperimentService, ExperimentSpec
from cellgen_api.runlog import follow
from cellgen_api.service import SandboxService
from cellgen_api.store import build_store


def build_backend():
    if settings.backend == "e2b":
        from cellgen_api.backends.e2b import E2BBackend

        return E2BBackend(
            api_key=settings.e2b_api_key,
            template=settings.e2b_template,
            opencode_port=4096,
        )
    from cellgen_api.backends.local import LocalBackend

    return LocalBackend(
        root=settings.sandbox_root,
        engine_src=settings.engine_src,
        opencode_bin=settings.opencode_bin,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = build_store(settings.mongo_url, settings.store_root)
    backend = build_backend()
    service = SandboxService(backend, store, settings.opencode_bin)
    app.state.service = service
    # Shares the store with the sandbox service: an artifact exported from a
    # workspace is what an experiment runs.
    app.state.experiments = ExperimentService(store)
    logger.info(f"API up: backend={backend.name} engine={settings.engine_src}")

    # Idle workspaces cost money on E2B and leak processes locally, and pausing
    # loses nothing -- it snapshots first. Started here rather than in the
    # service so tests get a service that never reclaims behind their back.
    reaper = asyncio.create_task(service.run_reaper(
        settings.idle_pause_seconds, settings.reaper_interval_seconds))
    try:
        yield
    finally:
        reaper.cancel()
        with suppress(asyncio.CancelledError):
            await reaper
    # Leave sandboxes running: an API restart should not destroy a user's work.
    logger.info("API shutting down; sandboxes left as-is")


app = FastAPI(title="CellGenerator Studio API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def service(request: Request) -> SandboxService:
    return request.app.state.service


def experiments(request: Request) -> ExperimentService:
    return request.app.state.experiments


class CreateSandbox(BaseModel):
    sandbox_id: str | None = None
    restore_from: str | None = None


class ExportArtifact(BaseModel):
    name: str
    description: str = ""


class RunSmoke(BaseModel):
    """All optional: the defaults are the engine's own smoke-test cell."""

    cell: str | None = None
    preset: str | None = None
    max_time: int | None = None


@app.get("/api/health")
async def health():
    return {"ok": True, "backend": settings.backend}


@app.get("/api/sandboxes")
async def list_sandboxes(request: Request):
    return service(request).list_records()


@app.post("/api/sandboxes", status_code=201)
async def create_sandbox(body: CreateSandbox, request: Request):
    return await service(request).create(
        sandbox_id=body.sandbox_id, restore_from=body.restore_from)


@app.get("/api/sandboxes/{sandbox_id}")
async def get_sandbox(sandbox_id: str, request: Request):
    # An open tab polls this, so it counts as activity in its own right --
    # otherwise a workspace being watched but not typed into looks idle.
    service(request).touch(sandbox_id)
    record = await service(request).status(sandbox_id)
    if record is None:
        raise HTTPException(404, f"no such sandbox: {sandbox_id}")
    return record


@app.post("/api/sandboxes/{sandbox_id}/snapshot")
async def snapshot_sandbox(sandbox_id: str, request: Request):
    try:
        return await service(request).snapshot(sandbox_id)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/sandboxes/{sandbox_id}/pause")
async def pause_sandbox(sandbox_id: str, request: Request):
    try:
        return await service(request).pause(sandbox_id)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/sandboxes/{sandbox_id}/resume")
async def resume_sandbox(sandbox_id: str, request: Request):
    return await service(request).resume(sandbox_id)


@app.delete("/api/sandboxes/{sandbox_id}", status_code=204)
async def delete_sandbox(sandbox_id: str, request: Request, keep_snapshots: bool = True):
    await service(request).destroy(sandbox_id, keep_snapshots=keep_snapshots)


@app.get("/api/sandboxes/{sandbox_id}/snapshots")
async def list_snapshots(sandbox_id: str, request: Request):
    return [d for d in service(request).store.list("snapshots")
            if d.get("sandbox_id") == sandbox_id]


@app.get("/api/sandboxes/{sandbox_id}/proxy")
async def sandbox_proxy_url(sandbox_id: str, request: Request):
    """Where to point the iframe for this sandbox.

    Each running sandbox gets its own root-path proxy on an ephemeral port --
    opencode's UI loads its assets from absolute paths, so it cannot be served
    from under a subpath. See proxy.py.
    """
    url = service(request).proxy_url(sandbox_id)
    if url is None:
        raise HTTPException(409, f"sandbox {sandbox_id} is not running")
    return {"proxy_url": url}


@app.get("/api/sandboxes/{sandbox_id}/plugins")
async def sandbox_plugins(sandbox_id: str, request: Request):
    """The constraint plugins currently in this sandbox.

    Read from the sandbox on every call: the agent edits these files directly,
    so anything cached here would be stale as soon as it does.
    """
    try:
        return await service(request).plugins(sandbox_id)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/sandboxes/{sandbox_id}/smoke")
async def sandbox_smoke(body: RunSmoke, sandbox_id: str, request: Request):
    """Solve one small cell with this sandbox's plugins loaded.

    The inner loop of constraint authoring: does the engine still solve, and
    at what objective. Compare objectives, never layouts -- see
    docs/solve-reproducibility.md.
    """
    try:
        return await service(request).smoke(
            sandbox_id, cell=body.cell, preset=body.preset,
            max_time=body.max_time)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/sandboxes/{sandbox_id}/export", status_code=201)
async def export_artifact(sandbox_id: str, body: ExportArtifact, request: Request):
    """Capture this sandbox's work as an artifact an experiment can run.

    Everything the user changed travels as files: plugins individually, so a
    run can toggle and re-parameterise them, and any other edit as a patch
    against the sandbox baseline.
    """
    try:
        return await service(request).export_artifact(
            sandbox_id, body.name, body.description)
    except KeyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ArtifactError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/artifacts")
async def list_artifacts(request: Request):
    """Artifacts available to import into an experiment."""
    return [
        {k: v for k, v in doc.items() if k != "artifact"}
        for doc in service(request).list_artifacts()
    ]


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, request: Request):
    artifact = service(request).get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, f"no such artifact: {artifact_id}")
    return artifact.to_dict()


@app.get("/api/builtins")
async def list_builtins():
    """The engine's own constraints, each switchable per experiment.

    Read from the engine rather than stored, so a constraint added to the
    engine appears here without a second list to maintain. An experiment turns
    one off through its artifact's manifest.
    """
    try:
        from src.cellgen.plugins import builtins as builtin_registry
    except ImportError as exc:  # pragma: no cover - engine not installed
        raise HTTPException(
            503, f"the engine is not importable from this API: {exc}") from exc
    return {"builtins": builtin_registry.catalogue()}


# ---------------------------------------------------------------- experiments


class CreateExperiment(BaseModel):
    name: str
    preset: str
    cells: list[str]
    artifact_id: str | None = None
    description: str = ""
    overrides: list[str] = []
    # Always applied: the engine's own default is no limit at all.
    max_time: int = 300


@app.post("/api/experiments", status_code=201)
async def create_experiment(body: CreateExperiment, request: Request):
    """Queue one run per cell.

    Returns immediately with every run `pending`. Dispatch failures do not
    fail the request -- the runs are recorded with the reason so they can be
    re-sent -- because losing the experiment would be worse than delaying it.
    """
    try:
        return experiments(request).create(ExperimentSpec(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/experiments")
async def list_experiments(request: Request):
    return experiments(request).list()


@app.get("/api/experiments/{experiment_id}")
async def get_experiment(experiment_id: str, request: Request):
    experiment = experiments(request).get(experiment_id)
    if experiment is None:
        raise HTTPException(404, f"no such experiment: {experiment_id}")
    return experiment


@app.post("/api/experiments/{experiment_id}/cancel")
async def cancel_experiment(experiment_id: str, request: Request):
    if experiments(request).get(experiment_id) is None:
        raise HTTPException(404, f"no such experiment: {experiment_id}")
    return experiments(request).cancel_experiment(experiment_id)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    run = experiments(request).run(run_id)
    if run is None:
        raise HTTPException(404, f"no such run: {run_id}")
    return run


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    try:
        return experiments(request).cancel_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/runs/{run_id}/log")
async def stream_run_log(run_id: str, request: Request):
    """Follow a run's log: everything so far, then each line as it lands.

    Server-sent events, so the browser reconnects on its own and an ordinary
    proxy does not break it.
    """
    run = experiments(request).run(run_id)
    if run is None:
        raise HTTPException(404, f"no such run: {run_id}")

    log_path = run.get("artifacts", {}).get("log")
    return StreamingResponse(
        follow(run_id, Path(log_path) if log_path else None),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx buffers event streams by default, which defeats the point.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs/{run_id}/artifacts/{kind}")
async def download_run_artifact(run_id: str, kind: str, request: Request):
    """Serve one of a run's output files (`res`, `var`, `png`, `gds`, `log`)."""
    run = experiments(request).run(run_id)
    if run is None:
        raise HTTPException(404, f"no such run: {run_id}")

    path = (run.get("artifacts") or {}).get(kind)
    if not path or not Path(path).is_file():
        raise HTTPException(404, f"run {run_id} has no {kind!r} artifact")
    return FileResponse(path, filename=f"{run.get('cell', run_id)}.{kind}")
