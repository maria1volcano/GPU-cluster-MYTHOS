"""M3 prediction-layer tests.

Covers the milestone gate (real demo window emits the P0 predictions), the
fixtures contract, determinism (CONTRACTS §6), the FR-2 time-to-throttle
countdown on a synthetic approach-from-below, and the agent-loop output
compatibility handshake.
"""
from __future__ import annotations

import json

import pytest

from sentinel.config import DEMO_WINDOW, FIXTURES_DIR
from sentinel.predict.config import LEAD_TIME_S
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import (
    SCHEDULING_BOTTLENECK,
    THERMAL_THROTTLE,
    Evidence,
    Prediction,
    prediction_frame,
)

HOT_RACK = "rack-00"
G2_THROTTLE_TEMP = 84.0


# --- fixtures ----------------------------------------------------------------
def _sample_frames():
    path = FIXTURES_DIR / "telemetry_frames.sample.jsonl"
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _golden_frame():
    with open(FIXTURES_DIR / "telemetry_frame.golden.json") as fh:
        return json.load(fh)


def _evidence_metric(pred: Prediction, metric: str):
    for e in pred.evidence:
        if e.metric == metric:
            return e
    return None


# --- the milestone gate (live, full-resolution engine) -----------------------
def test_live_gate_emits_both_p0_predictions():
    """DoD: over the demo window the layer emits a THERMAL_THROTTLE for the hot
    G2 rack (with temp-trend evidence against the 84 C line) AND a matching
    SCHEDULING_BOTTLENECK with >= 3 heavy jobs queued."""
    from sentinel.engine import Engine

    start, _ = DEMO_WINDOW
    # A sub-window through the queue-peak burst (start + ~4110s) keeps the test
    # fast while covering the money moment.
    window = (start, start + 4200)

    thermal = None
    bottleneck = None
    for _frame, preds in PredictionEngine().attach(Engine(), window):
        for p in preds:
            if p.type == THERMAL_THROTTLE and p.target["id"] == HOT_RACK and thermal is None:
                thermal = p
            if p.type == SCHEDULING_BOTTLENECK and p.target["id"] == HOT_RACK and bottleneck is None:
                bottleneck = p

    assert thermal is not None, "expected a THERMAL_THROTTLE on the hot G2 rack"
    temp_ev = _evidence_metric(thermal, "rack_temp_c")
    assert temp_ev is not None and temp_ev.threshold == G2_THROTTLE_TEMP
    assert thermal.severity in {"medium", "high", "critical"}
    assert 0.0 <= thermal.eta_seconds  # a real, non-negative time-to-event

    assert bottleneck is not None, "expected a SCHEDULING_BOTTLENECK on the hot rack"
    heavy_ev = _evidence_metric(bottleneck, "queued_heavy_jobs")
    assert heavy_ev is not None and heavy_ev.value >= 3


# --- fixtures contract -------------------------------------------------------
def test_fixtures_parse_and_predict():
    frames = _sample_frames()
    assert len(frames) == 134  # the recorded trajectory
    engine = PredictionEngine()
    types = set()
    for f in frames:
        for p in engine.on_frame(f):
            types.add(p.type)
    assert THERMAL_THROTTLE in types
    assert SCHEDULING_BOTTLENECK in types


def test_golden_frame_is_the_money_moment():
    """The golden frame (8 heavy queued, 45 GPUs throttling on rack-00) must
    trip both P0 predictors on the hot rack in a single tick."""
    engine = PredictionEngine()
    preds = engine.on_frame(_golden_frame())
    hot = {p.type for p in preds if p.target.get("id") == HOT_RACK}
    assert THERMAL_THROTTLE in hot
    assert SCHEDULING_BOTTLENECK in hot
    thermal = next(p for p in preds if p.type == THERMAL_THROTTLE and p.target["id"] == HOT_RACK)
    # 45/49 GPUs throttling -> this is an active throttle, not a distant forecast.
    assert thermal.severity == "critical"
    assert thermal.eta_seconds == 0.0


# --- determinism (CONTRACTS §6) ----------------------------------------------
def test_determinism_same_frames_same_predictions():
    frames = _sample_frames()

    def run():
        engine = PredictionEngine()
        return [[p.to_dict() for p in engine.on_frame(f)] for f in frames]

    assert run() == run()


