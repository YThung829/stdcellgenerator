"""A sandbox must hand its agent the engine's MCP tools.

The tools themselves are tested in the engine's own suite. What matters here is
the wiring: a freshly created sandbox has to carry an `opencode.json` that the
sandbox's opencode actually accepts, or the agent is back to guessing.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.backends.base import OPENCODE_CONFIG  # noqa: E402

OPENCODE_BIN = os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode")
requires_opencode = pytest.mark.skipif(
    not OPENCODE_BIN, reason="no opencode binary available")


@pytest.fixture
async def backend(tmp_path):
    from cellgen_api.backends.local import LocalBackend

    yield LocalBackend(
        root=tmp_path / "sandboxes",
        engine_src=REPO_ROOT / "engine",
        opencode_bin=OPENCODE_BIN or "opencode",
    )


@requires_opencode
async def test_a_new_sandbox_registers_the_cellgen_server(backend):
    handle = await backend.create("ws")
    try:
        config = json.loads(
            (Path(handle.workdir) / "opencode.json").read_text())
    finally:
        await backend.destroy(handle)

    assert config["mcp"]["cellgen"]["enabled"] is True
    assert config["mcp"]["cellgen"]["command"] == [
        "python", "-m", "src.cellgen.mcp.server"]


@requires_opencode
async def test_opencode_itself_accepts_the_config(backend):
    """The shape is opencode's to define, so ask opencode.

    A config it silently ignores looks identical to a working one from here.
    """
    handle = await backend.create("ws")
    try:
        code, out, err = await backend.exec(
            handle, [OPENCODE_BIN or "opencode", "mcp", "list"], timeout=120)
    finally:
        await backend.destroy(handle)

    assert code == 0, err[-500:]
    assert "cellgen" in out, out[-500:]


def test_an_existing_config_keeps_its_own_settings(tmp_path):
    """The agent may have written settings here; installing must not erase them."""
    from cellgen_api.backends.local import LocalBackend

    workdir = tmp_path / "engine"
    workdir.mkdir()
    (workdir / "opencode.json").write_text(json.dumps({
        "theme": "opencode",
        "mcp": {"other": {"type": "local", "command": ["true"], "enabled": True}},
    }))

    LocalBackend._ensure_opencode_config(workdir)
    config = json.loads((workdir / "opencode.json").read_text())

    assert config["theme"] == "opencode"
    assert "other" in config["mcp"]
    assert "cellgen" in config["mcp"]


def test_unparseable_config_is_replaced_rather_than_fatal(tmp_path):
    from cellgen_api.backends.local import LocalBackend

    workdir = tmp_path / "engine"
    workdir.mkdir()
    (workdir / "opencode.json").write_text("{ not json")

    LocalBackend._ensure_opencode_config(workdir)
    config = json.loads((workdir / "opencode.json").read_text())
    assert config["mcp"]["cellgen"] == OPENCODE_CONFIG["mcp"]["cellgen"]
