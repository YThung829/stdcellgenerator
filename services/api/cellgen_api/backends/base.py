"""The sandbox abstraction.

A sandbox is somewhere an opencode server runs with the engine checked out, so
a user can develop constraints against it. In production that is an E2B
sandbox; in development it is a local process. Both must behave the same from
the API's point of view, because the state layer, the reverse proxy and the
lifecycle endpoints are written against this interface, not against E2B.

Two invariants every backend must uphold, both established by the Phase 0
spike (``docs/opencode-state-spike.md``):

1. ``workdir`` is byte-identical across rebuilds. An opencode session records
   the absolute directory it belongs to, so a restored session attaches to the
   wrong project if the path moves.
2. ``XDG_DATA_HOME`` pins opencode's storage. (``OPENCODE_DATA_DIR``, which the
   plan originally assumed, is ignored by opencode.)
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum

# Fixed so that opencode sessions restore into the same project identity
# no matter which backend or which rebuild created them.
WORKDIR = "/workspace/engine"
OPENCODE_PORT = 4096

# Registers the engine's MCP server with the sandbox's opencode, so the agent
# can ask the live engine what exists and whether a constraint solves instead
# of inferring both from AGENTS.md. Written into the workdir at sandbox
# creation; the E2B template bakes in the identical file.
OPENCODE_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        "cellgen": {
            "type": "local",
            # Run as a module so it resolves through the installed engine
            # rather than a path that differs between backends.
            "command": ["python", "-m", "src.cellgen.mcp.server"],
            "enabled": True,
        }
    },
}


class SandboxState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class SandboxHandle:
    """A sandbox the API can address.

    ``backend_id`` is whatever the backend needs to find it again -- an E2B
    sandbox id, or a local process id. The API stores it and nothing else,
    which is what keeps the persisted state to a single string.
    """

    id: str
    backend: str
    backend_id: str
    state: SandboxState
    workdir: str = WORKDIR
    base_url: str | None = None
    password: str | None = None
    meta: dict = field(default_factory=dict)


class SandboxBackend(abc.ABC):
    """Create, address and tear down sandboxes running opencode."""

    name: str

    @abc.abstractmethod
    async def create(self, sandbox_id: str) -> SandboxHandle:
        """Provision a sandbox with the engine present and opencode serving."""

    @abc.abstractmethod
    async def resume(self, handle: SandboxHandle) -> SandboxHandle:
        """Bring a paused sandbox back and return its current address.

        The address changes across a pause/resume cycle, and opencode may need
        restarting, so implementations must re-resolve ``base_url`` and health
        check rather than trusting the stored value.
        """

    @abc.abstractmethod
    async def pause(self, handle: SandboxHandle) -> SandboxHandle:
        """Suspend the sandbox, keeping its filesystem."""

    @abc.abstractmethod
    async def destroy(self, handle: SandboxHandle) -> None:
        """Tear the sandbox down for good."""

    @abc.abstractmethod
    async def exec(self, handle: SandboxHandle, cmd: list[str],
                   cwd: str | None = None, timeout: float = 120) -> tuple[int, str, str]:
        """Run a command inside the sandbox. Returns ``(returncode, stdout, stderr)``."""

    @abc.abstractmethod
    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        """Write a text file inside the sandbox."""

    @abc.abstractmethod
    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        """Read a text file from inside the sandbox."""

    async def health(self, handle: SandboxHandle) -> bool:
        """True when opencode is answering on this sandbox.

        ``GET /session`` needs no model provider configured, so this costs
        nothing and works on a freshly booted sandbox.
        """
        import httpx

        if not handle.base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{handle.base_url}/session",
                                        auth=self._auth(handle))
                return resp.status_code == 200
        except Exception:
            return False

    async def wait_healthy(self, handle: SandboxHandle, timeout: float = 60,
                           interval: float = 0.4) -> bool:
        """Poll ``health`` until it passes, or give up.

        opencode takes seconds to start listening, so checking once right after
        launching it reports every sandbox as failed.
        """
        import asyncio

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self.health(handle):
                return True
            await asyncio.sleep(interval)
        return False

    @staticmethod
    def _auth(handle: SandboxHandle):
        return ("opencode", handle.password) if handle.password else None
