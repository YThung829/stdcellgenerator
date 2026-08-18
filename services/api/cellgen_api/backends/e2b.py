"""E2B sandbox backend.

Not exercised yet -- no API key is configured in this environment -- so it is
written against the interface the local backend already proves out, and left
explicit about what remains to be verified against the real service.

The two invariants from ``base`` matter most here:

* the working directory is ``/workspace/engine`` in the template, constant
  across every sandbox, which is what makes restored sessions attach correctly;
* ``XDG_DATA_HOME`` is set in the template so opencode's storage is where the
  state layer expects it.

Pausing uses ``keep_memory=False``: opencode keeps its state in SQLite on disk,
so there is nothing in RAM worth preserving, and a disk-only pause is smaller
and faster (E2B charges roughly 4 seconds per GB of RAM to pause).
"""

from __future__ import annotations

from cellgen_api.backends.base import (
    OPENCODE_PORT,
    SandboxBackend,
    SandboxHandle,
    SandboxState,
    WORKDIR,
)


class E2BBackend(SandboxBackend):
    name = "e2b"

    def __init__(self, api_key: str | None, template: str,
                 opencode_port: int = OPENCODE_PORT):
        if not api_key:
            raise RuntimeError(
                "E2B_API_KEY is not set. Either configure it, or run with "
                "CELLGEN_BACKEND=local to use a local opencode process."
            )
        self.api_key = api_key
        self.template = template
        self.port = opencode_port

    def _sdk(self):
        try:
            from e2b import AsyncSandbox
        except ImportError as exc:  # pragma: no cover - depends on install
            raise RuntimeError(
                "the e2b package is not installed; `pip install e2b`"
            ) from exc
        return AsyncSandbox

    async def create(self, sandbox_id: str) -> SandboxHandle:
        import secrets

        AsyncSandbox = self._sdk()
        password = secrets.token_urlsafe(16)
        sbx = await AsyncSandbox.create(
            template=self.template,
            api_key=self.api_key,
            envs={"OPENCODE_SERVER_PASSWORD": password},
        )
        handle = SandboxHandle(
            id=sandbox_id,
            backend=self.name,
            backend_id=sbx.sandbox_id,
            state=SandboxState.STARTING,
            workdir=WORKDIR,
            password=password,
        )
        await self._start_opencode(sbx, handle)
        return handle

    async def _start_opencode(self, sbx, handle: SandboxHandle) -> None:
        await sbx.commands.run(
            f"opencode serve --port {self.port} --hostname 0.0.0.0",
            background=True, cwd=handle.workdir,
            envs={"OPENCODE_SERVER_PASSWORD": handle.password or ""},
        )
        handle.base_url = f"https://{sbx.get_host(self.port)}"
        handle.state = (
            SandboxState.RUNNING if await self.health(handle) else SandboxState.FAILED
        )

    async def _connect(self, handle: SandboxHandle):
        AsyncSandbox = self._sdk()
        return await AsyncSandbox.connect(handle.backend_id, api_key=self.api_key)

    async def resume(self, handle: SandboxHandle) -> SandboxHandle:
        AsyncSandbox = self._sdk()
        sbx = await AsyncSandbox.resume(handle.backend_id, api_key=self.api_key)
        # The address changes across a pause/resume cycle and E2B does not
        # reconnect clients, so re-resolve rather than trusting the record.
        handle.base_url = f"https://{sbx.get_host(self.port)}"
        if not await self.health(handle):
            # keep_memory=False means a cold boot: opencode is not running.
            await self._start_opencode(sbx, handle)
        else:
            handle.state = SandboxState.RUNNING
        return handle

    async def pause(self, handle: SandboxHandle) -> SandboxHandle:
        sbx = await self._connect(handle)
        await sbx.pause(keep_memory=False)
        handle.state = SandboxState.PAUSED
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        sbx = await self._connect(handle)
        await sbx.kill()
        handle.state = SandboxState.STOPPED

    async def exec(self, handle: SandboxHandle, cmd: list[str],
                   cwd: str | None = None, timeout: float = 120) -> tuple[int, str, str]:
        sbx = await self._connect(handle)
        result = await sbx.commands.run(
            " ".join(cmd), cwd=cwd or handle.workdir, timeout=timeout)
        return result.exit_code, result.stdout, result.stderr

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        sbx = await self._connect(handle)
        await sbx.files.write(path, content)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        sbx = await self._connect(handle)
        return await sbx.files.read(path)
