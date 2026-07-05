"""DCGM-style dashboard event generation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sentinel.server.app import app
from sentinel.server.dcgm_events import events_from_frame
from sentinel.server.runtime import reset_runtime_for_tests


@pytest.fixture()
def client(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    state_path = tmp_path / "state.json"
    reset_runtime_for_tests(log_path=log_path, state_path=state_path)
    with TestClient(app) as c:
        yield c


def test_events_from_frame_includes_cluster_and_rack_metrics():
    runtime = reset_runtime_for_tests()
    runtime.warm()
    frame = runtime.step_once()
    events = events_from_frame(frame)
    assert len(events) >= 2
    assert any("DCGM cluster tick" in e["message"] for e in events)
    assert any(e["type"] == "metric_update" for e in events)


def test_cluster_state_includes_map_racks(client):
    res = client.get("/api/cluster/state")
    body = res.json()
    assert "mapRacks" in body
    assert len(body["mapRacks"]) <= 8
    assert body.get("dcgmSampleCount", 0) > 0


def test_dcgm_samples_endpoint(client):
    client.get("/api/cluster/state")
    res = client.get("/api/telemetry/dcgm/samples?limit=10")
    assert res.status_code == 200
    samples = res.json()
    assert isinstance(samples, list)
    if samples:
        assert "gpu_id" in samples[0]
        assert "temp_c" in samples[0]
