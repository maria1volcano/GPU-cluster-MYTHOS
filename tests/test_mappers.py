"""Tests for dashboard mappers."""
from __future__ import annotations

import json

from sentinel.config import FIXTURES_DIR
from sentinel.predict.schema import (
    Evidence,
    Prediction,
    THERMAL_THROTTLE,
    NODE_INSTABILITY,
    SCHEDULING_BOTTLENECK,
)
from sentinel.server.mappers import (
    _evidence_to_signals,
    _time_to_impact_minutes,
    alert_prediction_sort_key,
    frame_to_cluster_state,
    predictions_to_frontend,
    select_alert_prediction,
    select_map_racks,
)
from sentinel.telemetry.sample import (
    GpuTelemetrySample,
    QueuedPodInfo,
    RackAggregate,
    TelemetryFrame,
)


def _frame_from_fixture(raw: dict) -> TelemetryFrame:
    samples = tuple(
        GpuTelemetrySample(
            gpu_id=s["gpu_id"],
            node_sn=s["node_sn"],
            rack_id=s["rack_id"],
            model=s["model"],
            t=s["t"],
            util=s["util"],
            temp_c=s["temp_c"],
            power_w=s["power_w"],
            sm_clock_mhz=s["sm_clock_mhz"],
            mem_clock_mhz=s["mem_clock_mhz"],
            mem_used_mib=s["mem_used_mib"],
            throttle_reasons=tuple(s.get("throttle_reasons") or ()),
            xid_errors=s.get("xid_errors", 0),
            ecc_errors=s.get("ecc_errors") or {"volatile": 0, "aggregate": 0},
        )
        for s in raw["samples"]
    )
    racks = tuple(
        RackAggregate(
            rack_id=r["rack_id"],
            gpu_model=r.get("gpu_model"),
            num_nodes=r["num_nodes"],
            capacity_gpus=r["capacity_gpus"],
            gpu_demand=r["gpu_demand"],
            util=r["util"],
            active_gpus=r["active_gpus"],
            temp_c_mean_active=r["temp_c_mean_active"],
            temp_c_max=r["temp_c_max"],
            power_w_total=r["power_w_total"],
            throttling_gpus=r["throttling_gpus"],
            active_pods=r["active_pods"],
            queued_pods=r["queued_pods"],
            queued_heavy=r["queued_heavy"],
        )
        for r in raw["racks"]
    )
    queue = tuple(
        QueuedPodInfo(
            name=q["name"],
            num_gpu=q["num_gpu"],
            gpu_milli=q["gpu_milli"],
            gpu_demand=q["gpu_demand"],
            qos=q["qos"],
            heavy=q["heavy"],
            waiting_s=q["waiting_s"],
            target_rack=q.get("target_rack"),
        )
        for q in raw.get("queue", [])
    )
    return TelemetryFrame(
        tick=raw["tick"],
        t=raw["t"],
        trace_day=raw["trace_day"],
        samples=samples,
        racks=racks,
        queue=queue,
        cluster=dict(raw["cluster"]),
    )


def _rack(rid: str, *, demand: float = 0, util: float = 0, risk: float = 10, power: float = 10.0) -> dict:
    return {
        "id": rid,
        "gpuDemandGpus": demand,
        "gpuUtilizationPct": util,
        "queuePressurePct": 0,
        "riskScore": risk,
        "temperatureC": 83.0 if demand > 0 else 34.0,
        "powerDrawKw": 24.0 if demand > 0 else power,
    }


def test_map_racks_pin_includes_migration_target_outside_floor():
    racks = [_rack(f"rack-{i:02d}", demand=1 if i == 0 else 0, util=20 if i == 0 else 0) for i in range(42)]
    mapped = select_map_racks(racks, pin_ids=["rack-00", "rack-18"])
    ids = [r["id"] for r in mapped]
    assert "rack-00" in ids
    assert "rack-18" in ids
    assert len(mapped) == 8


def test_map_racks_keep_fixed_floor_order_with_pins():
    racks = [_rack(f"rack-{i:02d}", demand=1 if i == 0 else 0, util=20 if i == 0 else 0) for i in range(8)]
    mapped = select_map_racks(racks, pin_ids=["rack-00", "rack-02"])
    assert [r["id"] for r in mapped] == [f"rack-{i:02d}" for i in range(8)]


def test_map_racks_slot_positions_follow_rack_id():
    racks = [_rack(f"rack-{i:02d}") for i in range(8)]
    mapped = select_map_racks(racks, pin_ids=["rack-02", "rack-00"])
    assert mapped[1]["id"] == "rack-01"
    assert mapped[1]["position"]["x"] == -1.0
    assert mapped[2]["id"] == "rack-02"
    assert mapped[2]["position"]["x"] == 1.0


