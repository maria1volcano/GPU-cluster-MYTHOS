"""Tests for the M3 upgrades: engine fork, thermal physics (ODE inversion),
digital-twin lookahead, and counterfactual scoring.

The live-engine tests share one module-scoped engine seeked to the demo burst
(seek + replay is the slow part), then fork cheaply per assertion.
"""
from __future__ import annotations

import math

import pytest

from sentinel.config import DEMO_WINDOW, TEMP_TAU_S, TICK_TRACE_S
from sentinel.predict import physics
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.twin import DigitalTwin, _summarize


# --- thermal physics (pure, fast) --------------------------------------------
def test_ode_crossing_basic():
    # Already over the line -> 0.
    assert physics.time_to_threshold(85.0, 90.0, 84.0) == 0.0
    # Asymptote below the line -> never crosses.
    assert physics.time_to_threshold(80.0, 83.0, 84.0) is None
    # Analytic crossing matches the closed form.
    tau = TEMP_TAU_S
    eta = physics.time_to_threshold(80.0, 88.0, 84.0, tau)
    expected = tau * math.log((88.0 - 80.0) / (88.0 - 84.0))
    assert abs(eta - expected) < 1e-6


def test_infer_target_from_slope():
    # target = T + tau * dT/dt
    assert abs(physics.infer_target_temp(80.0, 0.02, 300.0) - 86.0) < 1e-9


def test_eta_from_trend_never_when_flat_below_line():
    eta, _ = physics.eta_from_trend(80.0, 0.0, 84.0)
    assert eta is None


def test_ode_beats_linear_against_rc_ground_truth():
    """Generate the true trajectory from the RC recurrence the telemetry model
    uses; the ODE-inverted ETA must be closer to the true crossing than the
    naive linear one, measured from an early vantage point."""
    tau, line, t0, target = TEMP_TAU_S, 84.0, 78.0, 86.0
    alpha = 1.0 - math.exp(-TICK_TRACE_S / tau)
    temps = [t0]
    for _ in range(400):
        temps.append(temps[-1] + (target - temps[-1]) * alpha)
    true_cross = next(k for k, T in enumerate(temps) if T >= line)
    i = 5
    true_remaining = (true_cross - i) * TICK_TRACE_S
    slope_s = (temps[i] - temps[i - 2]) / (2 * TICK_TRACE_S)
    linear_eta = (line - temps[i]) / slope_s
    ode_eta, _ = physics.eta_from_trend(temps[i], slope_s, line, tau)
    assert abs(ode_eta - true_remaining) < abs(linear_eta - true_remaining)


# --- engine fork (needs a live engine) ---------------------------------------
def test_fork_is_deterministic_and_independent():
    from sentinel.engine import Engine
    start, _ = DEMO_WINDOW
    eng = Engine()
    eng.seek(start)
    for _ in range(30):
        eng.tick()
    fork = eng.fork()
    live = [eng.tick().to_dict() for _ in range(15)]
    proj = [fork.tick().to_dict() for _ in range(15)]
    assert live == proj, "fork must reproduce the live trajectory bit-for-bit"

    # A second fork with an action must not disturb the (already-advanced) live.
    live_next = eng.tick().to_dict()
    g = eng.fork()
    g.apply_action("MIGRATE_JOB", "no-such-job", "rack-01")  # invalid -> no-op
    assert eng.tick().to_dict() == g.tick().to_dict()
    assert live_next["t"] < eng.t


# --- digital twin + counterfactual -------------------------------------------
@pytest.fixture(scope="module")
def burst_engine():
    from sentinel.engine import Engine
    start, _ = DEMO_WINDOW
    eng = Engine()
    pe = PredictionEngine(engine=eng)
    for frame, _preds in pe.attach(eng, (start, start + 4200)):
        if frame["t"] >= start + 4050:
            break
    return eng, pe


def test_twin_projection_shape(burst_engine):
    eng, _pe = burst_engine
    twin = DigitalTwin(eng)
    proj = twin.project("rack-00")
    assert len(proj.trajectory) == twin.horizon_ticks
    assert proj.peak_throttling > 0            # rack-00 is hot at the burst
    assert proj.peak_temp_mean > 0


