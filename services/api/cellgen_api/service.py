"""Sandbox lifecycle, tying the backend, the state store and persistence together.

The persisted record for a sandbox is deliberately thin: which backend holds
it, the backend's own id, and a pointer to the last snapshot. Everything else
is re-derived on resume, because a sandbox's address changes across a
pause/resume cycle and must never be read from a stale record.
"""

from __future__ import annotations

import time

from loguru import logger

from cellgen_api.backends.base import SandboxBackend, SandboxHandle, SandboxState
from cellgen_api.state import SandboxSnapshot, SandboxStateStore
from cellgen_api.store import Store, new_id

SANDBOXES = "sandboxes"
SNAPSHOTS = "snapshots"


class SandboxService:
    def __init__(self, backend: SandboxBackend, store: Store, opencode_bin: str):
        self.backend = backend
        self.store = store
        self.state_store = SandboxStateStore(backend, opencode_bin)
        # Live handles by sandbox id. Rebuilt from the store on demand.
        self._handles: dict[str, SandboxHandle] = {}

    # -- records ---------------------------------------------------------
    def _record(self, handle: SandboxHandle, **extra) -> dict:
        doc = {
            "_id": handle.id,
            "backend": handle.backend,
            "backend_id": handle.backend_id,
            "state": handle.state.value,
            "workdir": handle.workdir,
            "updated_at": time.time(),
            **extra,
        }
        existing = self.store.get(SANDBOXES, handle.id) or {}
        doc = {**existing, **doc}
        doc.setdefault("created_at", time.time())
        self.store.put(SANDBOXES, handle.id, doc)
        return doc

    def get_record(self, sandbox_id: str) -> dict | None:
        return self.store.get(SANDBOXES, sandbox_id)

    def list_records(self) -> list[dict]:
        return self.store.list(SANDBOXES)

    def handle(self, sandbox_id: str) -> SandboxHandle | None:
        return self._handles.get(sandbox_id)

    # -- lifecycle -------------------------------------------------------
    async def create(self, sandbox_id: str | None = None,
                     restore_from: str | None = None) -> dict:
        """Create a sandbox, optionally restoring a snapshot into it.

        Reusing an existing ``sandbox_id`` rebuilds that workspace: the backend
        gives it the same working directory, which is what lets restored
        opencode sessions attach to the right project.
        """
        sandbox_id = sandbox_id or new_id("sbx")
        handle = await self.backend.create(sandbox_id)
        self._handles[sandbox_id] = handle

        restored = 0
        if handle.state is SandboxState.RUNNING:
            snapshot = self._load_snapshot(sandbox_id, restore_from)
            if snapshot:
                restored = await self.state_store.restore(handle, snapshot)

        return self._record(handle, restored_sessions=restored)

    def _load_snapshot(self, sandbox_id: str,
                       snapshot_id: str | None) -> SandboxSnapshot | None:
        """Explicit snapshot if given, else this sandbox's most recent one."""
        if snapshot_id:
            doc = self.store.get(SNAPSHOTS, snapshot_id)
            return SandboxSnapshot.from_dict(doc["snapshot"]) if doc else None

        candidates = [d for d in self.store.list(SNAPSHOTS)
                      if d.get("sandbox_id") == sandbox_id]
        if not candidates:
            return None
        return SandboxSnapshot.from_dict(candidates[0]["snapshot"])

    async def snapshot(self, sandbox_id: str) -> dict:
        handle = self._require_handle(sandbox_id)
        snap = await self.state_store.snapshot(handle)
        doc = self.store.upsert(SNAPSHOTS, {
            "_id": new_id("snap"),
            "sandbox_id": sandbox_id,
            "snapshot": snap.to_dict(),
            "session_count": len(snap.sessions),
            "size_bytes": snap.size_bytes,
        })
        self._record(handle, last_snapshot_id=doc["_id"])
        return doc

    async def pause(self, sandbox_id: str) -> dict:
        """Snapshot first, then suspend.

        Snapshotting before pausing is the point: a paused sandbox is not a
        backup, and if it is later lost the snapshot is what rebuilds it.
        """
        handle = self._require_handle(sandbox_id)
        try:
            await self.snapshot(sandbox_id)
        except Exception:
            logger.exception(f"[{sandbox_id}] snapshot before pause failed; pausing anyway")
        handle = await self.backend.pause(handle)
        self._handles[sandbox_id] = handle
        return self._record(handle)

    async def resume(self, sandbox_id: str) -> dict:
        """Resume, re-resolving the address and restarting opencode if needed."""
        handle = self._handles.get(sandbox_id)
        if handle is None:
            # Nothing live for this id: rebuild from the last snapshot instead.
            logger.info(f"[{sandbox_id}] no live handle; rebuilding from snapshot")
            return await self.create(sandbox_id)

        handle = await self.backend.resume(handle)
        self._handles[sandbox_id] = handle
        return self._record(handle)

    async def destroy(self, sandbox_id: str, keep_snapshots: bool = True) -> None:
        handle = self._handles.pop(sandbox_id, None)
        if handle is not None:
            await self.backend.destroy(handle)
        self.store.delete(SANDBOXES, sandbox_id)
        if not keep_snapshots:
            for doc in self.store.list(SNAPSHOTS):
                if doc.get("sandbox_id") == sandbox_id:
                    self.store.delete(SNAPSHOTS, doc["_id"])

    async def status(self, sandbox_id: str) -> dict | None:
        """The stored record plus a live health check, or None if unknown.

        Returns None rather than an empty dict so callers can distinguish
        "no such sandbox" from "a sandbox with no interesting fields".
        """
        record = self.get_record(sandbox_id)
        if record is None:
            return None
        handle = self._handles.get(sandbox_id)
        record["healthy"] = bool(handle) and await self.backend.health(handle)
        return record

    def _require_handle(self, sandbox_id: str) -> SandboxHandle:
        handle = self._handles.get(sandbox_id)
        if handle is None:
            raise KeyError(
                f"sandbox {sandbox_id!r} is not running in this process; "
                f"resume it first"
            )
        return handle
