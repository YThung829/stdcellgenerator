"""Tests for the constraint plugin layer.

The load-bearing property is that the plugin layer is *transparent*: with no
plugin loaded, or with one that adds nothing, the solved result must be exactly
what the engine produced before the layer existed. Everything else builds on
that.

Note on determinism -- measured, not assumed: repeating the *same* solve four
times in one process yields four layouts, two of which happened to match. The
objective was 1021.0 every time. So the engine reaches equal-cost optima
nondeterministically, and no available knob fixes it:

* ``num_search_workers=1`` is silently ignored -- ``model_preset`` 2 (the
  default) raises the count back to ``max(configured, 8)``.
* ``deterministic_solve=true`` does not make repeated solves layout-identical
  either.

Therefore **transparency is asserted on the objective value, never on the
layout**. The same limitation applies to comparing experiment results in the
web UI: compare metrics, not geometry.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cellgen import plugins  # noqa: E402
from src.cellgen.plugins import registry  # noqa: E402
from src.cellgen.plugins.loader import PluginLoadError  # noqa: E402
from src.cellgen.run import run  # noqa: E402

TIME_CAP = ["max_time.value=true", "max_time.time=120"]
STABLE = TIME_CAP


def objective_of(res_path: Path) -> float:
    """Read '** Objective value: N' from a .res file.

    This is the invariant worth asserting on: it is reproducible across runs,
    whereas the placement/routing geometry is not (see the module docstring).
    """
    first = res_path.read_text().splitlines()[0]
    return float(first.split(":")[1])


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is process-global; a leaked registration breaks later tests."""
    prev = Path.cwd()
    os.chdir(REPO_ROOT)
    registry.clear()
    plugins.set_active([])
    yield
    registry.clear()
    plugins.set_active([])
    os.chdir(prev)


