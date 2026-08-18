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
    resp = await client.get("/api/sandboxes/nope/proxy")
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
    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-proxy"})).json()
    proxy_url = created["proxy_url"]
    assert proxy_url

    # The browser sends no credentials; the proxy supplies them server-side.
    async with httpx.AsyncClient(timeout=30) as direct:
        resp = await direct.get(f"{proxy_url}/session")
        assert resp.status_code == 200
        assert resp.json() == []

    # Neither the sandbox address nor its password is exposed to the client.
    record = (await client.get("/api/sandboxes/ws-proxy")).json()
    assert "password" not in record
    assert "base_url" not in record


@requires_opencode
async def test_proxy_serves_the_ui_with_working_assets(client):
    """The subpath proxy this replaced returned 404 for every asset.

    opencode's UI references `/assets/...` absolutely, so a proxy mounted under
    a path serves the HTML but none of the JavaScript. Serving at the root is
    the whole reason for a per-sandbox port.
    """
    import re

    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-assets"})).json()
    proxy_url = created["proxy_url"]

    async with httpx.AsyncClient(timeout=30) as direct:
        page = await direct.get(f"{proxy_url}/")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]

        assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
        assert assets, "no bundled assets referenced; page shape changed?"
        for asset in assets:
            resp = await direct.get(f"{proxy_url}{asset}")
            assert resp.status_code == 200, f"asset 404 through proxy: {asset}"


@requires_opencode
async def test_proxy_forwards_writes(client):
    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-write"})).json()
    proxy_url = created["proxy_url"]

    async with httpx.AsyncClient(timeout=30) as direct:
        made = await direct.post(f"{proxy_url}/session", json={"title": "via proxy"})
        assert made.status_code == 200
        assert made.json()["title"] == "via proxy"

        listed = (await direct.get(f"{proxy_url}/session")).json()
        assert [s["title"] for s in listed] == ["via proxy"]


@requires_opencode
async def test_proxy_does_not_mislabel_compressed_responses(client):
    """A compressed body must keep its `content-encoding`.

    The proxy forwards bodies raw, so dropping the header handed the browser
    gzip bytes labelled `application/json`: opencode's UI loaded but could
    decode none of its own API responses.
    """
    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-gzip"})).json()
    proxy_url = created["proxy_url"]

    async with httpx.AsyncClient(timeout=60) as direct:
        # A large enough payload that the upstream actually compresses it.
        resp = await direct.get(f"{proxy_url}/config/providers",
                                headers={"accept-encoding": "gzip"})
        assert resp.status_code == 200
        # httpx decodes per the header, so this only parses if the two agree.
        assert "providers" in resp.json()

        plain = await direct.get(f"{proxy_url}/config/providers",
                                 headers={"accept-encoding": "identity"})
        assert "content-encoding" not in plain.headers
        assert "providers" in plain.json()


@requires_opencode
async def test_creating_a_running_sandbox_is_a_no_op(client):
    """Re-creating a live id must not start a second, port-clashing sandbox.

    The frontend re-issues this on every reconnect. Spawning a duplicate left
    the original unreachable, because a sandbox's opencode port comes from its
    id and the second process could not bind it.
    """
    import httpx

    first = (await client.post("/api/sandboxes",
                               json={"sandbox_id": "ws-twice"})).json()
    async with httpx.AsyncClient(timeout=30) as direct:
        await direct.post(f"{first['proxy_url']}/session", json={"title": "keep me"})

    again = (await client.post("/api/sandboxes",
                               json={"sandbox_id": "ws-twice"})).json()
    assert again["state"] == "running"
    assert again["proxy_url"] == first["proxy_url"]

    # The original sandbox is untouched, work and all.
    async with httpx.AsyncClient(timeout=30) as direct:
        sessions = (await direct.get(f"{again['proxy_url']}/session")).json()
    assert [s["title"] for s in sessions] == ["keep me"]


@requires_opencode
async def test_pause_snapshots_first_then_resume_restores(client):
    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-cycle"})).json()
    async with httpx.AsyncClient(timeout=30) as direct:
        await direct.post(f"{created['proxy_url']}/session", json={"title": "work"})

    paused = (await client.post("/api/sandboxes/ws-cycle/pause")).json()
    assert paused["state"] == "paused"

    # Pausing must snapshot first -- a paused sandbox is not a backup.
    snaps = (await client.get("/api/sandboxes/ws-cycle/snapshots")).json()
    assert len(snaps) == 1
    assert snaps[0]["session_count"] == 1

    resumed = (await client.post("/api/sandboxes/ws-cycle/resume")).json()
    assert resumed["state"] == "running"
    # A resumed sandbox gets a fresh proxy: its address changed.
    assert resumed["proxy_url"]

    async with httpx.AsyncClient(timeout=30) as direct:
        sessions = (await direct.get(f"{resumed['proxy_url']}/session")).json()
    assert [s["title"] for s in sessions] == ["work"]


@requires_opencode
async def test_rebuild_from_snapshot_after_total_loss(client):
    """The disposable-sandbox promise, through the HTTP API."""
    import httpx

    created = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-lost"})).json()
    async with httpx.AsyncClient(timeout=30) as direct:
        await direct.post(f"{created['proxy_url']}/session", json={"title": "precious"})
    await client.post("/api/sandboxes/ws-lost/snapshot")

    # Lose the sandbox completely, keeping only the snapshot.
    await client.delete("/api/sandboxes/ws-lost?keep_snapshots=true")

    rebuilt = (await client.post("/api/sandboxes",
                                 json={"sandbox_id": "ws-lost"})).json()
    assert rebuilt["state"] == "running"
    assert rebuilt["restored_sessions"] == 1

    async with httpx.AsyncClient(timeout=30) as direct:
        sessions = (await direct.get(f"{rebuilt['proxy_url']}/session")).json()
    assert [s["title"] for s in sessions] == ["precious"]
