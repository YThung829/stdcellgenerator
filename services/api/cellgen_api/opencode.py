"""Thin client for a running opencode server.

Only the parts the API needs: listing sessions (for health checks and for
deciding what to snapshot) and driving export/import.

Everything here goes through opencode's own supported interfaces rather than
its SQLite file. The Phase 0 spike established why: the on-disk schema is an
internal detail that has already been renamed once upstream, whereas
``export``/``import`` are documented commands that round-trip a session into a
completely empty data directory with its id intact.
"""

from __future__ import annotations

import json

import httpx
from loguru import logger


class OpenCodeError(Exception):
    pass


class OpenCodeClient:
    """REST client for one opencode server."""

    def __init__(self, base_url: str, password: str | None = None, timeout: float = 30):
        self.base_url = base_url.rstrip("/")
        self.auth = ("opencode", password) if password else None
        self.timeout = timeout

    async def _get(self, path: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}{path}", auth=self.auth)
            resp.raise_for_status()
            return resp.json()

    async def list_sessions(self) -> list[dict]:
        """Every session this server knows about.

        Also doubles as a health check: it needs no model provider configured.
        """
        return await self._get("/session")

    async def create_session(self, title: str | None = None) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/session", auth=self.auth,
                json={"title": title} if title else {},
            )
            resp.raise_for_status()
            return resp.json()


class OpenCodeCLI:
    """The subset of the opencode CLI that has no REST equivalent.

    ``export``/``import`` are CLI-only, and they are the whole basis of state
    capture, so they get run through the sandbox's exec channel.
    """

    def __init__(self, backend, handle, binary: str = "opencode"):
        self.backend = backend
        self.handle = handle
        self.binary = binary

    async def export_session(self, session_id: str) -> dict:
        """Export one session as JSON.

        The session id is required: with no argument the command blocks on an
        interactive picker, which would hang a request.
        """
        if not session_id:
            raise ValueError("session_id is required; a bare export blocks on a picker")
        code, out, err = await self.backend.exec(
            self.handle, [self.binary, "export", session_id], timeout=120)
        if code != 0:
            raise OpenCodeError(f"export {session_id} failed ({code}): {err[-500:]}")
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise OpenCodeError(f"export {session_id} returned non-JSON: {out[:200]}") from exc

    async def import_session(self, session: dict) -> str:
        """Import a previously exported session. Returns its id."""
        session_id = session.get("info", {}).get("id", "unknown")
        path = f"/tmp/import_{session_id}.json"
        await self.backend.write_file(self.handle, path, json.dumps(session))
        code, out, err = await self.backend.exec(
            self.handle, [self.binary, "import", path], timeout=120)
        if code != 0:
            raise OpenCodeError(f"import {session_id} failed ({code}): {err[-500:]}")
        logger.debug(f"imported session {session_id}: {out.strip()}")
        return session_id
