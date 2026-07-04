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

    def speak_recommendation(self, prediction, recommendation, output_wav=None):
        self.calls.append(recommendation.recommendation_id)
        out = Path(output_wav)
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


def test_tts_skipped_without_api_key(client):
    runtime = get_runtime()
    pending = _wait_for_recommendation()
    time.sleep(0.2)
    assert pending.alert_text is not None
    assert pending.alert_status == "skipped"
    assert runtime.alert_audio_path(pending.recommendation.recommendation_id) is None


def test_tts_e2e_generates_alert_audio(tts_client):
    client, speaker = tts_client
    pending = _wait_for_recommendation()
    rec_id = pending.recommendation.recommendation_id

    speaker.wait()
    assert speaker.calls == [rec_id]

    rec = client.get("/api/agent/recommendation").json()
    assert rec["alertStatus"] == "ready"
    assert rec["alertText"]
    assert rec["alertAudioUrl"] == f"/api/agent/recommendation/{rec_id}/alert-audio"

    audio = client.get(rec["alertAudioUrl"])
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert len(audio.content) > 44

    events = client.get("/api/telemetry/events").json()
    assert any("Voice alert ready" in e["message"] for e in events)


def test_alert_audio_404_when_not_ready(client):
    res = client.get("/api/agent/recommendation/rec-missing/alert-audio")
    assert res.status_code == 404


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
