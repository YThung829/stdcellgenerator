"""The MCP tools, driven over a real stdio MCP session.

Exercised through an actual client rather than by calling the functions
directly: the point of this server is that opencode can reach it, and a tool
that is registered wrongly, or returns something unserialisable, only fails at
that boundary.

Each test opens its own session inside its own frame. That is not a style
choice -- `stdio_client` runs on an anyio task group, and pytest-asyncio tears
an async-generator fixture down in a different task than it set it up in, which
anyio refuses ("exit cancel scope in a different task"). One tool per test
keeps the number of server starts down.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("mcp", reason="the mcp package is not installed")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

PLUGIN = '''
from src.cellgen.plugins import constraint


@constraint(id="mcp_probe", stage="post_routing", tech=["FinFET"],
            params={"cap": 5}, description="A plugin written for the test.")
def mcp_probe(inst, params):
    inst.opt.Add(sum(inst.edge_vars.values()) <= 10_000)
'''

INFEASIBLE = '''
from src.cellgen.plugins import constraint


@constraint(id="mcp_impossible", stage="post_routing", tech=["FinFET"],
            params={}, description="Deliberately unsatisfiable.")
def mcp_impossible(inst, params):
    inst.opt.Add(sum(inst.edge_vars.values()) <= 0)
    inst.opt.Add(sum(inst.edge_vars.values()) >= 1)
'''


class Client:
    """Thin wrapper that unwraps a tool result into a Python value."""

    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, tool: str, /, **args):
        # Positional-only, so a tool argument called `name` cannot collide
        # with this parameter -- `export_constraint` takes exactly that.
        result = await self.session.call_tool(tool, args)
        text = "".join(c.text for c in result.content if hasattr(c, "text"))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


@asynccontextmanager
async def mcp_client():
    """Start the server the way opencode does and open a session on it."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.cellgen.mcp.server"],
        cwd=str(REPO_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield Client(session)


@pytest.fixture
def plugin_file(tmp_path):
    """Write a plugin into the workspace the server reads, and clean up.

    The server resolves its plugin directory from the engine root, so the file
    has to land there rather than in a tmp_path the subprocess cannot see.
    """
    plugins = REPO_ROOT / "plugins"
    plugins.mkdir(exist_ok=True)
    written: list[Path] = []

    def write(name: str, source: str) -> Path:
        path = plugins / name
        path.write_text(source)
        written.append(path)
        return path

    yield write

    for path in written:
        path.unlink(missing_ok=True)


async def test_the_server_advertises_its_tools():
    async with mcp_client() as client:
        listed = await client.session.list_tools()
        names = {t.name for t in listed.tools}
    assert {"describe_context", "list_plugins", "validate_constraint",
            "run_smoke", "export_constraint"} <= names


async def test_describe_context_reports_the_real_engine():
    """The tool that stops the agent inventing variable names."""
    async with mcp_client() as client:
        doc = await client.call("describe_context")
        section = await client.call("describe_context", section="lgg")
        unknown_tech = await client.call("describe_context", tech="GaN")
        unknown_section = await client.call("describe_context", section="zzz")

    # Containers that genuinely exist on the orchestrator.
    assert "inst.edge_vars" in doc
    assert "inst.lgg" in doc

    assert "lgg." in section
    assert len(section) < len(doc), "filtering returned the whole document"

    assert "Unknown technology" in unknown_tech
    # A miss must point back at the full reference rather than look empty.
    assert "no section matching" in unknown_section.lower()


async def test_validate_constraint_separates_a_bad_file_from_a_hard_one(plugin_file):
    """Fails in milliseconds where a solve takes seconds."""
    good = plugin_file("mcp_probe.py", PLUGIN)
    broken = plugin_file("mcp_broken.py", "def oops(:\n    pass\n")
    empty = plugin_file("mcp_empty.py", "X = 1\n")

    async with mcp_client() as client:
        ok = await client.call("validate_constraint", path=str(good))
        syntax = await client.call("validate_constraint", path=str(broken))
        nothing = await client.call("validate_constraint", path=str(empty))
        missing = await client.call("validate_constraint", path="nope.py")

    assert ok["ok"] is True
    assert (ok["id"], ok["stage"]) == ("mcp_probe", "post_routing")
    assert ok["params"] == {"cap": 5}

    assert syntax["ok"] is False and syntax["error"]
    assert nothing["ok"] is False and "constraint" in nothing["error"].lower()
    assert missing["ok"] is False and "no such file" in missing["error"]


async def test_list_plugins_sees_the_workspace(plugin_file):
    plugin_file("mcp_probe.py", PLUGIN)
    async with mcp_client() as client:
        listed = await client.call("list_plugins")
    assert "mcp_probe" in [p["id"] for p in listed["plugins"]]


@pytest.mark.slow
async def test_run_smoke_solves_and_reports_what_the_plugin_added(plugin_file):
    plugin_file("mcp_probe.py", PLUGIN)
    async with mcp_client() as client:
        result = await client.call("run_smoke", max_time=60)

    assert result["ok"] is True
    assert result["status"] == "ok"
    assert result["objective"] == pytest.approx(1021.0)

    delta = next(d for d in result["plugin_deltas"] if d["id"] == "mcp_probe")
    assert delta["constraints_added"] == 1


@pytest.mark.slow
async def test_an_unsatisfiable_plugin_is_an_answer_not_a_crash(plugin_file):
    """INFEASIBLE comes back as a result, with no objective borrowed from before."""
    plugin_file("mcp_impossible.py", INFEASIBLE)
    async with mcp_client() as client:
        result = await client.call("run_smoke", max_time=30)
        # The server is still answering, which is why the solve is a subprocess.
        still_alive = await client.call("list_plugins")

    assert result["ok"] is False
    assert result["status"] == "INFEASIBLE"
    assert "objective" not in result
    assert result["log_tail"]
    assert still_alive["plugins"] is not None


async def test_export_explains_itself_when_unconfigured():
    """Without an API to reach, it must say what to do instead."""
    async with mcp_client() as client:
        result = await client.call(
            "export_constraint", path="plugins/x.py", name="x")
    assert result["ok"] is False
    assert "CELLGEN_API_BASE" in result["error"]