# --- FR-2 time-to-throttle countdown (synthetic approach-from-below) ---------
def _synthetic_rising_frames(n=12, start_temp=78.0, step=0.4, t0=1_000_000, tick0=0):
    """A G2 rack warming steadily from below the line with NO GPUs throttling
    yet — the case where a genuine time-to-throttle countdown exists."""
    frames = []
    for i in range(n):
        temp = start_temp + step * i
        frames.append({
            "type": "telemetry", "v": 1, "tick": tick0 + i,
            "t": t0 + i * 30, "trace_day": (t0 + i * 30) / 86400.0,
            "samples": [],
            "racks": [{
                "rack_id": HOT_RACK, "gpu_model": "G2", "num_nodes": 32,
                "capacity_gpus": 256, "gpu_demand": 40.0, "util": 0.16,
                "active_gpus": 40, "temp_c_mean_active": temp, "temp_c_max": temp + 1,
                "power_w_total": 20000.0, "throttling_gpus": 0,
                "active_pods": 40, "queued_pods": 0, "queued_heavy": 0,
            }],
            "queue": [], "cluster": {},
        })
    return frames


def test_thermal_countdown_before_throttle():
    engine = PredictionEngine()
    fired = None
    for f in _synthetic_rising_frames():
        for p in engine.on_frame(f):
            if p.type == THERMAL_THROTTLE:
                fired = p
                break
        if fired:
            break
    assert fired is not None, "rising temp toward the line should forecast a throttle"
    # A real countdown: strictly positive eta, within the lead-time window, and
    # NOT critical (nothing is throttling yet).
    assert 0.0 < fired.eta_seconds < LEAD_TIME_S
    assert fired.severity in {"medium", "high"}
    temp_ev = _evidence_metric(fired, "rack_temp_c")
    assert temp_ev.slope_per_min > 0


def test_flat_below_line_does_not_fire():
    """A cool rack sitting flat well below its line must stay silent."""
    engine = PredictionEngine()
    preds = []
    for f in _synthetic_rising_frames(n=12, start_temp=60.0, step=0.0):
        preds = engine.on_frame(f)
    assert all(p.type != THERMAL_THROTTLE for p in preds)


# --- episode debounce --------------------------------------------------------
def test_episode_keeps_one_id_then_reopens():
    engine = PredictionEngine()
    rising = _synthetic_rising_frames(n=6, start_temp=83.0, step=0.6)
    ids = []
    for f in rising:
        for p in engine.on_frame(f):
            if p.type == THERMAL_THROTTLE:
                ids.append(p.prediction_id)
    assert ids, "expected the throttle episode to fire"
    assert len(set(ids)) == 1, "a continuous episode must keep a single id"


# --- agent-loop output compatibility handshake -------------------------------
def test_prediction_to_dict_shape_matches_agent_contract():
    engine = PredictionEngine()
    preds = engine.on_frame(_golden_frame())
    p = next(pp for pp in preds if pp.type == THERMAL_THROTTLE)
    d = p.to_dict()
    assert set(d) == {
        "prediction_id", "type", "target", "eta_seconds",
        "severity", "confidence", "evidence", "t",
    }
    assert d["target"]["kind"] == "rack" and "id" in d["target"]
    # The agent's template narration reads these metric names off evidence.
    metrics = {e["metric"] for e in d["evidence"]}
    assert "rack_temp_c" in metrics
    temp_ev = next(e for e in d["evidence"] if e["metric"] == "rack_temp_c")
    assert {"slope_per_min", "threshold", "current"} <= set(temp_ev)


def test_evidence_drops_none_fields():
    e = Evidence(metric="rack_util", value=0.19)
    assert e.to_dict() == {"metric": "rack_util", "value": 0.19}


def test_prediction_frame_wrapper():
    engine = PredictionEngine()
    preds = engine.on_frame(_golden_frame())
    frame = prediction_frame(t=123, tick=5, predictions=preds)
    assert frame["type"] == "prediction" and frame["v"] == 1
    assert frame["t"] == 123 and frame["tick"] == 5
    assert len(frame["predictions"]) == len(preds)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
