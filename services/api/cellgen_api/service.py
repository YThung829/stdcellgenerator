"""Sandbox lifecycle, tying the backend, the state store and persistence together.

The persisted record for a sandbox is deliberately thin: which backend holds
it, the backend's own id, and a pointer to the last snapshot. Everything else
is re-derived on resume, because a sandbox's address changes across a
pause/resume cycle and must never be read from a stale record.
"""

from __future__ import annotations

import time
from dataclasses import asdict

from loguru import logger

from cellgen_api.artifacts import Artifact, capture
from cellgen_api.backends.base import SandboxBackend, SandboxHandle, SandboxState
from cellgen_api.proxy import SandboxProxy
from cellgen_api.state import SandboxSnapshot, SandboxStateStore
from cellgen_api.store import Store, new_id
from cellgen_api.workspace import list_plugins, run_smoke

SANDBOXES = "sandboxes"
SNAPSHOTS = "snapshots"
ARTIFACTS = "artifacts"


class SandboxService:
    def __init__(self, backend: SandboxBackend, store: Store, opencode_bin: str):
        self.backend = backend
        self.store = store
        self.state_store = SandboxStateStore(backend, opencode_bin)
        # Live handles by sandbox id. Rebuilt from the store on demand.
        self._handles: dict[str, SandboxHandle] = {}
        # One root-path proxy per running sandbox; see proxy.py for why the
        # proxy cannot live under a path on this API.
        self._proxies: dict[str, SandboxProxy] = {}

    # -- records ---------------------------------------------------------
    def _record(self, handle: SandboxHandle, **extra) -> dict:
        proxy = self._proxies.get(handle.id)
        doc = {
            "_id": handle.id,
            "backend": handle.backend,
            "backend_id": handle.backend_id,
            "state": handle.state.value,
            "workdir": handle.workdir,
            # What the frontend iframes. Note this deliberately exposes no
            # sandbox address and no password.
            "proxy_url": proxy.url if proxy else None,
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

    def proxy_url(self, sandbox_id: str) -> str | None:
        proxy = self._proxies.get(sandbox_id)
        return proxy.url if proxy else None

    async def _start_proxy(self, handle: SandboxHandle) -> None:
        await self._stop_proxy(handle.id)
        if handle.state is not SandboxState.RUNNING or not handle.base_url:
            return
        proxy = SandboxProxy(handle.base_url, handle.password)
        await proxy.start()
        self._proxies[handle.id] = proxy

    async def _stop_proxy(self, sandbox_id: str) -> None:
        proxy = self._proxies.pop(sandbox_id, None)
        if proxy is not None:
            await proxy.stop()

    # -- lifecycle -------------------------------------------------------
    async def create(self, sandbox_id: str | None = None,
                     restore_from: str | None = None) -> dict:
        """Create a sandbox, optionally restoring a snapshot into it.

        Reusing an existing ``sandbox_id`` rebuilds that workspace: the backend
        gives it the same working directory, which is what lets restored
        opencode sessions attach to the right project.

        Creating an id that is already running is a no-op rather than a second
        sandbox. The frontend calls this on every reconnect, and a sandbox's
        opencode port is derived from its id -- so starting a duplicate would
        fail to bind and take the working sandbox down with it.
        """
        sandbox_id = sandbox_id or new_id("sbx")

        live = self._handles.get(sandbox_id)
        if live is not None and await self.backend.health(live):
            logger.info(f"[{sandbox_id}] already running; returning existing sandbox")
            if sandbox_id not in self._proxies:
                await self._start_proxy(live)
            return self._record(live, restored_sessions=0)

        handle = await self.backend.create(sandbox_id)
        self._handles[sandbox_id] = handle
        await self._start_proxy(handle)

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

    async def export_artifact(self, sandbox_id: str, name: str,
                              description: str = "") -> dict:
        """Capture the sandbox's work as a stored, experiment-ready artifact.

        This is the only route out of a sandbox: the sandbox cannot push, and
        nothing in it touches the upstream engine.
        """
        handle = self._require_handle(sandbox_id)
        artifact = await capture(self.backend, handle, name, description)
        return self.store.upsert(ARTIFACTS, {
            "_id": new_id("art"),
            "sandbox_id": sandbox_id,
            "name": name,
            "description": description,
            "artifact": artifact.to_dict(),
            "plugin_count": len(artifact.plugins),
            "patch_bytes": len(artifact.engine_patch),
            "changed_files": artifact.changed_files,
            "size_bytes": artifact.size_bytes,
        })

    async def plugins(self, sandbox_id: str) -> dict:
        """What is in the sandbox's plugin directory right now."""
        return await list_plugins(self.backend, self._require_handle(sandbox_id))

    async def smoke(self, sandbox_id: str, cell: str | None = None,
                    preset: str | None = None, max_time: int | None = None) -> dict:
        """Solve one small cell in the sandbox to check its plugins still work."""
        handle = self._require_handle(sandbox_id)
        kwargs = {k: v for k, v in
                  {"cell": cell, "preset": preset, "max_time": max_time}.items()
                  if v is not None}
        result = await run_smoke(self.backend, handle, **kwargs)
        doc = {"sandbox_id": sandbox_id, "finished_at": time.time(),
               **asdict(result)}
        # Kept on the sandbox record so the UI can show the last result
        # immediately on load, without re-running a solve.
        self.store.put(SANDBOXES, sandbox_id,
                       {**(self.get_record(sandbox_id) or {}), "last_smoke": doc})
        return doc

    def list_artifacts(self) -> list[dict]:
        return self.store.list(ARTIFACTS)

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        doc = self.store.get(ARTIFACTS, artifact_id)
        return Artifact.from_dict(doc["artifact"]) if doc else None

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
        await self._stop_proxy(sandbox_id)
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
        # The sandbox address changes across a pause/resume cycle, so the
        # proxy is rebuilt rather than reused.
        await self._start_proxy(handle)
        return self._record(handle)

    async def destroy(self, sandbox_id: str, keep_snapshots: bool = True) -> None:
        await self._stop_proxy(sandbox_id)
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
