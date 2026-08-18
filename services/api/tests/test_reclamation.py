"""Reclaiming idle workspaces.

An idle sandbox is not free: on E2B it bills for as long as it runs, and
locally it leaves an opencode process behind. Pausing is the right disposal
because it loses nothing -- it snapshots first, and the next visit resumes into
the same conversation.

The reaper is tested by calling it, not by waiting for its loop.
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
async def service(tmp_path):
    from cellgen_api.backends.local import LocalBackend
    from cellgen_api.service import SandboxService
    from cellgen_api.store import FileStore

    backend = LocalBackend(
        root=tmp_path / "sandboxes",
        engine_src=REPO_ROOT / "engine",
        opencode_bin=OPENCODE_BIN or "opencode",
    )
    svc = SandboxService(backend, FileStore(tmp_path / "store"),
                         OPENCODE_BIN or "opencode")
    yield svc
    for record in svc.list_records():
        await svc.destroy(record["_id"], keep_snapshots=False)


@requires_opencode
async def test_a_new_workspace_is_not_immediately_idle(service):
    """Creating must stamp activity.

    Without a timestamp the idle time is measured from the epoch, so the very
    first reaper pass would pause a workspace the user just opened.
    """
    await service.create("ws")
    assert service.idle_seconds("ws") < 60
    assert await service.reap_idle(idle_after=300) == []


@requires_opencode
async def test_an_idle_workspace_is_paused_and_snapshotted(service):
    await service.create("ws")

    # Anything at all counts as idle when the threshold is zero-ish.
    paused = await service.reap_idle(idle_after=0.001)
    assert paused == ["ws"]
    assert (await service.status("ws"))["state"] == "paused"

    # Pausing snapshots first, so the conversation survives reclamation.
    snaps = [d for d in service.store.list("snapshots") if d["sandbox_id"] == "ws"]
    assert len(snaps) == 1


@requires_opencode
async def test_recent_activity_defers_reclamation(service):
    await service.create("ws")
    service.touch("ws")
    assert await service.reap_idle(idle_after=300) == []
    assert (await service.status("ws"))["state"] == "running"


@requires_opencode
async def test_proxy_traffic_counts_as_activity(service):
    """The embedded UI polls constantly; that is the real signal of use."""
    import httpx

    record = await service.create("ws")
    service._last_active["ws"] = 0.0  # pretend it has been idle for ever
    assert service.idle_seconds("ws") > 1000

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{record['proxy_url']}/session")
        assert resp.status_code == 200

    assert service.idle_seconds("ws") < 60
    assert await service.reap_idle(idle_after=300) == []


@requires_opencode
async def test_a_paused_workspace_is_left_alone(service):
    """Already reclaimed; the reaper must not keep pausing it."""
    await service.create("ws")
    await service.pause("ws")
    assert await service.reap_idle(idle_after=0.001) == []


@requires_opencode
async def test_a_reclaimed_workspace_resumes_with_its_conversation(service):
    """The whole reason pausing is an acceptable way to reclaim."""
    import httpx

    record = await service.create("ws")
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{record['proxy_url']}/session", json={"title": "kept"})

    assert await service.reap_idle(idle_after=0.001) == ["ws"]
    resumed = await service.resume("ws")
    assert resumed["state"] == "running"

    async with httpx.AsyncClient(timeout=30) as client:
        sessions = (await client.get(f"{resumed['proxy_url']}/session")).json()
    assert [s["title"] for s in sessions] == ["kept"]


async def test_reclamation_can_be_switched_off(service):
    """`CELLGEN_IDLE_PAUSE_SECONDS=0` means never reclaim."""
    assert await service.reap_idle(idle_after=0) == []


@requires_opencode
async def test_one_stuck_workspace_does_not_block_the_others(service, monkeypatch):
    await service.create("ws-a")
    await service.create("ws-b")

    real_pause = service.pause

    async def pause(sandbox_id, *args, **kwargs):
        if sandbox_id == "ws-a":
            raise RuntimeError("backend wedged")
        return await real_pause(sandbox_id, *args, **kwargs)

    monkeypatch.setattr(service, "pause", pause)

    assert await service.reap_idle(idle_after=0.001) == ["ws-b"]
