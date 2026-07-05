"""HTTP server tests — M5 FastAPI layer."""
from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.server.app import app
from sentinel.server.runtime import get_runtime, reset_runtime_for_tests


class FakeAlertSpeaker:
    """Deterministic TTS stub for server integration tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._done = threading.Event()

    @property
    def is_configured(self) -> bool:
        return True

    def speak_recommendation(self, prediction, recommendation, output_wav=None, *, alert_text=None):
        return self.speak_sync(alert_text or "alert", output_wav=output_wav)

    def speak_sync(self, text, output_wav=None):
        self.calls.append(text)
        self.last_text = text
        out = Path(output_wav) if output_wav else Path("alert.wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 100)
        self._done.set()
        return out

    def wait(self, timeout: float = 5.0) -> None:
        assert self._done.wait(timeout), "TTS did not complete in time"


@pytest.fixture()
def client(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    state_path = tmp_path / "state.json"
    reset_runtime_for_tests(log_path=log_path, state_path=state_path)
    with TestClient(app) as c:
        yield c


def _wait_for_recommendation(timeout: float = 30.0):
    runtime = get_runtime()
    deadline = time.time() + timeout
    while time.time() < deadline:
        runtime.step_once()
        if runtime._pending is not None:
            return runtime._pending
    raise AssertionError("No recommendation fired within replay window")


@pytest.fixture()
def tts_client(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    state_path = tmp_path / "state.json"
    alert_dir = tmp_path / "alerts"
    speaker = FakeAlertSpeaker()
    reset_runtime_for_tests(
        log_path=log_path,
        state_path=state_path,
        tts_enabled=True,
        alert_speaker=speaker,
        alert_dir=alert_dir,
    )
    with TestClient(app) as c:
        yield c, speaker


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_runtime_warm(client):
    from sentinel.server.runtime import get_runtime

    runtime = get_runtime()
    runtime.warm()
    assert runtime.latest_frame is not None
    assert len(runtime.latest_frame.racks) > 0


def test_cluster_state_shape(client):
    res = client.get("/api/cluster/state")
    assert res.status_code == 200
    body = res.json()
    assert "racks" in body
    assert len(body["racks"]) > 0
    rack = body["racks"][0]
    for key in (
        "temperatureC",
        "throttleTempC",
        "powerDrawKw",
        "gpuUtilizationPct",
        "coolingEfficiencyPct",
        "queuePressurePct",
    ):
        assert key in rack
        assert rack[key] is not None
    assert "averageTemperatureC" in body
    assert "timestamp" in body
    assert "mapRacks" in body
    assert len(body["mapRacks"]) <= 8
    for mr in body["mapRacks"]:
        assert mr["temperatureC"] is not None
        assert mr["powerDrawKw"] is not None
        assert mr["gpuUtilizationPct"] is not None


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
    assert all(e.get("operatorAction") for e in log)
    assert not any("Replay" in e.get("message", "") for e in log)


def test_decision_log_excludes_telemetry_events(client):
    client.post("/api/replay/start")
    client.get("/api/telemetry/events").json()
    log = client.get("/api/decision-log").json()
    for entry in log:
        assert entry.get("operatorAction") in ("SURFACED", "APPROVE", "OVERRIDE", "DISMISS")
        assert entry.get("type") not in ("agent_event", "risk_detected")


def test_demo_reset(client):
    client.post("/api/replay/start")
    assert client.post("/api/demo/reset").status_code == 200
    state = client.get("/api/cluster/state").json()
    assert state.get("replayStatus") in ("idle", "running", "paused", "stress", "resolved")


def test_stress_endpoint(client):
    from sentinel.config import DEMO_WINDOW

    client.post("/api/replay/start")
    res = client.post("/api/replay/stress")
    assert res.status_code == 200
    body = res.json()
    assert body.get("success") is True
    assert "state" in body
    state = body["state"]
    assert state.get("replayStatus") == "stress"
    racks = {r["id"]: r for r in state.get("racks", [])}
    assert "rack-00" in racks
    assert racks["rack-00"]["temperatureC"] >= 70.0
    assert racks["rack-00"]["gpuUtilizationPct"] > 0
    # Idle racks keep baseline power/temp — not a full metric wipe.
    assert racks["rack-01"]["powerDrawKw"] > 0
    assert racks["rack-01"]["temperatureC"] > 0
    rec = client.get("/api/agent/recommendation").json()
    assert rec is not None
    assert rec.get("affectedRackId") == "rack-00"
    map_ids = [r["id"] for r in state.get("mapRacks", [])]
    assert map_ids[:3] == ["rack-00", "rack-01", "rack-02"]
    runtime = get_runtime()
    assert runtime.latest_frame is not None
    assert runtime.latest_frame.t >= DEMO_WINDOW[0]


def test_step_once_produces_frame(client):
    from sentinel.server.runtime import get_runtime

    frame = get_runtime().step_once()
    assert frame.tick >= 0
    assert len(frame.racks) > 0


def test_no_duplicate_surfaced_after_approve(client):
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    before = client.get("/api/decision-log").json()
    surfaced_before = [e for e in before if e.get("operatorAction") == "SURFACED"]
    res = client.post(f"/api/agent/recommendation/{rec_id}/approve")
    assert res.status_code == 200
    get_runtime().step_once()
    after = client.get("/api/decision-log").json()
    surfaced_after = [e for e in after if e.get("operatorAction") == "SURFACED"]
    assert len(surfaced_after) == len(surfaced_before)


def test_surfaced_recommendation_writes_decision_log(client):
    _wait_for_recommendation()
    log = client.get("/api/decision-log").json()
    surfaced = [e for e in log if e.get("type") == "recommendation_generated"]
    assert surfaced, "expected SURFACED entry in decision log"
    assert "migrate" in surfaced[-1]["message"].lower()


def test_tts_skipped_without_api_key(client):
    runtime = get_runtime()
    pending = _wait_for_recommendation()
    time.sleep(0.3)
    assert pending.alert_text is not None
    assert pending.alert_status in ("skipped", "generating", "failed")
    assert runtime.alert_audio_path(pending.recommendation.recommendation_id) is None


def test_tts_e2e_generates_alert_audio(tts_client):
    client, speaker = tts_client
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id

    speaker.wait()
    assert len(speaker.calls) >= 1
    assert pending.alert_text
    assert pending.recommendation.job_id in pending.alert_text
    assert pending.recommendation.to_rack in pending.alert_text

    rec = client.get("/api/agent/recommendation").json()
    assert rec["alertStatus"] == "ready"
    assert rec["alertText"] == pending.alert_text
    assert rec["alertAudioUrl"] == f"/api/agent/recommendation/{rec_id}/alert-audio"

    audio = client.get(rec["alertAudioUrl"])
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert len(audio.content) > 44

    events = client.get("/api/telemetry/events").json()
    assert any("Voice alert ready" in e["message"] for e in events)


def test_approve_refreshes_destination_load(client):
    pending = _wait_for_recommendation()
    rec = pending.recommendation
    before = client.get("/api/cluster/state").json()
    to_before = next(r for r in before["racks"] if r["id"] == rec.to_rack)
    res = client.post(f"/api/agent/recommendation/{rec.recommendation_id}/approve")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    after = client.get("/api/cluster/state").json()
    to_after = next(r for r in after["racks"] if r["id"] == rec.to_rack)
    assert to_after["gpuDemandGpus"] >= to_before.get("gpuDemandGpus", 0)
    if body.get("toRackDemandGpus") is not None:
        assert to_after["gpuDemandGpus"] == body["toRackDemandGpus"]


def test_dismiss_prevents_alert_audio(client):
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    res = client.post(
        "/api/agent/recommendation/dismiss-audio",
        json={"recommendationId": rec_id},
    )
    assert res.status_code == 200
    rec = client.get("/api/agent/recommendation").json()
    assert rec["alertStatus"] == "dismissed"
    assert rec.get("alertAudioUrl") is None


def test_approve_with_operator_voice(tts_client):
    client, speaker = tts_client
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    speaker.wait()
    res = client.post(
        f"/api/agent/recommendation/{rec_id}/approve",
        json={"voiceConfirm": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["operatorAlertStatus"] == "ready"
    assert body["operatorAlertAudioUrl"]
    assert body["operatorAlertText"] == f"Approved. Migrating {pending.recommendation.job_id} to {pending.recommendation.to_rack}."
    assert "Operator approved" not in body["operatorAlertText"]
    audio = client.get(body["operatorAlertAudioUrl"])
    assert audio.status_code == 200


def test_alert_audio_404_when_not_ready(client):
    res = client.get("/api/agent/recommendation/rec-missing/alert-audio")
    assert res.status_code == 404


def test_approve_returns_impact_payload(client):
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    res = client.post(f"/api/agent/recommendation/{rec_id}/approve")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["action"] == "approved"
    assert body["outcome"] in ("averted", "unknown")
    assert body["title"]
    assert body["detail"]
    assert body["fromRack"]
    events = client.get("/api/telemetry/events").json()
    assert any("Migrated" in e["message"] or "approved" in e["message"].lower() for e in events)
    assert client.get("/api/agent/recommendation").status_code == 204


def test_override_returns_impact_payload(client):
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    res = client.post(
        f"/api/agent/recommendation/{rec_id}/override",
        json={"reason": "Maintenance scheduled"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "overridden"
    assert body["outcome"] == "overridden"
    assert "Maintenance scheduled" in body["detail"]
    log = client.get("/api/decision-log").json()
    override_entries = [e for e in log if e.get("operatorAction") == "OVERRIDE"]
    assert override_entries
    assert "Maintenance scheduled" in override_entries[-1]["message"]
    assert "UNKNOWN" not in override_entries[-1]["message"]
    events = client.get("/api/telemetry/events").json()
    assert any("No migration applied" in e["message"] for e in events)


def test_frontend_polling_alert_playback_flow(tts_client):
    """Simulates dashboard polling until alertAudioUrl is ready, then fetches WAV."""
    client, speaker = tts_client
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id
    speaker.wait()

    for _ in range(20):
        res = client.get("/api/agent/recommendation")
        if res.status_code == 200:
            body = res.json()
            if body.get("alertStatus") == "ready" and body.get("alertAudioUrl"):
                assert body["alertAudioUrl"] == f"/api/agent/recommendation/{rec_id}/alert-audio"
                audio = client.get(body["alertAudioUrl"])
                assert audio.status_code == 200
                assert audio.headers["content-type"] == "audio/wav"
                assert len(audio.content) > 44
                return
        time.sleep(0.05)

    raise AssertionError("Recommendation alert never reached ready state")
