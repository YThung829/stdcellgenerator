"""The experiment and run HTTP surface.

No broker: runs stay pending, which is exactly the state the routes have to
present honestly. What is checked here is the shape of the API -- status
codes, the derived experiment status, artifact downloads, and that the log
endpoint streams server-sent events rather than a plain body.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from cellgen_api.runlog import sse  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    import httpx

    from cellgen_api.experiments import ExperimentService
    from cellgen_api.main import app
    from cellgen_api.store import FileStore

    # Somewhere closed, so dispatch and cancellation take their no-broker paths.
    monkeypatch.setenv("CELLGEN_REDIS_URL", "redis://127.0.0.1:1/0")
    store = FileStore(tmp_path / "store")
    app.state.experiments = ExperimentService(store)
    app.state.store = store

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60)


async def make(client, **kw):
    body = {"name": "exp", "preset": "FinFET_4T_SH", "cells": ["INV_X1"], **kw}
    resp = await client.post("/api/experiments", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCreating:
    async def test_an_experiment_returns_its_runs(self, client):
        experiment = await make(client, cells=["INV_X1", "NAND2_X1"])
        assert experiment["status"] == "pending"
        assert sorted(r["cell"] for r in experiment["runs"]) == ["INV_X1", "NAND2_X1"]

    async def test_no_cells_is_a_bad_request(self, client):
        resp = await client.post("/api/experiments", json={
            "name": "e", "preset": "FinFET_4T_SH", "cells": []})
        assert resp.status_code == 400

    async def test_an_unknown_artifact_is_a_404(self, client):
        resp = await client.post("/api/experiments", json={
            "name": "e", "preset": "FinFET_4T_SH", "cells": ["INV_X1"],
            "artifact_id": "art_nope"})
        assert resp.status_code == 404

    async def test_a_dead_broker_still_records_the_experiment(self, client):
        """Losing the experiment would be worse than delaying its runs."""
        experiment = await make(client)
        listed = (await client.get("/api/experiments")).json()
        assert [e["_id"] for e in listed] == [experiment["_id"]]
        assert experiment["runs"][0]["status"] == "pending"


class TestReading:
    async def test_an_unknown_experiment_is_a_404(self, client):
        assert (await client.get("/api/experiments/exp_nope")).status_code == 404

    async def test_an_unknown_run_is_a_404(self, client):
        assert (await client.get("/api/runs/run_nope")).status_code == 404

    async def test_the_list_reports_progress(self, client):
        experiment = await make(client, cells=["A", "B"])
        runs = experiment["runs"]
        client._transport.app.state.experiments.store.put(
            "runs", runs[0]["_id"], {**runs[0], "status": "done"})

        listed = (await client.get("/api/experiments")).json()[0]
        assert (listed["run_count"], listed["done_count"]) == (2, 1)


class TestCancelling:
    async def test_cancelling_an_experiment_marks_its_runs(self, client):
        experiment = await make(client, cells=["A", "B"])
        resp = await client.post(f"/api/experiments/{experiment['_id']}/cancel")
        assert resp.status_code == 200

        after = (await client.get(f"/api/experiments/{experiment['_id']}")).json()
        assert after["status"] == "cancelled"
        assert all(r["status"] == "cancelled" for r in after["runs"])

    async def test_cancelling_an_unknown_experiment_is_a_404(self, client):
        assert (await client.post(
            "/api/experiments/exp_nope/cancel")).status_code == 404

    async def test_cancelling_an_unknown_run_is_a_404(self, client):
        assert (await client.post("/api/runs/run_nope/cancel")).status_code == 404


class TestArtifacts:
    async def test_a_run_without_the_file_is_a_404(self, client):
        experiment = await make(client)
        run_id = experiment["runs"][0]["_id"]
        assert (await client.get(
            f"/api/runs/{run_id}/artifacts/png")).status_code == 404

    async def test_a_recorded_file_is_served(self, client, tmp_path):
        experiment = await make(client)
        run = experiment["runs"][0]
        res = tmp_path / "INV_X1.res"
        res.write_text("** Objective value: 1021.0\n")
        client._transport.app.state.experiments.store.put(
            "runs", run["_id"], {**run, "artifacts": {"res": str(res)}})

        resp = await client.get(f"/api/runs/{run['_id']}/artifacts/res")
        assert resp.status_code == 200
        assert "1021.0" in resp.text


class TestLogStream:
    async def test_the_log_endpoint_speaks_server_sent_events(self, client, tmp_path):
        experiment = await make(client)
        run = experiment["runs"][0]
        log = tmp_path / "run.log"
        log.write_text("solving INV_X1\nstatus: OPTIMAL\n")
        client._transport.app.state.experiments.store.put(
            "runs", run["_id"], {**run, "artifacts": {"log": str(log)}})

        async with client.stream(
                "GET", f"/api/runs/{run['_id']}/log") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
                if "event: end" in body:
                    break

        # Everything written before the follower arrived is replayed first.
        assert "event: history" in body
        assert "status: OPTIMAL" in body
        # With no Redis there is no live tail, and the stream says so.
        assert "event: warning" in body

    async def test_the_log_of_an_unknown_run_is_a_404(self, client):
        assert (await client.get("/api/runs/run_nope/log")).status_code == 404


class TestSseFraming:
    """A log line is arbitrary text; the framing has to survive it."""

    def test_each_line_of_a_payload_is_prefixed(self):
        """A bare newline would otherwise end the event and truncate the rest."""
        framed = sse("first\nsecond")
        assert framed == "data: first\ndata: second\n\n"

    def test_an_event_name_is_emitted_before_the_data(self):
        assert sse("x", event="history").startswith("event: history\ndata: x")

    def test_every_event_ends_with_a_blank_line(self):
        assert sse("x").endswith("\n\n")