def test_time_to_impact_uses_lead_time_when_eta_zero():
    pred = Prediction(
        prediction_id="p1",
        type=THERMAL_THROTTLE,
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=0.0,
        severity="critical",
        confidence=0.9,
        evidence=[],
        t=0,
    )
    assert _time_to_impact_minutes(pred) == 8


def test_time_to_impact_from_positive_eta():
    pred = Prediction(
        prediction_id="p2",
        type=THERMAL_THROTTLE,
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=300.0,
        severity="high",
        confidence=0.8,
        evidence=[],
        t=0,
    )
    assert _time_to_impact_minutes(pred) == 5


def test_evidence_signals_at_throttle_ceiling():
    evidence = [
        Evidence(metric="rack_temp_c", slope_per_min=0.0, threshold=84.0, current=83.8),
        Evidence(metric="thermal_headroom_c", value=0.2),
        Evidence(metric="throttling_gpus", value=46.0),
        Evidence(metric="rack_util", value=0.198),
    ]
    signals = _evidence_to_signals(evidence)
    assert signals[0]["name"] == "Rack temperature"
    assert "pinned at throttle line" in signals[0]["value"]
    assert signals[1]["name"] == "Thermal headroom"
    assert "0.2°C below 84°C limit" in signals[1]["value"]
    assert signals[2]["value"] == "46 GPUs clock-capped"


def test_select_alert_prediction_prefers_thermal_on_tie():
    thermal = Prediction(
        prediction_id="p-thermal",
        type=THERMAL_THROTTLE,
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=0.0,
        severity="critical",
        confidence=0.85,
        evidence=[],
        t=0,
    )
    bottleneck = Prediction(
        prediction_id="p-bottleneck",
        type=SCHEDULING_BOTTLENECK,
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=0.0,
        severity="critical",
        confidence=0.85,
        evidence=[],
        t=0,
    )
    assert select_alert_prediction([bottleneck, thermal]).type == THERMAL_THROTTLE


def test_predictions_to_frontend_collapses_node_instability_per_rack():
    nodes = [
        Prediction(
            prediction_id=f"p-node-{i}",
            type=NODE_INSTABILITY,
            target={"kind": "node", "id": f"node-{i}", "rack_id": "rack-00"},
            eta_seconds=0.0,
            severity="high",
            confidence=0.6 + i * 0.01,
            evidence=[],
            t=0,
        )
        for i in range(5)
    ]
    cards = predictions_to_frontend(nodes)
    assert len(cards) == 1
    assert cards[0]["type"] == "node_instability"
    assert cards[0]["targetId"] == "node-4"


def test_golden_frame_maps_contract_rack_fields():
    with open(FIXTURES_DIR / "telemetry_frame.golden.json") as fh:
        raw = json.load(fh)
    frame = _frame_from_fixture(raw)
    assert len(frame.racks) == 42
    state = frame_to_cluster_state(frame, "running")
    rack00 = next(r for r in state["racks"] if r["id"] == "rack-00")
    src = next(r for r in frame.racks if r.rack_id == "rack-00")
    assert rack00["temperatureC"] == round(src.temp_c_mean_active, 1)
    assert rack00["heavyJobsQueued"] == src.queued_heavy
    assert rack00["gpuDemandGpus"] == round(src.gpu_demand, 2)
    assert rack00["throttleTempC"] == 84.0
    assert state["dcgmSampleCount"] == len(frame.samples)


def test_decision_entry_surfaced_maps_to_recommendation_generated():
    from sentinel.server.mappers import decision_entry_to_frontend

    mapped = decision_entry_to_frontend(
        {
            "decision_id": "dec-0001",
            "t": 12824140,
            "operator_action": "SURFACED",
            "outcome": "PENDING",
            "lead_time_seconds": 480.0,
            "recommendation": {
                "job_id": "openb-pod-1",
                "from_rack": "rack-00",
                "to_rack": "rack-18",
            },
        }
    )
    assert mapped["type"] == "recommendation_generated"
    assert "openb-pod-1" in mapped["message"]
    assert mapped["leadTimeMinutes"] == 8


def test_decision_entry_override_shows_reason():
    from sentinel.server.mappers import decision_entry_to_frontend

    mapped = decision_entry_to_frontend(
        {
            "decision_id": "dec-0002",
            "t": 12824150,
            "operator_action": "OVERRIDE",
            "outcome": "OVERRIDDEN",
            "operator_alternative": {"reason": "Do not move priority workload"},
            "recommendation": {
                "job_id": "openb-pod-1",
                "from_rack": "rack-00",
                "to_rack": "rack-18",
            },
        }
    )
    assert mapped["type"] == "operator_overrode"
    assert mapped["message"] == "Override — Do not move priority workload"
    assert mapped["overrideReason"] == "Do not move priority workload"
    assert "UNKNOWN" not in mapped["message"]
