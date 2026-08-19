"""Switching off one of the engine's own constraints.

This is the point of the whole plugin layer: until now a built-in rule could
only be removed by editing `archit/<TECH>/main.py`, so nobody could measure
what one was buying. The tests that matter are the two ends of that:

* with nothing disabled the model must be *identical* to what the engine
  always produced -- gating 102 call sites is worthless if it perturbs them;
* disabling one must actually remove its constraints from the model, not just
  log that it did.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cellgen import plugins  # noqa: E402
from src.cellgen.plugins import builtins  # noqa: E402
from src.cellgen.run import run  # noqa: E402

TIME_CAP = ["max_time.value=true", "max_time.time=120"]

# Cheap to state, and its absence is unmistakable in the model: it links every
# transistor's placement to its source/drain/gate columns.
LINKING = "placement.link_source_drain_gate_columns_to_transistor_placement"
# Only applies when PMOS and NMOS both break diffusion at a column.
ALIGNMENT = "placement.diffusion_alignment"


@pytest.fixture(autouse=True)
def _reset():
    """The disabled set is process-global; a leak would poison later tests."""
    builtins.set_disabled([])
    yield
    builtins.set_disabled([])


def objective_of(out: Path, cell: str = "INV_X1") -> float:
    first = (out / "result" / f"{cell}.res").read_text().splitlines()[0]
    return float(first.split(":")[1])


def manifest(tmp_path: Path, builtins_entry) -> Path:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"builtins": builtins_entry}))
    return plugin_dir


class TestCatalogue:
    """What the UI lists as switchable."""

    def test_it_finds_the_core_constraints(self):
        ids = {c["id"] for c in plugins.catalogue()}
        assert LINKING in ids
        assert "routing.prohibit_routing_to_left_cell_boundaries" in ids
        # Derived from the modules, so it covers all four of them.
        assert {c["module"] for c in plugins.catalogue()} == {
            "placement", "routing", "rule", "pin"}

    def test_it_reports_what_is_switched_off(self):
        builtins.set_disabled([LINKING])
        entry = next(c for c in plugins.catalogue() if c["id"] == LINKING)
        assert entry["enabled"] is False


class TestManifestParsing:
    """A UI may write either form; both have to mean the same thing."""

    def test_a_plain_list_of_ids(self):
        assert plugins.disabled_builtins({"builtins": [LINKING]}) == [LINKING]

    def test_objects_with_enabled_false(self):
        assert plugins.disabled_builtins(
            {"builtins": [{"id": LINKING, "enabled": False}]}) == [LINKING]

    def test_an_object_left_enabled_is_not_disabled(self):
        """Listing every built-in with its state must not switch them all off."""
        assert plugins.disabled_builtins(
            {"builtins": [{"id": LINKING, "enabled": True}]}) == []

    def test_no_builtins_section_disables_nothing(self):
        assert plugins.disabled_builtins({"plugins": []}) == []
        assert plugins.disabled_builtins(None) == []


@pytest.mark.slow
class TestTransparency:
    """Gating the call sites must not change the model when nothing is off."""

    def test_the_objective_is_unchanged(self, tmp_path):
        results = run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
                      overrides=TIME_CAP)
        assert results["INV_X1"] == "ok"
        # The value this cell has always solved to; see docs/plan.md.
        assert objective_of(tmp_path / "out") == pytest.approx(1021.0)

    def test_every_gated_constraint_reports_its_contribution(self, tmp_path):
        run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out", overrides=TIME_CAP)
        records = builtins.records()

        assert records, "no built-in ran; are the call sites still gated?"
        assert not any(r.skipped for r in records)
        # The linking constraint is substantial; a zero here would mean the
        # measurement is reading the wrong side of the call.
        linking = next(r for r in records if r.id == LINKING)
        assert linking.constraints_added > 0


@pytest.mark.slow
class TestDisabling:
    def test_disabling_one_removes_its_constraints(self, tmp_path):
        """The measurable claim: fewer constraints, and it is marked skipped."""
        plugin_dir = manifest(tmp_path, [LINKING])
        run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
            overrides=TIME_CAP, plugin_dir=plugin_dir)

        skipped = [r for r in builtins.records() if r.skipped]
        assert [r.id for r in skipped] == [LINKING]

    def test_a_disabled_constraint_leaves_the_model_smaller(self, tmp_path):
        """The checkable claim: its constraints are gone from the model.

        Not the objective. Removing a rule only ever *relaxes* the model, so
        the optimum may well be unchanged -- INV_X1 still solves to 1021
        without this one -- and asserting otherwise would be asserting
        something that is not true.
        """
        baseline = tmp_path / "baseline"
        run("FinFET_4T_SH", ["INV_X1"], baseline, overrides=TIME_CAP)
        before = sum(r.constraints_added for r in builtins.records())

        plugin_dir = manifest(tmp_path, [LINKING])
        run("FinFET_4T_SH", ["INV_X1"], tmp_path / "loosened",
            overrides=TIME_CAP, plugin_dir=plugin_dir)
        after = sum(r.constraints_added for r in builtins.records())

        assert after < before, "disabling the rule did not shrink the model"
        # A relaxed model cannot be worse than the constrained one.
        assert objective_of(tmp_path / "loosened") <= objective_of(baseline)

    def test_an_unknown_id_is_ignored_rather_than_fatal(self, tmp_path):
        """A stale manifest must not stop a batch that would otherwise run."""
        plugin_dir = manifest(tmp_path, ["placement.no_such_constraint"])
        results = run("FinFET_4T_SH", ["INV_X1"], tmp_path / "out",
                      overrides=TIME_CAP, plugin_dir=plugin_dir)
        assert results["INV_X1"] == "ok"


@pytest.mark.slow
@pytest.mark.parametrize("preset,tech", [
    ("QFET_4T_SH", "QFET"),
    ("CFET_4T_SH", "CFET"),
])
def test_the_other_technologies_still_build_their_models(preset, tech, tmp_path):
    """FinFET is what the rest of the suite covers; these two are not.

    Their orchestrators had their call sites gated too -- CFET's is a fork with
    its own copies -- so a mistake there would otherwise surface only when
    somebody ran that technology for real.
    """
    results = run(preset, ["INV_X1"], tmp_path / "out",
                  overrides=["max_time.value=true", "max_time.time=60"])
    assert results["INV_X1"] == "ok"

    ran = [r for r in builtins.records() if not r.skipped]
    assert ran, f"{tech} ran no gated built-ins"


@pytest.mark.slow
def test_the_cli_honours_a_disabled_builtin(tmp_path):
    """End to end, the way an experiment runs it."""
    plugin_dir = manifest(tmp_path, [ALIGNMENT])
    proc = subprocess.run(
        [sys.executable, "-m", "src.cellgen.run",
         "--preset", "FinFET_4T_SH", "--cell", "INV_X1",
         "--output-dir", str(tmp_path / "out"),
         "--plugin-dir", str(plugin_dir),
         *sum((["--override", o] for o in TIME_CAP), [])],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert f"[builtin:{ALIGNMENT}] SKIPPED" in proc.stderr
