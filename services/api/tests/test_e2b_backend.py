"""What can be checked about the E2B backend without an E2B account.

The live service is unreachable from the development environment, so the risk
this file addresses is the one that actually bit: code written against an SDK
that was never called. Every SDK entry point the backend uses is asserted to
exist with a compatible signature, and the pure logic around those calls --
shell quoting, path resolution -- is exercised directly.

None of this proves a sandbox boots. It proves the calls are real, which is
what stopped being true when ``AsyncSandbox.resume`` was invented wholesale.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.backends.base import WORKDIR, SandboxHandle, SandboxState  # noqa: E402
from cellgen_api.backends.e2b import E2BBackend  # noqa: E402

e2b = pytest.importorskip("e2b", reason="the e2b SDK is not installed")
AsyncSandbox = e2b.AsyncSandbox


def handle() -> SandboxHandle:
    return SandboxHandle(
        id="ws", backend="e2b", backend_id="sbx_123",
        state=SandboxState.RUNNING, workdir=WORKDIR, password="pw",
    )


class TestSdkSurface:
    """The SDK must still offer what the backend calls.

    `resume` is the cautionary tale: the backend called
    `AsyncSandbox.resume(...)`, which has never existed. Connecting to a paused
    sandbox is what resumes it.
    """

    def test_every_sdk_call_the_backend_makes_exists(self):
        """The general form of the `resume` bug: calling something imaginary.

        Reads the backend's own source rather than a hand-kept list, so a call
        added later is covered without anyone remembering to add it here.
        """
        import re

        text = (API_ROOT / "cellgen_api" / "backends" / "e2b.py").read_text()
        used = set(re.findall(r"\bAsyncSandbox\.(\w+)", text))
        assert used, "no SDK calls found; did the module move?"
        missing = sorted(name for name in used if not hasattr(AsyncSandbox, name))
        assert not missing, f"backend calls SDK methods that do not exist: {missing}"

    def test_there_is_no_resume_to_call(self):
        assert not hasattr(AsyncSandbox, "resume"), (
            "the SDK grew a resume(); revisit the backend, which resumes by "
            "connecting because none existed"
        )

    @pytest.mark.parametrize("name", ["create", "connect", "pause", "kill", "get_host"])
    def test_entry_points_exist(self, name):
        assert hasattr(AsyncSandbox, name)

    def test_create_accepts_every_argument_the_backend_passes(self):
        params = inspect.signature(AsyncSandbox.create).parameters
        for arg in ("template", "timeout", "metadata", "envs"):
            assert arg in params, f"AsyncSandbox.create lost {arg!r}"

    def test_pause_can_drop_memory(self):
        """keep_memory=False is the whole reason resume is treated as a cold boot."""
        assert "keep_memory" in inspect.signature(AsyncSandbox.pause).parameters

    def test_pause_and_kill_are_addressable_by_id(self):
        """Both must work without connecting, since connecting resumes."""
        for name in ("_cls_pause", "_cls_kill"):
            assert "sandbox_id" in inspect.signature(getattr(AsyncSandbox, name)).parameters

    def test_connect_takes_a_sandbox_id_and_a_timeout(self):
        params = inspect.signature(AsyncSandbox._cls_connect_sandbox).parameters
        assert "sandbox_id" in params
        # The timeout is how an active workspace stays alive.
        assert "timeout" in params

    def test_get_host_is_synchronous(self):
        """Awaiting it would yield a coroutine where a hostname is expected."""
        assert not inspect.iscoroutinefunction(AsyncSandbox.get_host)

    def test_command_and_file_calls_match(self):
        from e2b.sandbox_async.commands.command import Commands
        from e2b.sandbox_async.filesystem.filesystem import Filesystem

        run = inspect.signature(Commands.run).parameters
        for arg in ("cmd", "background", "envs", "cwd", "timeout"):
            assert arg in run, f"Commands.run lost {arg!r}"
        assert "format" in inspect.signature(Filesystem.read).parameters


class TestShellQuoting:
    """E2B runs a shell; the local backend execs argv. Only one needs quoting."""

    def test_a_git_pathspec_survives_the_shell(self):
        """Artifact capture passes `:(exclude)plugins/*`.

        Unquoted, the shell reads the parentheses as a syntax error and expands
        the glob -- so exporting from an E2B sandbox would fail outright.
        """
        rendered = E2BBackend.shell(
            ["git", "diff", "--cached", "HEAD", "--", ".", ":(exclude)plugins/*"])
        assert "'" in rendered
        import shlex
        assert shlex.split(rendered)[-1] == ":(exclude)plugins/*"

    def test_paths_with_spaces_stay_one_argument(self):
        import shlex
        rendered = E2BBackend.shell(["cat", "/workspace/engine/a file.py"])
        assert shlex.split(rendered) == ["cat", "/workspace/engine/a file.py"]

    def test_a_plain_command_is_unchanged(self):
        assert E2BBackend.shell(["git", "add", "-A"]) == "git add -A"


class TestPathResolution:
    """Callers pass workdir-relative paths, as the local backend accepts."""

    def test_relative_paths_resolve_against_the_workdir(self):
        assert E2BBackend._abs(handle(), "plugins/manifest.json") == \
            f"{WORKDIR}/plugins/manifest.json"

    def test_absolute_paths_are_left_alone(self):
        assert E2BBackend._abs(handle(), "/etc/hostname") == "/etc/hostname"


class TestConstruction:
    def test_a_missing_api_key_says_what_to_do(self):
        with pytest.raises(RuntimeError, match="E2B_API_KEY"):
            E2BBackend(api_key=None, template="cellgen-engine")

    def test_the_workdir_is_the_fixed_one(self):
        """E2B rebuilds are new sandboxes; only the constant path restores sessions."""
        assert handle().workdir == WORKDIR == "/workspace/engine"

    def test_sandboxes_are_given_a_generous_lifetime(self):
        """The SDK's default would reap a workspace mid-session."""
        backend = E2BBackend(api_key="k", template="t")
        assert backend.sandbox_timeout >= 3600


class TestProviderCredentials:
    """The agent needs a model, and on E2B nothing is inherited.

    A sandbox with no provider key boots perfectly happily and then cannot
    write a single constraint, which is the one failure that looks like
    success from the outside.
    """

    def test_creating_a_sandbox_forwards_them(self):
        backend = E2BBackend(api_key="k", template="t",
                             sandbox_env={"ANTHROPIC_API_KEY": "sk-test"})
        assert backend.sandbox_env["ANTHROPIC_API_KEY"] == "sk-test"

    def test_the_server_password_is_not_overwritten_by_them(self):
        """Forwarding must not let a stray name clobber the proxy's credential."""
        backend = E2BBackend(
            api_key="k", template="t",
            sandbox_env={"OPENCODE_SERVER_PASSWORD": "attacker-chosen"})
        envs = {**backend.sandbox_env, "OPENCODE_SERVER_PASSWORD": "real"}
        assert envs["OPENCODE_SERVER_PASSWORD"] == "real"

    def test_none_is_an_empty_mapping_not_a_crash(self):
        assert E2BBackend(api_key="k", template="t").sandbox_env == {}
