"""What the constraint-development UI reads out of a sandbox.

The plugin listing is pure parsing and is tested directly; the smoke endpoint
runs a real solve inside a real sandbox, so it is marked and skips without an
opencode binary like the rest of the sandbox tests.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.workspace import describe_plugin  # noqa: E402

OPENCODE_BIN = os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode")
requires_opencode = pytest.mark.skipif(
    not OPENCODE_BIN, reason="no opencode binary available")

PLUGIN = '''
"""Cap vias sharing a column."""

from src.cellgen.plugins import constraint


@constraint(
    id="max_vias_per_col",
    stage="post_routing",
    tech=["FinFET", "QFET"],
    params={"max_vias": 2},
    description="Cap the number of vias sharing one column.",
)
def max_vias_per_col(inst, params):
    inst.opt.Add(sum(inst.edge_vars.values()) <= params["max_vias"])
'''

INFEASIBLE = '''
from src.cellgen.plugins import constraint


@constraint(id="impossible", stage="post_routing", tech=["FinFET"],
            params={}, description="Deliberately unsatisfiable.")
def impossible(inst, params):
    inst.opt.Add(sum(inst.edge_vars.values()) <= 0)
    inst.opt.Add(sum(inst.edge_vars.values()) >= 1)
'''


class TestDescribePlugin:
    """Metadata is parsed, never imported: plugins only execute in a sandbox."""

    def test_reads_the_constraint_metadata(self):
        info = describe_plugin("plugins/p.py", PLUGIN)
        assert (info.id, info.stage) == ("max_vias_per_col", "post_routing")
        assert info.tech == ["FinFET", "QFET"]
        assert info.params == {"max_vias": 2}
        assert info.description.startswith("Cap the number")
        assert info.error == ""

    def test_a_broken_file_is_listed_with_its_error(self):
        """A syntax error must stay visible rather than drop out of the list."""
        info = describe_plugin("plugins/broken.py", "def oops(:\n    pass\n")
        assert info.error
        assert info.id == ""

    def test_a_file_without_a_constraint_says_so(self):
        info = describe_plugin("plugins/helper.py", "X = 1\n")
        assert info.error == "no @constraint decorator found"

    def test_importing_is_never_attempted(self):
        """Parsing means a plugin with side effects cannot touch this process."""
        source = "raise SystemExit('should never run')\n" + PLUGIN
        info = describe_plugin("plugins/hostile.py", source)
        assert info.id == "max_vias_per_col"


@pytest.fixture
async def client(tmp_path):
    import httpx
    from cellgen_api.backends.local import LocalBackend
    from cellgen_api.main import app
    from cellgen_api.service import SandboxService
    from cellgen_api.store import FileStore

    backend = LocalBackend(
        root=tmp_path / "sandboxes",
        engine_src=REPO_ROOT / "engine",
        opencode_bin=OPENCODE_BIN or "opencode",
    )
    app.state.service = SandboxService(
        backend, FileStore(tmp_path / "store"), OPENCODE_BIN or "opencode")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test", timeout=900) as c:
        yield c

    for record in app.state.service.list_records():
        await app.state.service.destroy(record["_id"], keep_snapshots=False)


def _workdir(client) -> Path:
    from cellgen_api.main import app
    return Path(app.state.service.handle("ws").workdir)


@requires_opencode
async def test_plugins_endpoint_reflects_the_manifest(client):
    """The manifest decides what runs, so the listing must show its values."""
    await client.post("/api/sandboxes", json={"sandbox_id": "ws"})
    workdir = _workdir(client)
    (workdir / "plugins").mkdir(exist_ok=True)
    (workdir / "plugins" / "max_vias_per_col.py").write_text(PLUGIN)
    (workdir / "plugins" / "manifest.json").write_text(
        '{"plugins": [{"id": "max_vias_per_col", "enabled": false, '
        '"params": {"max_vias": 7}}]}')

    body = (await client.get("/api/sandboxes/ws/plugins")).json()
    entry = next(p for p in body["plugins"] if p["id"] == "max_vias_per_col")
    # Not the decorator's default of 2 -- the manifest overrides it.
    assert entry["params"] == {"max_vias": 7}
    assert entry["enabled"] is False


@requires_opencode
async def test_smoke_reports_a_solve(client):
    await client.post("/api/sandboxes", json={"sandbox_id": "ws"})

    result = (await client.post("/api/sandboxes/ws/smoke", json={})).json()
    assert result["ok"] is True
    assert result["status"] == "OPTIMAL"
    assert result["objective"] == pytest.approx(1021.0)
    assert result["elapsed"] is not None

    # Kept on the record so the UI can show it without re-solving.
    record = (await client.get("/api/sandboxes/ws")).json()
    assert record["last_smoke"]["status"] == "OPTIMAL"


@requires_opencode
async def test_smoke_reports_no_objective_for_a_failed_solve(client):
    """An INFEASIBLE run must not inherit the previous run's objective.

    The `.res` file is only written on success, so reading it unconditionally
    reported the last good objective alongside the failure -- which reads as
    'solved at 1021.0' in the UI.
    """
    await client.post("/api/sandboxes", json={"sandbox_id": "ws"})
    workdir = _workdir(client)
    (workdir / "plugins").mkdir(exist_ok=True)

    first = (await client.post("/api/sandboxes/ws/smoke", json={})).json()
    assert first["objective"] == pytest.approx(1021.0)

    (workdir / "plugins" / "impossible.py").write_text(INFEASIBLE)
    failed = (await client.post("/api/sandboxes/ws/smoke",
                                json={"max_time": 30})).json()
    assert failed["ok"] is False
    assert failed["status"] == "INFEASIBLE"
    assert failed["objective"] is None


@requires_opencode
async def test_workspace_endpoints_need_a_running_sandbox(client):
    assert (await client.get("/api/sandboxes/nope/plugins")).status_code == 409
    assert (await client.post("/api/sandboxes/nope/smoke",
                              json={})).status_code == 409
