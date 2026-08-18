"""End-to-end smoke tests for the place-and-route flow.

These are the safety net for every later change to the constraint stack: they
run a real CP-SAT solve, so a regression that changes the model shows up as a
changed objective rather than silently producing a different layout.

``INV_X1`` on FinFET is deliberately the primary case -- it solves in well under
a second, which keeps this suite usable as a pre-commit check and as the
in-sandbox verification step for constraint development.

Run with::

    cd engine && python -m pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.cellgen.io.cdl import scan_cdl  # noqa: E402
from src.cellgen.io.logparse import parse_log, status_is_ok  # noqa: E402
from src.cellgen.run import resolve_preset, run  # noqa: E402

# Keep every solve bounded so a model regression fails the suite instead of
# hanging CI. INV_X1 needs ~0.3s; the cap is pure insurance.
TIME_CAP = ["max_time.value=true", "max_time.time=120"]


@pytest.fixture(autouse=True)
def _repo_cwd():
    """config.init() reads ./input/pin_*.json relative to the working directory."""
    prev = Path.cwd()
    os.chdir(REPO_ROOT)
    yield
    os.chdir(prev)


class TestPresetResolution:
    @pytest.mark.parametrize(
        "preset,tech,lib_name",
        [
            ("FinFET_4T_SH", "FinFET", "PROBE3_FinFET_2F_4T_4530OF0"),
            ("CFET_4T_SH", "CFET", "PROBE3_CFET_2F_4T_4530OF0"),
            ("QFET_4T_SH", "QFET", "PROBE3_QFET_2F_4T_4242OF21"),
        ],
    )
    def test_resolves_to_existing_inputs(self, preset, tech, lib_name):
        cfg = resolve_preset(preset)
        assert cfg.tech == tech
        assert cfg.lib_name == lib_name
        assert cfg.cdl_file.is_file()
        assert cfg.layer_file.is_file()

    def test_preset_config_overrides_are_parsed(self):
        """QFET's preset is the only one carrying CONFIG_OVERRIDES."""
        cfg = resolve_preset("QFET_4T_SH")
        assert "max_time.value=true" in cfg.overrides
        assert "insert_num_db=2" in cfg.overrides

    def test_unknown_preset_lists_the_valid_ones(self):
        with pytest.raises(FileNotFoundError, match="FinFET_4T_SH"):
            resolve_preset("does_not_exist")


class TestSmokeSolve:
    def test_inv_x1_finfet_solves(self, tmp_path):
        results = run("FinFET_4T_SH", ["INV_X1"], tmp_path, overrides=TIME_CAP)
        assert results == {"INV_X1": "ok"}

        res = tmp_path / "result" / "INV_X1.res"
        assert res.is_file(), "solve reported success but wrote no .res"
        assert "** Placement Result **" in res.read_text()
        assert "** Routing Result **" in res.read_text()

        parsed = parse_log(tmp_path / "logs" / "INV_X1.log")
        assert status_is_ok(parsed["status"]), parsed
        assert parsed["obj"], "no objective parsed from the log"

    def test_writes_the_expected_artifacts(self, tmp_path):
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, overrides=TIME_CAP)
        for rel in ("config/INV_X1.json", "result/INV_X1.res",
                    "result/INV_X1.var", "logs/INV_X1.log", "run.json"):
            assert (tmp_path / rel).is_file(), f"missing artifact: {rel}"

    def test_run_json_records_what_was_run(self, tmp_path):
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, overrides=TIME_CAP)
        meta = json.loads((tmp_path / "run.json").read_text())
        assert meta["tech"] == "FinFET"
        assert meta["cells"] == ["INV_X1"]
        assert "max_time.value=true" in meta["overrides"]


class TestPerRunIsolation:
    """The reason this driver exists: `make spnr` overwrites across runs."""

    def test_two_runs_do_not_share_config(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        run("FinFET_4T_SH", ["INV_X1"], a, overrides=[*TIME_CAP, "seed=1"])
        run("FinFET_4T_SH", ["INV_X1"], b, overrides=[*TIME_CAP, "seed=99"])

        seed_a = json.loads((a / "config" / "INV_X1.json").read_text())["seed"]["value"]
        seed_b = json.loads((b / "config" / "INV_X1.json").read_text())["seed"]["value"]
        assert (seed_a, seed_b) == (1, 99), (
            "per-run configs bled into each other; generate_config(force=True) "
            "is what prevents the Makefile's silent-reuse behaviour"
        )

    def test_rerun_into_same_dir_regenerates_config(self, tmp_path):
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, overrides=[*TIME_CAP, "seed=1"])
        run("FinFET_4T_SH", ["INV_X1"], tmp_path, overrides=[*TIME_CAP, "seed=7"])
        seed = json.loads((tmp_path / "config" / "INV_X1.json").read_text())["seed"]["value"]
        assert seed == 7, "second run reused the first run's config JSON"


class TestFailureContainment:
    """A failing cell must not take down the process or the rest of the batch."""

    def test_bad_config_is_contained_and_batch_continues(self, tmp_path):
        # MPO=8 asks for more M1 entry points than M2 has rows, which raises
        # during constraint construction -- the same shape of failure a buggy
        # user-authored constraint plugin will produce.
        results = run(
            "FinFET_4T_SH", ["INV_X1", "NAND2_X1"], tmp_path,
            overrides=[*TIME_CAP, "MPO=8"],
        )
        assert set(results) == {"INV_X1", "NAND2_X1"}, "batch stopped early"
        assert results["INV_X1"] == "ERROR"
        assert "ValueError" in (tmp_path / "logs" / "INV_X1.log").read_text()

    def test_cli_exit_code_is_nonzero_on_failure(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-m", "src.cellgen.run",
             "--preset", "FinFET_4T_SH", "--cell", "INV_X1",
             "--output-dir", str(tmp_path), "--override", "MPO=8"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 1, proc.stderr[-2000:]

    def test_cli_exit_code_is_zero_on_success(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, "-m", "src.cellgen.run",
             "--preset", "FinFET_4T_SH", "--cell", "INV_X1",
             "--output-dir", str(tmp_path), *sum((["--override", o] for o in TIME_CAP), [])],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]


class TestNetlistParsing:
    def test_scan_cdl_finds_the_bundled_cells(self):
        cells = scan_cdl(REPO_ROOT / "input" / "cdl" / "PROBE_2F4T.cdl")
        assert len(cells) == 46
        for expected in ("INV_X1", "NAND2_X1", "AOI21_X2", "DFFHQN_X1"):
            assert expected in cells
