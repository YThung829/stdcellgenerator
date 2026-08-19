"""Run opencode as a local process instead of in a remote sandbox.

This exists so the whole sandbox layer -- state capture and restore, the
reverse proxy, the lifecycle endpoints -- can be built and tested without E2B
credentials. It is also the fastest way to work on the API day to day.

Differences from a real sandbox, all deliberate:

* No isolation. Plugin code runs as the current user. Fine for local
  development, never for anything multi-tenant.
* ``pause`` stops the process rather than snapshotting memory; ``resume``
  starts it again. Since opencode keeps its state in SQLite on disk, that
  restores the conversation just as a real resume would -- which is exactly
  why the plan chose ``keepMemory=False`` for E2B too.
* ``workdir`` is a real path on this machine rather than ``/workspace/engine``.
  The path still has to be *stable across rebuilds*, so it is derived from the
  sandbox id and reused.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import signal
from pathlib import Path

from loguru import logger

from cellgen_api.backends.base import (
    OPENCODE_CONFIG,
    OPENCODE_PORT,
    SandboxBackend,
    SandboxHandle,
    SandboxState,
)


class LocalBackend(SandboxBackend):
    """A sandbox that is just a subprocess on this machine."""

    name = "local"

    def __init__(self, root: Path, engine_src: Path, opencode_bin: str = "opencode",
                 port_base: int = OPENCODE_PORT,
                 sandbox_env: dict[str, str] | None = None):
        self.root = Path(root)
        self.engine_src = Path(engine_src)
        self.opencode_bin = opencode_bin
        self.port_base = port_base
        # A local sandbox would inherit these anyway; setting them explicitly
        # keeps the two backends configured the same way rather than one of
        # them working by accident.
        self.sandbox_env = dict(sandbox_env or {})
        self.root.mkdir(parents=True, exist_ok=True)
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    # -- layout ----------------------------------------------------------
    def _home(self, sandbox_id: str) -> Path:
        return self.root / sandbox_id

    def workdir_for(self, sandbox_id: str) -> Path:
        """Stable per-sandbox engine checkout. Must not change across restarts."""
        return self._home(sandbox_id) / "engine"

    def _data_home(self, sandbox_id: str) -> Path:
        """What XDG_DATA_HOME points at; opencode stores its DB under here."""
        return self._home(sandbox_id) / "data"

    def _port(self, sandbox_id: str) -> int:
        # Deterministic per sandbox so a resume reuses the same port.
        return self.port_base + (abs(hash(sandbox_id)) % 1000)

    # -- lifecycle -------------------------------------------------------
    async def create(self, sandbox_id: str) -> SandboxHandle:
        home = self._home(sandbox_id)
        workdir = self.workdir_for(sandbox_id)
        if not workdir.exists():
            logger.info(f"[{sandbox_id}] copying engine -> {workdir}")
            shutil.copytree(
                self.engine_src, workdir,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "output", ".git", ".pytest_cache"),
            )
        (workdir / "plugins").mkdir(exist_ok=True)
        self._data_home(sandbox_id).mkdir(parents=True, exist_ok=True)
        self._ensure_opencode_config(workdir)
        await self._ensure_git_repo(workdir)

        handle = SandboxHandle(
            id=sandbox_id,
            backend=self.name,
            backend_id=str(home),
            state=SandboxState.STARTING,
            workdir=str(workdir),
            password=secrets.token_urlsafe(16),
        )
        # The baseline commit is what "what changed in this sandbox" is
        # measured against when the user exports their work.
        code, out, _ = await self._git(workdir, "rev-parse", "HEAD")
        if code == 0:
            handle.meta["baseline_commit"] = out.strip()
        return await self._start(handle)

    @staticmethod
    async def _git(workdir: Path, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=workdir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    @staticmethod
    def _ensure_opencode_config(workdir: Path) -> None:
        """Register the engine's MCP server with this workspace's opencode.

        Merged rather than overwritten: the agent may well have written its own
        settings into this file, and dropping them to install one server would
        be a poor trade.
        """
        path = workdir / "opencode.json"
        config: dict = {}
        if path.is_file():
            try:
                config = json.loads(path.read_text())
            except json.JSONDecodeError:
                logger.warning(f"{path} is not valid JSON; replacing it")
                config = {}

        mcp = dict(config.get("mcp") or {})
        mcp.update(OPENCODE_CONFIG["mcp"])
        config = {**OPENCODE_CONFIG, **config, "mcp": mcp}
        path.write_text(json.dumps(config, indent=2) + "\n")

    @staticmethod
    async def _ensure_git_repo(workdir: Path) -> None:
        """Make the sandbox workdir a git repo.

        opencode identifies a *project* by its git worktree. Without a repo it
        files every session under the catch-all "global" project rooted at "/",
        so the UI shows no project and restored sessions have nothing to attach
        to. A repo also lets the agent diff its own edits.
        """
        if (workdir / ".git").exists():
            return
        for cmd in (
            ["git", "init", "-q"],
            ["git", "add", "-A"],
            ["git", "-c", "user.email=sandbox@cellgen", "-c", "user.name=CellGen",
             "commit", "-q", "-m", "Sandbox baseline"],
        ):
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=workdir,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"{' '.join(cmd)} failed in {workdir}: {err.decode()[:200]}")
                return

    async def _start(self, handle: SandboxHandle) -> SandboxHandle:
        sandbox_id = handle.id
        port = self._port(sandbox_id)

        env = dict(os.environ)
        # The one env var that actually pins opencode's storage (see the
        # Phase 0 spike; OPENCODE_DATA_DIR is ignored).
        env.update(self.sandbox_env)
        env["XDG_DATA_HOME"] = str(self._data_home(sandbox_id))
        env["OPENCODE_SERVER_PASSWORD"] = handle.password or ""
        env["PYTHONUNBUFFERED"] = "1"
        env["MPLBACKEND"] = "Agg"

        log_path = self._home(sandbox_id) / "opencode.log"
        log_file = open(log_path, "ab")
        proc = await asyncio.create_subprocess_exec(
            self.opencode_bin, "serve", "--port", str(port), "--hostname", "127.0.0.1",
            cwd=handle.workdir, env=env,
            stdout=log_file, stderr=asyncio.subprocess.STDOUT,
            # Own process group, so stopping the sandbox kills any child the
            # agent spawned (a solve, say) rather than orphaning it.
            start_new_session=True,
        )
        self._procs[sandbox_id] = proc
        handle.base_url = f"http://127.0.0.1:{port}"
        handle.meta["pid"] = proc.pid
        handle.meta["log"] = str(log_path)

        if await self.wait_healthy(handle):
            handle.state = SandboxState.RUNNING
            logger.info(f"[{sandbox_id}] opencode up at {handle.base_url} (pid {proc.pid})")
        else:
            handle.state = SandboxState.FAILED
            logger.error(f"[{sandbox_id}] opencode did not become healthy; see {log_path}")
        return handle

    async def resume(self, handle: SandboxHandle) -> SandboxHandle:
        if await self.health(handle):
            handle.state = SandboxState.RUNNING
            return handle
        return await self._start(handle)

    async def pause(self, handle: SandboxHandle) -> SandboxHandle:
        await self._stop_process(handle)
        handle.state = SandboxState.PAUSED
        return handle

    async def destroy(self, handle: SandboxHandle) -> None:
        await self._stop_process(handle)
        shutil.rmtree(self._home(handle.id), ignore_errors=True)
        handle.state = SandboxState.STOPPED

    async def _stop_process(self, handle: SandboxHandle) -> None:
        proc = self._procs.pop(handle.id, None)
        pid = proc.pid if proc else handle.meta.get("pid")
        if pid is None:
            return
        # SIGTERM then SIGKILL the whole group -- the same shape the existing
        # GUI runner uses so a long solve cannot survive the sandbox.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(pid), sig)
            except ProcessLookupError:
                return
            for _ in range(20):
                await asyncio.sleep(0.1)
                try:
                    os.killpg(os.getpgid(pid), 0)
                except ProcessLookupError:
                    return

    # -- filesystem / exec ----------------------------------------------
    async def exec(self, handle: SandboxHandle, cmd: list[str],
                   cwd: str | None = None, timeout: float = 120) -> tuple[int, str, str]:
        env = dict(os.environ)
        env["XDG_DATA_HOME"] = str(self._data_home(handle.id))
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd or handle.workdir, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            raise
        return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")

    def _resolve(self, handle: SandboxHandle, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else Path(handle.workdir) / p

    async def write_file(self, handle: SandboxHandle, path: str, content: str) -> None:
        target = self._resolve(handle, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    async def read_file(self, handle: SandboxHandle, path: str) -> str:
        return self._resolve(handle, path).read_text()