def write_plugin(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(body)
    return path


NOOP_SRC = '''
from src.cellgen.plugins import constraint

@constraint(id="t_noop", stage="post_routing", tech=["FinFET", "CFET", "QFET"])
def t_noop(inst, params):
    """Adds nothing."""
    inst.opt.log_comment("test noop")
'''

CAP_SRC = '''
from src.cellgen.plugins import constraint

@constraint(id="t_cap", stage="post_routing", tech=["FinFET"], params={"max_edges": 10000})
def t_cap(inst, params):
    """Cap total routing edges."""
    inst.opt.Add(sum(inst.edge_vars.values()) <= params["max_edges"])
'''

CFET_ONLY_SRC = '''
from src.cellgen.plugins import constraint

@constraint(id="t_cfet_only", stage="post_routing", tech=["CFET"])
def t_cfet_only(inst, params):
    raise AssertionError("must not run on a FinFET solve")
'''


class TestRegistry:
    def test_registers_metadata(self):
        @registry.constraint(id="r1", stage="post_routing", tech=["FinFET"],
                             params={"n": 2}, description="desc")
        def _fn(inst, params):
            pass

        p = registry.get("r1")
        assert (p.stage, p.tech, p.params, p.description) == (
            "post_routing", ("FinFET",), {"n": 2}, "desc")

    def test_description_falls_back_to_docstring(self):
        @registry.constraint(id="r2", stage="post_routing", tech=["FinFET"])
        def _fn(inst, params):
            """First line wins.

            Second line ignored.
            """

        assert registry.get("r2").description == "First line wins."

    def test_rejects_duplicate_id(self):
        @registry.constraint(id="dup", stage="post_routing", tech=["FinFET"])
        def _a(inst, params):
            pass

        with pytest.raises(ValueError, match="Duplicate constraint id"):
            @registry.constraint(id="dup", stage="post_routing", tech=["FinFET"])
            def _b(inst, params):
                pass

    def test_rejects_unknown_stage(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            @registry.constraint(id="bad_stage", stage="nope", tech=["FinFET"])
            def _fn(inst, params):
                pass

    def test_rejects_unknown_technology(self):
        with pytest.raises(ValueError, match="unknown technology"):
            @registry.constraint(id="bad_tech", stage="post_routing", tech=["GAAFET"])
            def _fn(inst, params):
                pass

    def test_iter_filters_by_stage_and_tech(self):
        @registry.constraint(id="a", stage="post_routing", tech=["FinFET"])
        def _a(inst, params):
            pass

        @registry.constraint(id="b", stage="pre_solve", tech=["FinFET"])
        def _b(inst, params):
            pass

        @registry.constraint(id="c", stage="post_routing", tech=["CFET"])
        def _c(inst, params):
            pass

        assert [p.id for p in registry.iter_constraints("post_routing", "FinFET")] == ["a"]
        assert [p.id for p in registry.iter_constraints("pre_solve", "FinFET")] == ["b"]
        assert [p.id for p in registry.iter_constraints("post_routing", "CFET")] == ["c"]


class TestLoader:
    def test_loads_a_directory(self, tmp_path):
        write_plugin(tmp_path, "noop", NOOP_SRC)
        loaded = plugins.load_plugin_dir(tmp_path)
        assert [p.id for p in loaded] == ["t_noop"]
        assert loaded[0].source_file.endswith("noop.py")

    def test_missing_directory_is_an_error(self, tmp_path):
        with pytest.raises(PluginLoadError, match="does not exist"):
            plugins.load_plugin_dir(tmp_path / "nope")

    def test_broken_plugin_fails_loudly(self, tmp_path):
        write_plugin(tmp_path, "broken", "this is not python(")
        with pytest.raises(PluginLoadError, match="Failed to import"):
            plugins.load_plugin_dir(tmp_path)

    def test_underscore_files_are_skipped(self, tmp_path):
        write_plugin(tmp_path, "_helper", NOOP_SRC)
        assert plugins.load_plugin_dir(tmp_path) == []

    def test_no_manifest_runs_everything(self, tmp_path):
        write_plugin(tmp_path, "noop", NOOP_SRC)
        selections = plugins.load(tmp_path)
        assert [s.plugin.id for s in selections] == ["t_noop"]

    def test_manifest_overrides_params(self, tmp_path):
        write_plugin(tmp_path, "cap", CAP_SRC)
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"plugins": [{"id": "t_cap", "params": {"max_edges": 7}}]}))
        selections = plugins.load(tmp_path)
        assert selections[0].params == {"max_edges": 7}

    def test_manifest_can_disable(self, tmp_path):
        write_plugin(tmp_path, "cap", CAP_SRC)
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"plugins": [{"id": "t_cap", "enabled": False}]}))
        assert plugins.load(tmp_path) == []

    def test_manifest_orders_plugins(self, tmp_path):
        write_plugin(tmp_path, "noop", NOOP_SRC)
        write_plugin(tmp_path, "cap", CAP_SRC)
        (tmp_path / "manifest.json").write_text(json.dumps({"plugins": [
            {"id": "t_cap", "order": 2},
            {"id": "t_noop", "order": 1},
        ]}))
        assert [s.plugin.id for s in plugins.load(tmp_path)] == ["t_noop", "t_cap"]

    def test_manifest_referencing_unknown_plugin_is_an_error(self, tmp_path):
        write_plugin(tmp_path, "noop", NOOP_SRC)
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"plugins": [{"id": "does_not_exist"}]}))
        with pytest.raises(PluginLoadError, match="unknown constraint"):
            plugins.load(tmp_path)


