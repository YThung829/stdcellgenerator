"""E2B sandbox backend.

Written against the interface the local backend proves out, and checked method
by method against the installed ``e2b`` SDK -- see ``tests/test_e2b_backend.py``,
which fails if the SDK stops offering a call this file makes. What is *not*
verified is the live service: no reachable E2B endpoint exists in the
development environment, so every round trip below is unexercised against a
real sandbox. Treat first contact as a bring-up, not a regression run.

Two invariants from ``base`` matter most here:

* the working directory is ``/workspace/engine`` in the template, constant
  across every sandbox, which is what makes restored sessions attach correctly.
  E2B has no notion of "the same sandbox id again" -- a rebuild is a brand new
  sandbox -- so that fixed path is the *only* thing tying a restored session
  back to its project;
* ``XDG_DATA_HOME`` is set in the template so opencode's storage is where the
  state layer expects it.

Pausing uses ``keep_memory=False``: opencode keeps its state in SQLite on disk,
so there is nothing in RAM worth preserving, and a disk-only pause is smaller
and faster (E2B charges roughly 4 seconds per GB of RAM to pause). The cost is
that resuming cold-boots the sandbox, so opencode is never still running --
which is why resume always health-checks and restarts it.
"""

from __future__ import annotations

import secrets
import shlex

from loguru import logger

from cellgen_api.backends.base import (
    OPENCODE_PORT,
    SandboxBackend,
    SandboxHandle,
    SandboxState,
    WORKDIR,
)

# How long E2B keeps a sandbox alive without being told otherwise. The SDK's
# own default is minutes, which would kill a workspace mid-thought; idle
# workspaces are meant to be reclaimed deliberately by the service's reaper,
# not dropped by a timeout nobody set.
DEFAULT_SANDBOX_TIMEOUT = 3600


