"""Capture and restore a sandbox's conversation state.

The requirement is that a sandbox can be thrown away and the next one comes
back with the full history, while what we actually store stays as small as
possible.

What gets stored is the exported session JSON and nothing else. Measured in the
Phase 0 spike: an empty session is 634 bytes, against 308 KB for the opencode
data directory it lives in, and the JSON grows only with conversation length --
not with the engine, its dependencies, or any solver output.

Explicitly *not* stored:

* the engine checkout -- it is baked into the sandbox image
* Python dependencies -- likewise
* solver output (``.res``/``.var``/``.log``/``.png``) -- reproducible, and
  megabytes each
* plugin sources -- they are versioned separately as constraint documents,
  which is the whole point of the plugin layer
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from cellgen_api.backends.base import SandboxBackend, SandboxHandle
from cellgen_api.opencode import OpenCodeCLI, OpenCodeClient


@dataclass
class SandboxSnapshot:
    """Everything needed to rebuild a sandbox's conversation state."""

    sandbox_id: str
    sessions: list[dict] = field(default_factory=list)
    workdir: str = ""

    @property
    def size_bytes(self) -> int:
        import json

        return len(json.dumps(self.sessions).encode())

    def to_dict(self) -> dict:
        return {
            "sandbox_id": self.sandbox_id,
            "sessions": self.sessions,
            "workdir": self.workdir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SandboxSnapshot":
        return cls(
            sandbox_id=d["sandbox_id"],
            sessions=d.get("sessions", []),
            workdir=d.get("workdir", ""),
        )


class SandboxStateStore:
    """Snapshot and restore conversation state via opencode's export/import."""

    def __init__(self, backend: SandboxBackend, opencode_bin: str = "opencode"):
        self.backend = backend
        self.opencode_bin = opencode_bin

    async def snapshot(self, handle: SandboxHandle) -> SandboxSnapshot:
        """Export every session on this sandbox."""
        client = OpenCodeClient(handle.base_url, handle.password)
        cli = OpenCodeCLI(self.backend, handle, self.opencode_bin)

        sessions_meta = await client.list_sessions()
        exported: list[dict] = []
        for meta in sessions_meta:
            session_id = meta.get("id")
            if not session_id:
                continue
            try:
                exported.append(await cli.export_session(session_id))
            except Exception:
                # One unexportable session must not cost us the others.
                logger.exception(f"failed to export session {session_id}; skipping")

        snap = SandboxSnapshot(
            sandbox_id=handle.id, sessions=exported, workdir=handle.workdir)
        logger.info(
            f"[{handle.id}] snapshot: {len(exported)}/{len(sessions_meta)} session(s), "
            f"{snap.size_bytes} bytes"
        )
        return snap

    async def restore(self, handle: SandboxHandle, snapshot: SandboxSnapshot) -> int:
        """Import a snapshot's sessions. Returns how many were restored.

        Warns when the working directory differs from the one the snapshot was
        taken in: opencode scopes a session to its absolute directory, so the
        sessions would import but not show up under this project.
        """
        if snapshot.workdir and snapshot.workdir != handle.workdir:
            logger.warning(
                f"[{handle.id}] snapshot was taken in {snapshot.workdir!r} but this "
                f"sandbox is {handle.workdir!r}; sessions are scoped to their "
                f"absolute directory and may not appear under this project"
            )

        cli = OpenCodeCLI(self.backend, handle, self.opencode_bin)
        restored = 0
        for session in snapshot.sessions:
            try:
                await cli.import_session(session)
                restored += 1
            except Exception:
                logger.exception("failed to import a session; continuing")

        logger.info(f"[{handle.id}] restored {restored}/{len(snapshot.sessions)} session(s)")
        return restored
