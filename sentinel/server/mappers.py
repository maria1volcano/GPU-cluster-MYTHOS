"""Map sentinel backend models → frontend dashboard types (frontend/src/types/cluster.ts)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction, THERMAL_THROTTLE
from sentinel.telemetry.profiles import PROFILES
from sentinel.telemetry.sample import TelemetryFrame


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _risk_level(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "warning"
    if score >= 35:
        return "watch"
    return "healthy"


def _rack_risk(
    temp_c: float,
    trend: float,
    util_pct: float,
    queue_pct: float,
    power_kw: float,
    cooling_pct: float,
) -> float:
    temp_score = max(0.0, (temp_c - 60) * 2.2)
    trend_score = max(0.0, trend * 12)
    cooling_score = max(0.0, (85 - cooling_pct) * 0.9)
    util_score = util_pct * 0.25
    queue_score = queue_pct * 0.2
    power_score = max(0.0, (power_kw - 100) * 0.25)
    raw = temp_score + trend_score + cooling_score + util_score + queue_score + power_score
    return min(100.0, round(raw))


def _cooling_efficiency(temp_c: float, throttle_temp: float, throttling_gpus: int, active_gpus: int) -> float:
    if active_gpus <= 0:
        return 92.0
    headroom = max(0.0, throttle_temp - temp_c)
    base = 70.0 + min(25.0, headroom * 2.5)
    penalty = (throttling_gpus / active_gpus) * 35.0
    return max(45.0, min(96.0, base - penalty))


def _rack_position(index: int, cols: int = 7) -> Dict[str, float]:
    row, col = divmod(index, cols)
    return {"x": float(col - cols // 2), "y": 0.0, "z": float(row - 2)}


# 8-rack operator floor layout (matches mock dashboard spacing).
_MAP_FLOOR_LAYOUT = [
    {"x": -3.0, "z": -1.5},
    {"x": -1.0, "z": -1.5},
    {"x": 1.0, "z": -1.5},
    {"x": 3.0, "z": -1.5},
    {"x": -3.0, "z": 1.5},
    {"x": -1.0, "z": 1.5},
    {"x": 1.0, "z": 1.5},
    {"x": 3.0, "z": 1.5},
]


def select_map_racks(racks_out: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """Top racks by risk for the 3D floor — stable sort, fixed layout."""
    ranked = sorted(
        racks_out,
        key=lambda r: (-r["riskScore"], -r["gpuUtilizationPct"], r["id"]),
    )[:limit]
    mapped: List[Dict[str, Any]] = []
    for i, rack in enumerate(ranked):
        slot = _MAP_FLOOR_LAYOUT[i] if i < len(_MAP_FLOOR_LAYOUT) else {"x": 0.0, "z": 0.0}
        mapped.append(
            {
                **rack,
                "position": {"x": slot["x"], "y": 0.0, "z": slot["z"]},
                "label": rack.get("label") or rack["id"].replace("-", " ").title(),
            }
        )
    return mapped


def _slope_for_rack(rack_id: str, trends: Optional[Dict]) -> float:
    if not trends:
        return 0.0
    trend = trends.get(rack_id)
    if trend is None:
        return 0.0
    return getattr(trend, "slope_c_per_s", 0.0) * 60.0


def _primary_job_on_rack(rack_id: str, pod_rack: Optional[Dict[str, str]]) -> Optional[str]:
    if not pod_rack:
        return None
    jobs = sorted(p for p, rid in pod_rack.items() if rid == rack_id)
    return jobs[0] if jobs else None


def frame_to_cluster_state(
    frame: TelemetryFrame,
    replay_status: str,
    trends: Optional[Dict] = None,
    history: Optional[Dict[str, List[Dict]]] = None,
    pod_rack: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    racks_out: List[Dict[str, Any]] = []
    for i, rack in enumerate(frame.racks):
        prof = PROFILES.get(rack.gpu_model) if rack.gpu_model else None
        throttle = prof.throttle_temp if prof else 84.0
        util_pct = max(0.0, min(100.0, rack.util * 100.0))
        demand_gpus = round(rack.gpu_demand, 2)
        trend = _slope_for_rack(rack.rack_id, trends)
        power_kw = max(0.1, rack.power_w_total / 1000.0)
        queue_pct = min(98.0, rack.queued_heavy * 12.0 + rack.queued_pods * 4.0)
        cooling = _cooling_efficiency(
            rack.temp_c_mean_active, throttle, rack.throttling_gpus, max(1, rack.active_gpus)
        )
        risk_score = _rack_risk(
            rack.temp_c_mean_active, trend, util_pct, queue_pct, power_kw, cooling
        )
        hist = list((history or {}).get(rack.rack_id, []))
        hist.append(
            {
                "t": frame.t,
                "temp": rack.temp_c_mean_active,
                "power": power_kw,
                "util": util_pct,
            }
        )
        hist = hist[-30:]
        if history is not None:
            history[rack.rack_id] = hist

        racks_out.append(
            {
                "id": rack.rack_id,
                "label": rack.rack_id.replace("-", " ").title(),
                "heavyJobsQueued": rack.queued_heavy,
                "temperatureC": round(rack.temp_c_mean_active, 1),
                "temperatureTrendCPerMin": round(trend, 2),
                "powerDrawKw": round(power_kw, 1),
                "gpuUtilizationPct": round(util_pct, 1),
                "gpuDemandGpus": demand_gpus,
                "activePodCount": rack.active_pods,
                "coolingEfficiencyPct": round(cooling, 1),
                "queuePressurePct": round(queue_pct, 1),
                "activeJobId": _primary_job_on_rack(rack.rack_id, pod_rack),
                "workloadType": "training" if rack.gpu_demand > 1 else ("inference" if rack.gpu_demand > 0 else "idle"),
                "riskScore": risk_score,
                "riskLevel": _risk_level(risk_score),
                "position": _rack_position(i),
                "history": hist,
            }
        )

    active = sum(1 for r in frame.racks if r.gpu_demand > 0)
    avg_temp = sum(r.temp_c_mean_active for r in frame.racks) / len(frame.racks)
    total_power = sum(r.power_w_total for r in frame.racks) / 1000.0
    avg_cool = sum(r["coolingEfficiencyPct"] for r in racks_out) / len(racks_out)
    heavy_queued = sum(r.queued_heavy for r in frame.racks)
    projected = sum(1 for r in racks_out if r["riskLevel"] in ("warning", "critical"))

    return {
        "timestamp": _trace_iso(frame.t),
        "replayStatus": replay_status,
        "totalRacks": len(racks_out),
        "heavyJobsQueued": heavy_queued,
        "averageGpuUtilizationPct": round(
            sum(r["gpuUtilizationPct"] for r in racks_out) / max(1, len(racks_out)), 1
        )
        if racks_out
        else 0,
        "averageQueuePressurePct": round(
            sum(r["queuePressurePct"] for r in racks_out) / max(1, len(racks_out)), 1
        ),
        "projectedRiskCount": projected,
        "averageTemperatureC": round(avg_temp, 1),
        "totalPowerDrawKw": round(total_power, 1),
        "averageCoolingEfficiencyPct": round(avg_cool, 1),
        "activeJobs": active,
        "agentConfidencePct": 78,
        "racks": racks_out,
        "mapRacks": select_map_racks(racks_out),
        "dcgmSampleCount": len(frame.samples),
        "dcgmThrottlingGpus": sum(1 for s in frame.samples if s.throttle_reasons),
    }


def _evidence_to_signals(evidence: List[Evidence]) -> List[Dict[str, Any]]:
    signals = []
    for e in evidence:
        sev = "watch"
        if e.metric == "rack_temp_c" and e.slope_per_min and e.slope_per_min > 1.2:
            sev = "critical"
        elif e.metric == "queued_heavy_jobs" and e.value and e.value >= 3:
            sev = "warning"
        parts = []
        if e.current is not None:
            parts.append(f"{e.current}")
        if e.slope_per_min is not None:
            parts.append(f"{e.slope_per_min:+.1f}/min")
        if e.value is not None:
            parts.append(f"{e.value}")
        signals.append(
            {
                "name": e.metric.replace("_", " ").title(),
                "value": " · ".join(parts) or e.metric,
                "trend": "rising"
                if e.slope_per_min and e.slope_per_min > 0
                else ("falling" if e.slope_per_min and e.slope_per_min < 0 else "stable"),
                "severity": sev,
            }
        )
    return signals


def recommendation_to_agent(
    rec: Recommendation,
    prediction: Prediction,
    status: str = "pending",
    *,
    alert_text: Optional[str] = None,
    alert_status: Optional[str] = None,
    alert_audio_url: Optional[str] = None,
) -> Dict[str, Any]:
    issue_map = {
        THERMAL_THROTTLE: "thermal_throttling",
        "SCHEDULING_BOTTLENECK": "scheduling_bottleneck",
        "NODE_INSTABILITY": "node_instability",
    }
    rack_id = prediction.target.get("id", rec.from_rack or "rack-00")
    risk_score = min(100, max(35, int(60 + prediction.confidence * 40)))
    payload: Dict[str, Any] = {
        "id": rec.recommendation_id,
        "createdAt": _iso_now(),
        "status": status,
        "affectedRackId": rec.from_rack or rack_id,
        "destinationRackId": rec.to_rack,
        "affectedJobId": rec.job_id,
        "riskScore": risk_score,
        "riskLevel": _risk_level(risk_score),
        "predictedIssue": issue_map.get(prediction.type, "projected_throttling_risk"),
        "timeToImpactMinutes": max(0, round(prediction.eta_seconds / 60)),
        "actionType": "migrate_job" if rec.action == "MIGRATE_JOB" else "monitor",
        "title": f"Migrate {rec.job_id} off {rec.from_rack}",
        "summary": rec.justification,
        "recommendation": rec.expected_effect,
        "explanation": rec.as_card(),
        "confidencePct": round(prediction.confidence * 100),
        "signals": _evidence_to_signals(rec.evidence),
    }
    if alert_text:
        payload["alertText"] = alert_text
    if alert_status:
        payload["alertStatus"] = alert_status
    if alert_audio_url:
        payload["alertAudioUrl"] = alert_audio_url
    return payload


def decision_entry_to_frontend(entry: Dict[str, Any]) -> Dict[str, Any]:
    action = entry.get("operator_action", "").lower()
    outcome = entry.get("outcome", "")
    type_map = {
        "APPROVE": "operator_approved",
        "OVERRIDE": "operator_overrode",
        "DISMISS": "operator_event",
    }
    msg = f"{action} → {outcome}" if outcome else action
    sev = "healthy" if outcome == "AVERTED" else "watch"
    t = entry.get("t", 0)
    return {
        "id": entry.get("decision_id", f"dec-{t}"),
        "timestamp": _trace_iso(int(t)) if t else _iso_now(),
        "type": type_map.get(entry.get("operator_action", ""), "agent_event"),
        "message": msg,
        "severity": sev,
    }


def telemetry_event(
    message: str,
    *,
    severity: str = "watch",
    event_type: str = "agent_event",
    rack_id: Optional[str] = None,
    t: Optional[int] = None,
) -> Dict[str, Any]:
    ts = _trace_iso(t) if t is not None else _iso_now()
    return {
        "id": f"evt-{ts}-{abs(hash(message)) % 10_000}",
        "timestamp": ts,
        "type": event_type,
        "rackId": rack_id,
        "message": message,
        "severity": severity,
    }
