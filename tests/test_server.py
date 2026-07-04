"""HTTP server tests — M5 FastAPI layer."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentinel.server.app import app
from sentinel.server.runtime import reset_runtime_for_tests


@pytest.fixture()
def client(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    state_path = tmp_path / "state.json"
    reset_runtime_for_tests(log_path=log_path, state_path=state_path)
    with TestClient(app) as c:
        yield c


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_cluster_state_shape(client):
    res = client.get("/api/cluster/state")
    assert res.status_code == 200
    body = res.json()
    assert "racks" in body
    assert len(body["racks"]) > 0
    assert "averageTemperatureC" in body
    assert "timestamp" in body


def test_recommendation_empty_initially(client):
    res = client.get("/api/agent/recommendation")
    assert res.status_code == 204


def test_replay_controls(client):
    assert client.post("/api/replay/start").status_code == 200
    assert client.post("/api/replay/pause").status_code == 200
    assert client.post("/api/replay/resume").status_code == 200


def test_telemetry_and_decision_log(client):
    client.post("/api/replay/start")
    events = client.get("/api/telemetry/events").json()
    assert isinstance(events, list)
    assert any("Replay" in e["message"] for e in events)
    log = client.get("/api/decision-log").json()
    assert isinstance(log, list)


def test_demo_reset(client):
    client.post("/api/replay/start")
    assert client.post("/api/demo/reset").status_code == 200
    state = client.get("/api/cluster/state").json()
    assert state.get("replayStatus") in ("idle", "running", "paused", "stress", "resolved")


def test_stress_endpoint(client):
    assert client.post("/api/replay/stress").status_code == 200
    state = client.get("/api/cluster/state").json()
    assert state.get("replayStatus") == "stress"


def test_step_once_produces_frame(client):
    from sentinel.server.runtime import get_runtime

    frame = get_runtime().step_once()
    assert frame.tick >= 0
    assert len(frame.racks) > 0