class E2BBackend(SandboxBackend):
    name = "e2b"

    def __init__(self, api_key: str | None, template: str,
                 opencode_port: int = OPENCODE_PORT,
                 sandbox_timeout: int = DEFAULT_SANDBOX_TIMEOUT):
        if not api_key:
            raise RuntimeError(
                "E2B_API_KEY is not set. Either configure it, or run with "
                "CELLGEN_BACKEND=local to use a local opencode process."
            )
        self.api_key = api_key
        self.template = template
        self.port = opencode_port
        self.sandbox_timeout = sandbox_timeout
        # Connecting costs an API round trip *and* resumes a paused sandbox,
        # so the object is kept rather than re-derived per call.
        self._sandboxes: dict[str, object] = {}

    def _sdk(self):
        try:
            from e2b import AsyncSandbox
        except ImportError as exc:  # pragma: no cover - depends on install
            raise RuntimeError(
                "the e2b package is not installed; `pip install e2b`"
            ) from exc
        return AsyncSandbox

    @staticmethod
    def shell(cmd: list[str]) -> str:
        """Render an argv list for E2B, which runs commands through a shell.

        Quoting is not cosmetic here. Artifact capture passes git pathspecs
        such as ``:(exclude)plugins/*``; unquoted, the shell treats the
        parentheses as a syntax error and expands the glob against the wrong
        directory. The local backend never sees this because it execs argv
        directly.
        """
        return " ".join(shlex.quote(part) for part in cmd)

    async def _sandbox(self, handle: SandboxHandle):
        """The live sandbox object for this handle, connecting if needed.

        ``connect`` resumes a paused sandbox as a side effect, so this must
        never be used on the pause path.
        """
        if (cached := self._sandboxes.get(handle.id)) is not None:
            return cached
        AsyncSandbox = self._sdk()
        sbx = await AsyncSandbox.connect(
            handle.backend_id, timeout=self.sandbox_timeout, api_key=self.api_key)
        self._sandboxes[handle.id] = sbx
        return sbx

    async def create(self, sandbox_id: str) -> SandboxHandle:
        AsyncSandbox = self._sdk()
        password = secrets.token_urlsafe(16)
        sbx = await AsyncSandbox.create(
            template=self.template,
            timeout=self.sandbox_timeout,
            # Tagged so a sandbox can still be identified in the E2B dashboard,
            # or recovered by listing, if this service loses its records.
            metadata={"cellgen_sandbox_id": sandbox_id},
            envs={"OPENCODE_SERVER_PASSWORD": password},
            api_key=self.api_key,
        )
        handle = SandboxHandle(
            id=sandbox_id,
            backend=self.name,
            backend_id=sbx.sandbox_id,
            state=SandboxState.STARTING,
            workdir=WORKDIR,
            password=password,
        )
        self._sandboxes[sandbox_id] = sbx
        await self._start_opencode(sbx, handle)
        await self._record_baseline(handle)
        return handle

    async def _record_baseline(self, handle: SandboxHandle) -> None:
        """Remember the template's commit, so exports can diff against it.

        Artifact capture refuses to run without this; the local backend takes
        it from the repo it just copied, and here it comes from the template's
        own checkout.
        """
        try:
            code, out, _ = await self.exec(
                handle, ["git", "rev-parse", "HEAD"], timeout=30)
            if code == 0:
                handle.meta["baseline_commit"] = out.strip()
            else:
                logger.warning(f"[{handle.id}] no baseline commit; exports will fail")
        except Exception:
            logger.exception(f"[{handle.id}] could not read the baseline commit")

    async def _start_opencode(self, sbx, handle: SandboxHandle) -> None:
        await sbx.commands.run(
            f"opencode serve --port {self.port} --hostname 0.0.0.0",
            background=True, cwd=handle.workdir,
            envs={"OPENCODE_SERVER_PASSWORD": handle.password or ""},
        )
        # get_host is synchronous and returns host:port without a scheme.
        handle.base_url = f"https://{sbx.get_host(self.port)}"
        healthy = await self.wait_healthy(handle)
        handle.state = SandboxState.RUNNING if healthy else SandboxState.FAILED
        if healthy:
            logger.info(f"[{handle.id}] opencode up at {handle.base_url}")
        else:
            logger.error(f"[{handle.id}] opencode did not become healthy")

    async def resume(self, handle: SandboxHandle) -> SandboxHandle:
        """Bring a paused sandbox back.

        The SDK has no ``resume``: connecting to a paused sandbox resumes it.
        Because pausing drops memory, this is a cold boot -- opencode is not
        running afterwards, and the address has changed -- so both are
        re-derived rather than trusted.
        """
        self._sandboxes.pop(handle.id, None)
        sbx = await self._sandbox(handle)
        handle.base_url = f"https://{sbx.get_host(self.port)}"
        if await self.health(handle):
            handle.state = SandboxState.RUNNING
        else:
            await self._start_opencode(sbx, handle)
        return handle

    async def pause(self, handle: SandboxHandle) -> SandboxHandle:
        """Suspend the sandbox, keeping its filesystem.

        Addressed by id rather than through a connected object: connecting
        would resume the sandbox first, so pausing that way would round-trip a
        paused sandbox back up and straight down again.
        """
        AsyncSandbox = self._sdk()
        await AsyncSandbox.pause(
            sandbox_id=handle.backend_id, keep_memory=False, api_key=self.api_key)
        self._sandboxes.pop(handle.id, None)
        handle.state = SandboxState.PAUSED
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        AsyncSandbox = self._sdk()
        try:
            # By id, for the same reason as pause: no need to wake it to kill it.
            await AsyncSandbox.kill(sandbox_id=handle.backend_id, api_key=self.api_key)
        finally:
            # Drop the cached object even if the kill failed -- it is not
            # something to keep handing out.
            self._sandboxes.pop(handle.id, None)
            handle.state = SandboxState.STOPPED

    async def exec(self, handle: SandboxHandle, cmd: list[str],
                   cwd: str | None = None, timeout: float = 120) -> tuple[int, str, str]:
        sbx = await self._sandbox(handle)
        result = await sbx.commands.run(
            self.shell(cmd), cwd=cwd or handle.workdir, timeout=timeout)
        return result.exit_code, result.stdout, result.stderr

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        sbx = await self._sandbox(handle)
        await sbx.files.write(self._abs(handle, path), content)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        sbx = await self._sandbox(handle)
        return await sbx.files.read(self._abs(handle, path), format="text")

    @staticmethod
    def _abs(handle: SandboxHandle, path: str) -> str:
        """Resolve a workdir-relative path.

        Callers pass paths like ``plugins/manifest.json`` because that is what
        the local backend accepts; E2B's filesystem API resolves relative paths
        against the home directory, not the working directory.
        """
        return path if path.startswith("/") else f"{handle.workdir.rstrip('/')}/{path}"