class TestTransparency:
    """The property everything else depends on."""

    def test_inert_plugin_does_not_change_the_result(self, tmp_path):
        plug = tmp_path / "plug"
        write_plugin(plug, "noop", NOOP_SRC)

        base, withp = tmp_path / "base", tmp_path / "withp"
        run("FinFET_4T_SH", ["INV_X1"], base, overrides=STABLE)
        run("FinFET_4T_SH", ["INV_X1"], withp, overrides=STABLE, plugin_dir=plug)

        assert objective_of(base / "result" / "INV_X1.res") == \
               objective_of(withp / "result" / "INV_X1.res"), \
            "an inert plugin changed the objective value"

    def test_inert_plugin_reports_a_zero_delta(self, tmp_path):
        plug = tmp_path / "plug"
        write_plugin(plug, "noop", NOOP_SRC)
        run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
            overrides=STABLE, plugin_dir=plug)

        rec = [r for r in plugins.records() if r.id == "t_noop"]
        assert len(rec) == 1
        assert (rec[0].constraints_added, rec[0].variables_added) == (0, 0)

    def test_plugin_for_another_tech_does_not_run(self, tmp_path):
        # The plugin body raises if executed, so a FinFET solve completing
        # proves the tech filter held.
        plug = tmp_path / "plug"
        write_plugin(plug, "cfet_only", CFET_ONLY_SRC)
        results = run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
                      overrides=STABLE, plugin_dir=plug)
        assert results == {"INV_X1": "ok"}


class TestPluginEffect:
    def test_binding_plugin_adds_constraints_and_is_recorded(self, tmp_path):
        plug = tmp_path / "plug"
        write_plugin(plug, "cap", CAP_SRC)
        run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
            overrides=STABLE, plugin_dir=plug)

        rec = [r for r in plugins.records() if r.id == "t_cap"][0]
        assert rec.constraints_added == 1
        assert rec.stage == "post_routing"

    def test_unsatisfiable_plugin_is_contained(self, tmp_path):
        """An over-constraining plugin must fail the cell, not the process."""
        plug = tmp_path / "plug"
        write_plugin(plug, "cap", CAP_SRC)
        (plug / "manifest.json").write_text(json.dumps(
            {"plugins": [{"id": "t_cap", "params": {"max_edges": 3}}]}))

        out = tmp_path / "out"
        results = run("FinFET_4T_SH", ["INV_X1"], out, overrides=STABLE, plugin_dir=plug)

        assert results == {"INV_X1": "INFEASIBLE"}
        assert not (out / "result" / "INV_X1.res").exists(), \
            "an infeasible solve must not write a result file"

    def test_run_json_records_active_plugins(self, tmp_path):
        plug = tmp_path / "plug"
        write_plugin(plug, "cap", CAP_SRC)
        (plug / "manifest.json").write_text(json.dumps(
            {"plugins": [{"id": "t_cap", "params": {"max_edges": 500}}]}))

        out = tmp_path / "out"
        run("FinFET_4T_SH", ["INV_X1"], out, overrides=STABLE, plugin_dir=plug)

        meta = json.loads((out / "run.json").read_text())
        assert meta["plugins"] == [
            {"id": "t_cap", "stage": "post_routing", "params": {"max_edges": 500}}
        ]


class TestBundledExamples:
    """The examples in engine/plugins/examples/ are documentation; keep them working."""

    def test_examples_load(self):
        loaded = plugins.load_plugin_dir(REPO_ROOT / "plugins" / "examples")
        assert {p.id for p in loaded} == {"noop", "limit_total_metal"}

    def test_example_noop_is_inert(self, tmp_path):
        base, withp = tmp_path / "base", tmp_path / "withp"
        run("FinFET_4T_SH", ["INV_X1"], base, overrides=STABLE)

        plug = tmp_path / "plug"
        plug.mkdir()
        (plug / "noop.py").write_text(
            (REPO_ROOT / "plugins" / "examples" / "noop.py").read_text())
        run("FinFET_4T_SH", ["INV_X1"], withp, overrides=STABLE, plugin_dir=plug)

        assert objective_of(base / "result" / "INV_X1.res") == \
               objective_of(withp / "result" / "INV_X1.res")
