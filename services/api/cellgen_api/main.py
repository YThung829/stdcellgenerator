"""CellGenerator Studio API.

Tab 1 (the constraint development area) needs three things from the backend:
sandboxes it can create and throw away, conversation state that survives that,
and a same-origin route to the sandbox's opencode UI.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from cellgen_api.artifacts import ArtifactError
from cellgen_api.config import settings
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
    app.state.service = SandboxService(backend, store, settings.opencode_bin)
    logger.info(f"API up: backend={backend.name} engine={settings.engine_src}")
    yield
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