def test_counterfactual_relief_is_monotone(burst_engine):
    eng, _pe = burst_engine
    twin = DigitalTwin(eng)
    state = eng.replayer.state
    heavy_running = [n for n, rk in state.pod_rack.items()
                     if rk == "rack-00" and eng.replayer.pods[n].heavy]
    base = twin.project("rack-00")

    def peak_after_migrating(count):
        fork = eng.fork()
        for n in heavy_running[:count]:
            fork.apply_action("MIGRATE_JOB", n, "rack-18")
        traj = twin._roll(fork, "rack-00")
        return _summarize("rack-00", eng.t, traj,
                          twin.horizon_ticks * TICK_TRACE_S, twin.sustained_ticks).peak_throttling

    p1 = peak_after_migrating(1)
    p10 = peak_after_migrating(10)
    # Migrating more running heavy jobs off the rack never increases the
    # projected peak, and 10 gives strictly more relief than the baseline.
    assert p10 <= p1 <= base.peak_throttling
    assert p10 < base.peak_throttling


def test_score_action_reports_averted_gpus(burst_engine):
    eng, pe = burst_engine
    state = eng.replayer.state
    job = next(n for n, rk in state.pod_rack.items()
               if rk == "rack-00" and eng.replayer.pods[n].heavy)
    out = pe.score_action("rack-00", job, "rack-18")
    assert out is not None and out["applied"] is True
    assert set(out) >= {"averted_peak_throttling_gpus", "baseline_peak_throttling",
                        "action_peak_throttling", "horizon_min"}
    assert out["averted_peak_throttling_gpus"] >= 0


def test_score_action_invalid_is_guarded(burst_engine):
    _eng, pe = burst_engine
    out = pe.score_action("rack-00", "no-such-job", "rack-18")
    assert out is not None and out["applied"] is False


def test_twin_enriches_thermal_prediction(burst_engine):
    eng, pe = burst_engine
    # The engine is mid-window; take the next frame and check the enrichment.
    frame = eng.tick().to_dict()
    preds = pe.on_frame(frame)
    thermal = [p for p in preds if p.type == "THERMAL_THROTTLE" and p.target["id"] == "rack-00"]
    assert thermal, "expected a thermal prediction on the hot rack"
    metrics = {e.metric for e in thermal[0].evidence}
    assert "projected_peak_throttling_gpus" in metrics


# --- FR-3 duration-weighted queue pressure -----------------------------------
def test_weighted_pressure_scales_with_duration_class():
    from sentinel.predict.predictors import _weighted_queue_pressure
    # Same count + demand, but long Burstable jobs weigh far more than short BE.
    short = [{"heavy": True, "gpu_demand": 1.0, "gpu_milli": 1000, "qos": "BE"}] * 4
    long = [{"heavy": True, "gpu_demand": 1.0, "gpu_milli": 1000, "qos": "Burstable"}] * 4
    assert _weighted_queue_pressure(long) > 5 * _weighted_queue_pressure(short)
    # Non-heavy pods contribute nothing.
    assert _weighted_queue_pressure([{"heavy": False, "gpu_demand": 0.5,
                                      "gpu_milli": 500, "qos": "BE"}]) == 0.0


def test_bottleneck_emits_gpu_minutes_evidence():
    from sentinel.predict.predictors import SchedulingBottleneckPredictor
    rack = {"rack_id": "rack-00", "queued_heavy": 4, "util": 0.2, "throttling_gpus": 40,
            "queued_pods": 4}
    pods = [{"heavy": True, "gpu_demand": 1.0, "gpu_milli": 1000, "qos": "Burstable"}] * 4
    p = SchedulingBottleneckPredictor().predict(1000, rack, None, pods)
    assert p is not None
    metrics = {e.metric: e for e in p.evidence}
    gpu_min = metrics["queued_heavy_gpu_minutes"].value
    assert abs(gpu_min - 4 * 61.0) < 1e-6      # 4 heavy Burstable x 61 min
    assert p.severity in ("high", "critical")  # well above the count-only baseline


# --- FR-2 next-hotspot / target ranking --------------------------------------
def test_risk_board_ranks_hotspot_and_cool_targets(burst_engine):
    eng, pe = burst_engine
    frame = eng.tick().to_dict()
    pe.on_frame(frame)  # warm trends
    board = pe.risk_board(frame)
    assert board["hotspots"], "expected at least one hotspot"
    assert board["hotspots"][0]["rack_id"] == "rack-00"  # the sole hot rack
    assert board["hotspots"][0]["risk"] > 0
    # Best migration target is a cool rack with capacity (the T4 targets).
    top = board["targets"][0]
    assert top["free_gpus"] > 0
    assert top["rack_id"] != "rack-00"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
