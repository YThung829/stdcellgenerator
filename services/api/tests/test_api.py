"""API-level tests: lifecycle endpoints and the opencode reverse proxy.

Runs against the local backend with a real opencode process, so the proxy is
exercised against a real upstream rather than a mock.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

OPENCODE_BIN = os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode")
requires_opencode = pytest.mark.skipif(
    not OPENCODE_BIN, reason="no opencode binary available")


@pytest.fixture
async def client(tmp_path):
    """An app wired to a throwaway data root and the local backend."""
    import httpx
    from cellgen_api.backends.local import LocalBackend
    from cellgen_api.main import app
    from cellgen_api.service import SandboxService
    from cellgen_api.store import FileStore

    backend = LocalBackend(
        root=tmp_path / "sandboxes",
        engine_src=REPO_ROOT / "engine",
        opencode_bin=OPENCODE_BIN or "opencode",
    )
    app.state.service = SandboxService(
        backend, FileStore(tmp_path / "store"), OPENCODE_BIN or "opencode")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test", timeout=120) as c:
        yield c

    for record in app.state.service.list_records():
        await app.state.service.destroy(record["_id"], keep_snapshots=False)


async def test_health_needs_no_sandbox(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_unknown_sandbox_is_404(client):
    assert (await client.get("/api/sandboxes/nope")).status_code == 404


async def test_proxy_refuses_when_sandbox_not_running(client):
    resp = await client.get("/api/sandboxes/nope/oc/session")
    assert resp.status_code == 409


@requires_opencode
async def test_create_list_and_delete(client):
    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-api"})).json()
    assert created["state"] == "running"
    assert created["_id"] == "ws-api"

    listed = (await client.get("/api/sandboxes")).json()
    assert [s["_id"] for s in listed] == ["ws-api"]

    status = (await client.get("/api/sandboxes/ws-api")).json()
    assert status["healthy"] is True

    assert (await client.delete("/api/sandboxes/ws-api")).status_code == 204
    assert (await client.get("/api/sandboxes")).json() == []


@requires_opencode
async def test_proxy_reaches_opencode_without_leaking_credentials(client):
    await client.post("/api/sandboxes", json={"sandbox_id": "ws-proxy"})

    # The browser sends no credentials; the proxy supplies them server-side.
    resp = await client.get("/api/sandboxes/ws-proxy/oc/session")
    assert resp.status_code == 200
    assert resp.json() == []

    # And the sandbox password is never exposed through the API surface.
    record = (await client.get("/api/sandboxes/ws-proxy")).json()
    assert "password" not in record
    assert "base_url" not in record


@requires_opencode
async def test_proxy_forwards_writes(client):
    await client.post("/api/sandboxes", json={"sandbox_id": "ws-write"})
    created = await client.post("/api/sandboxes/ws-write/oc/session",
                                json={"title": "via proxy"})
    assert created.status_code == 200
    assert created.json()["title"] == "via proxy"

    listed = (await client.get("/api/sandboxes/ws-write/oc/session")).json()
    assert [s["title"] for s in listed] == ["via proxy"]


@requires_opencode
async def test_pause_snapshots_first_then_resume_restores(client):
    await client.post("/api/sandboxes", json={"sandbox_id": "ws-cycle"})
    await client.post("/api/sandboxes/ws-cycle/oc/session", json={"title": "work"})

    paused = (await client.post("/api/sandboxes/ws-cycle/pause")).json()
    assert paused["state"] == "paused"

    # Pausing must snapshot first -- a paused sandbox is not a backup.
    snaps = (await client.get("/api/sandboxes/ws-cycle/snapshots")).json()
    assert len(snaps) == 1
    assert snaps[0]["session_count"] == 1

    resumed = (await client.post("/api/sandboxes/ws-cycle/resume")).json()
    assert resumed["state"] == "running"

    sessions = (await client.get("/api/sandboxes/ws-cycle/oc/session")).json()
    assert [s["title"] for s in sessions] == ["work"]


@requires_opencode
async def test_rebuild_from_snapshot_after_total_loss(client):
    """The disposable-sandbox promise, through the HTTP API."""
    await client.post("/api/sandboxes", json={"sandbox_id": "ws-lost"})
    await client.post("/api/sandboxes/ws-lost/oc/session", json={"title": "precious"})
    await client.post("/api/sandboxes/ws-lost/snapshot")

    # Lose the sandbox completely, keeping only the snapshot.
    await client.delete("/api/sandboxes/ws-lost?keep_snapshots=true")

    rebuilt = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-lost"})).json()
    assert rebuilt["state"] == "running"
    assert rebuilt["restored_sessions"] == 1

    sessions = (await client.get("/api/sandboxes/ws-lost/oc/session")).json()
    assert [s["title"] for s in sessions] == ["precious"]
