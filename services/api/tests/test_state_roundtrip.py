"""End-to-end check of the property the MVP rests on.

A sandbox must be disposable: throw it away, build a new one, and the
conversation history comes back. This drives the real opencode binary through
the local backend, so it exercises the actual export/import path that the E2B
backend will use unchanged.

Skipped unless an opencode binary is available (CELLGEN_OPENCODE_BIN, or
`opencode` on PATH).
"""

from __future__ import annotations

import shutil
import os
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.backends.base import SandboxState  # noqa: E402
from cellgen_api.backends.local import LocalBackend  # noqa: E402
from cellgen_api.opencode import OpenCodeClient  # noqa: E402
from cellgen_api.state import SandboxSnapshot, SandboxStateStore  # noqa: E402

OPENCODE_BIN = os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode")

# Applied per-test rather than module-wide: the serialisation test at the
# bottom is synchronous and needs neither mark.
requires_opencode = pytest.mark.skipif(
    not OPENCODE_BIN, reason="no opencode binary available")


@pytest.fixture
def backend(tmp_path):
    return LocalBackend(
        root=tmp_path / "sandboxes",
        engine_src=REPO_ROOT / "engine",
        opencode_bin=OPENCODE_BIN,
    )


@requires_opencode
async def test_create_starts_a_healthy_opencode(backend):
    handle = await backend.create("ws-health")
    try:
        assert handle.state is SandboxState.RUNNING, f"see {handle.meta.get('log')}"
        assert await backend.health(handle)
        assert (Path(handle.workdir) / "src" / "cellgen" / "run.py").is_file(), \
            "engine was not copied into the sandbox"
    finally:
        await backend.destroy(handle)


@requires_opencode
async def test_history_survives_destroying_the_sandbox(backend):
    """The MVP's headline requirement."""
    store = SandboxStateStore(backend, opencode_bin=OPENCODE_BIN)

    # 1. a sandbox with a conversation in it
    handle = await backend.create("ws-restore")
    assert handle.state is SandboxState.RUNNING, f"see {handle.meta.get('log')}"
    client = OpenCodeClient(handle.base_url, handle.password)
    created = await client.create_session("constraint work")
    original_id = created["id"]

    # 2. snapshot, then throw the sandbox away entirely
    snapshot = await store.snapshot(handle)
    assert len(snapshot.sessions) == 1
    assert snapshot.sessions[0]["info"]["id"] == original_id
    await backend.destroy(handle)

    # 3. a brand new sandbox under the same workspace id has no history
    rebuilt = await backend.create("ws-restore")
    try:
        assert rebuilt.state is SandboxState.RUNNING, f"see {rebuilt.meta.get('log')}"
        fresh_client = OpenCodeClient(rebuilt.base_url, rebuilt.password)
        assert await fresh_client.list_sessions() == [], \
            "a rebuilt sandbox should start empty"

        # 4. restore brings the conversation back, with its identity intact
        restored = await store.restore(rebuilt, snapshot)
        assert restored == 1

        sessions = await fresh_client.list_sessions()
        assert [s["id"] for s in sessions] == [original_id]
        assert sessions[0]["title"] == "constraint work"
    finally:
        await backend.destroy(rebuilt)


@requires_opencode
async def test_snapshot_is_small(backend):
    """The stored state must stay tiny -- that was an explicit requirement."""
    store = SandboxStateStore(backend, opencode_bin=OPENCODE_BIN)
    handle = await backend.create("ws-size")
    try:
        client = OpenCodeClient(handle.base_url, handle.password)
        await client.create_session("sizing")
        snap = await store.snapshot(handle)

        data_dir = Path(handle.backend_id) / "data"
        on_disk = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file())

        assert snap.size_bytes < 10_000, f"snapshot unexpectedly large: {snap.size_bytes}"
        assert snap.size_bytes < on_disk, (
            f"snapshot ({snap.size_bytes} B) should be far smaller than the "
            f"opencode data dir it replaces ({on_disk} B)"
        )
    finally:
        await backend.destroy(handle)


@requires_opencode
async def test_pause_then_resume_keeps_history(backend):
    """The everyday path: stop the process, start it again, history intact."""
    handle = await backend.create("ws-pause")
    try:
        client = OpenCodeClient(handle.base_url, handle.password)
        created = await client.create_session("before pause")

        await backend.pause(handle)
        assert handle.state is SandboxState.PAUSED
        assert not await backend.health(handle), "opencode still answering after pause"

        handle = await backend.resume(handle)
        assert handle.state is SandboxState.RUNNING

        client = OpenCodeClient(handle.base_url, handle.password)
        sessions = await client.list_sessions()
        assert [s["id"] for s in sessions] == [created["id"]]
    finally:
        await backend.destroy(handle)


def test_snapshot_serialises_round_trip():
    snap = SandboxSnapshot(
        sandbox_id="a", sessions=[{"info": {"id": "s1"}, "messages": []}], workdir="/w")
    assert SandboxSnapshot.from_dict(snap.to_dict()).to_dict() == snap.to_dict()
