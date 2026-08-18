"""Capturing work out of a sandbox and running it as an experiment.

A sandbox cannot push and must not touch the upstream engine, so this path --
files out, into storage, materialised again at experiment time -- is the only
way work travels. The decisive test runs a real solve against a materialised
artifact.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
ENGINE = REPO_ROOT / "engine"
sys.path.insert(0, str(API_ROOT))

from cellgen_api.artifacts import Artifact, PluginFile, materialize  # noqa: E402

OPENCODE_BIN = os.environ.get("CELLGEN_OPENCODE_BIN") or shutil.which("opencode")
requires_opencode = pytest.mark.skipif(
    not OPENCODE_BIN, reason="no opencode binary available")


CAP_PLUGIN = '''
from src.cellgen.plugins import constraint

@constraint(id="art_cap", stage="post_routing", tech=["FinFET"],
            params={"max_edges": 10000})
def art_cap(inst, params):
    """Cap total routing edges."""
    inst.opt.Add(sum(inst.edge_vars.values()) <= params["max_edges"])
'''


@pytest.fixture
async def sandbox(tmp_path):
    from cellgen_api.backends.local import LocalBackend

    backend = LocalBackend(root=tmp_path / "sbx", engine_src=ENGINE,
                           opencode_bin=OPENCODE_BIN or "opencode")
    handle = await backend.create("ws-artifact")
    yield backend, handle
    await backend.destroy(handle)


@requires_opencode
async def test_captures_plugins_and_manifest(sandbox, tmp_path):
    from cellgen_api.artifacts import capture

    backend, handle = sandbox
    await backend.write_file(handle, "plugins/cap.py", CAP_PLUGIN)
    await backend.write_file(handle, "plugins/manifest.json", json.dumps(
        {"plugins": [{"id": "art_cap", "params": {"max_edges": 42}}]}))

    art = await capture(backend, handle, name="my work")
    assert [p.path for p in art.plugins] == ["plugins/cap.py"]
    assert art.manifest == {"plugins": [{"id": "art_cap", "params": {"max_edges": 42}}]}
    # Nothing outside plugins/ changed, so there is no engine patch.
    assert art.engine_patch.strip() == ""


@requires_opencode
async def test_captures_edits_to_the_engine_itself(sandbox):
    """A change a plugin cannot express must still travel."""
    from cellgen_api.artifacts import capture

    backend, handle = sandbox
    target = "src/cellgen/core/errors.py"
    original = await backend.read_file(handle, target)
    await backend.write_file(handle, target, original + "\n# tweaked in sandbox\n")

    art = await capture(backend, handle, name="core tweak")
    assert target in art.changed_files
    assert "tweaked in sandbox" in art.engine_patch
    assert art.plugins == []


@requires_opencode
async def test_export_endpoint_stores_a_listable_artifact(tmp_path):
    import httpx
    from cellgen_api.backends.local import LocalBackend
    from cellgen_api.main import app
    from cellgen_api.service import SandboxService
    from cellgen_api.store import FileStore

    backend = LocalBackend(root=tmp_path / "sbx", engine_src=ENGINE,
                           opencode_bin=OPENCODE_BIN or "opencode")
    app.state.service = SandboxService(
        backend, FileStore(tmp_path / "store"), OPENCODE_BIN or "opencode")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test", timeout=120) as c:
        await c.post("/api/sandboxes", json={"sandbox_id": "ws-export"})
        handle = app.state.service.handle("ws-export")
        await backend.write_file(handle, "plugins/cap.py", CAP_PLUGIN)

        created = await c.post("/api/sandboxes/ws-export/export",
                               json={"name": "via api", "description": "d"})
        assert created.status_code == 201, created.text
        doc = created.json()
        assert doc["plugin_count"] == 1

        listing = (await c.get("/api/artifacts")).json()
        assert [a["name"] for a in listing] == ["via api"]
        # The listing is a summary; the payload is not inlined.
        assert "artifact" not in listing[0]

        full = (await c.get(f"/api/artifacts/{doc['_id']}")).json()
        assert full["plugins"][0]["path"] == "plugins/cap.py"

        await app.state.service.destroy("ws-export", keep_snapshots=False)


def test_materialize_writes_plugins_and_applies_patch(tmp_path):
    engine = tmp_path / "engine"
    engine.mkdir()

    art = Artifact(
        sandbox_id="s", name="n",
        plugins=[PluginFile(path="plugins/x.py", source="# hi\n")],
        manifest={"plugins": [{"id": "x"}]},
    )
    plugin_dir = materialize(art, engine)
    assert (engine / "plugins" / "x.py").read_text() == "# hi\n"
    assert json.loads((plugin_dir / "manifest.json").read_text()) == {"plugins": [{"id": "x"}]}


def test_materialized_artifact_actually_solves(tmp_path):
    """The end of the road: an artifact becomes a real experiment run.

    Uses a binding cap so the plugin's effect is unmistakable -- if the plugin
    were dropped somewhere along the way the solve would succeed instead of
    coming back INFEASIBLE.
    """
    engine = tmp_path / "engine"
    shutil.copytree(ENGINE, engine,
                    ignore=shutil.ignore_patterns("__pycache__", "output", ".git"))

    art = Artifact(
        sandbox_id="s", name="binding cap",
        plugins=[PluginFile(path="plugins/cap.py", source=CAP_PLUGIN)],
        manifest={"plugins": [{"id": "art_cap", "params": {"max_edges": 3}}]},
    )
    plugin_dir = materialize(art, engine)

    out = tmp_path / "run"
    proc = subprocess.run(
        [sys.executable, "-m", "src.cellgen.run",
         "--preset", "FinFET_4T_SH", "--cell", "INV_X1",
         "--output-dir", str(out), "--plugin-dir", str(plugin_dir),
         "--override", "max_time.value=true", "--override", "max_time.time=120"],
        cwd=engine, capture_output=True, text=True, timeout=600,
    )
    assert "[plugin:art_cap]" in proc.stdout + proc.stderr, "plugin never ran"
    assert proc.returncode == 1, "a cap of 3 edges should be infeasible"
    assert "INFEASIBLE" in proc.stdout + proc.stderr


def test_materialized_artifact_solves_when_cap_is_slack(tmp_path):
    """The same artifact with a workable parameter must solve."""
    engine = tmp_path / "engine"
    shutil.copytree(ENGINE, engine,
                    ignore=shutil.ignore_patterns("__pycache__", "output", ".git"))

    art = Artifact(
        sandbox_id="s", name="slack cap",
        plugins=[PluginFile(path="plugins/cap.py", source=CAP_PLUGIN)],
        manifest={"plugins": [{"id": "art_cap", "params": {"max_edges": 10000}}]},
    )
    plugin_dir = materialize(art, engine)

    out = tmp_path / "run"
    proc = subprocess.run(
        [sys.executable, "-m", "src.cellgen.run",
         "--preset", "FinFET_4T_SH", "--cell", "INV_X1",
         "--output-dir", str(out), "--plugin-dir", str(plugin_dir),
         "--override", "max_time.value=true", "--override", "max_time.time=120"],
        cwd=engine, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert (out / "result" / "INV_X1.res").is_file()

    meta = json.loads((out / "run.json").read_text())
    assert meta["plugins"] == [
        {"id": "art_cap", "stage": "post_routing", "params": {"max_edges": 10000}}
    ]
