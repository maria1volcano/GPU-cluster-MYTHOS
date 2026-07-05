"""Map sentinel backend models → frontend dashboard types (frontend/src/types/cluster.ts)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sentinel.config import BURN_IN_S, DEMO_WINDOW, LEAD_TIME_S
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


def _rack_floor_slot(rack_id: str) -> int:
    """Stable floor index: rack-00 / R-01 → slot 0, rack-01 / R-02 → slot 1, …"""
    match = re.search(r"(\d+)$", rack_id)
    if not match:
        return 99
    n = int(match.group(1))
    if rack_id.startswith("R-"):
        return n - 1
    return n


def _demo_floor_ids(limit: int, by_id: Dict[str, Dict[str, Any]]) -> Optional[List[str]]:
    """Prefer rack-00..07 (live) or R-01..08 (mock) when the full floor exists."""
    live = [f"rack-{i:02d}" for i in range(limit)]
    if all(rid in by_id for rid in live):
        return live
    mock = [f"R-{i:02d}" for i in range(1, limit + 1)]
    if all(rid in by_id for rid in mock):
        return mock
    return None


def select_map_racks(
    racks_out: List[Dict[str, Any]],
    limit: int = 8,
    pin_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Eight racks on the 3D floor — each rack keeps a fixed slot by id."""
    pin_ids = pin_ids or []
    by_id = {r["id"]: r for r in racks_out}
    floor_ids = _demo_floor_ids(limit, by_id)

    seen: set[str] = set()
    chosen_ids: List[str] = []
    for rid in pin_ids:
        if rid in by_id and rid not in seen:
            chosen_ids.append(rid)
            seen.add(rid)
    if floor_ids is not None:
        for rid in floor_ids:
            if len(chosen_ids) >= limit:
                break
            if rid not in seen:
                chosen_ids.append(rid)
                seen.add(rid)
    if len(chosen_ids) < limit:
        ranked = sorted(
            (r for r in racks_out if r["id"] not in seen),
            key=lambda r: (
                -float(r.get("gpuDemandGpus") or 0),
                -float(r.get("queuePressurePct") or 0),
                -r["riskScore"],
                -r["gpuUtilizationPct"],
                r["id"],
            ),
        )
        for rack in ranked:
            if len(chosen_ids) >= limit:
                break
            chosen_ids.append(rack["id"])
            seen.add(rack["id"])

    mapped: List[Dict[str, Any]] = []
    for rid in chosen_ids[:limit]:
        rack = by_id.get(rid)
        if rack is None:
            continue
        slot_idx = _rack_floor_slot(rid)
        slot = (
            _MAP_FLOOR_LAYOUT[slot_idx]
            if 0 <= slot_idx < len(_MAP_FLOOR_LAYOUT)
            else {"x": 0.0, "z": 0.0}
        )
        mapped.append(
            {
                **rack,
                "position": {"x": slot["x"], "y": 0.0, "z": slot["z"]},
                "label": rack.get("label") or rack["id"].replace("-", " ").title(),
            }
        )
    mapped.sort(key=lambda r: _rack_floor_slot(r["id"]))
    return mapped


def merge_map_racks_preserving_activity(
    prev: List[Dict[str, Any]],
    new: List[Dict[str, Any]],
    *,
    hot_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Keep floor activity when stress seek would drop non-hot racks to trace-idle."""
    if not new:
        return prev
    if not prev:
        return new
    prev_by_id = {r["id"]: r for r in prev}
    merged: List[Dict[str, Any]] = []
    for rack in new:
        rid = rack["id"]
        if hot_id and rid == hot_id:
            merged.append(rack)
            continue
        prior = prev_by_id.get(rid)
        if prior is None:
            merged.append(rack)
            continue
        out = dict(rack)
        for key in (
            "gpuUtilizationPct",
            "gpuDemandGpus",
            "queuePressurePct",
            "temperatureC",
            "powerDrawKw",
            "riskScore",
        ):
            pv = float(prior.get(key) or 0)
            nv = float(rack.get(key) or 0)
            if pv > nv:
                out[key] = prior[key]
        if float(out.get("riskScore") or 0) != float(rack.get("riskScore") or 0):
            out["riskLevel"] = _risk_level(int(out["riskScore"]))
        if prior.get("activeJobId") and not rack.get("activeJobId"):
            out["activeJobId"] = prior["activeJobId"]
        elif rack.get("activeJobId") and rack.get("activeJobId") != prior.get("activeJobId"):
            out["activeJobId"] = rack["activeJobId"]
        if prior.get("workloadType") not in (None, "idle") and rack.get("workloadType") == "idle":
            out["workloadType"] = prior["workloadType"]
        merged.append(out)
    merged.sort(key=lambda r: _rack_floor_slot(r["id"]))
    return merged


def _evidence_value(prediction: Prediction, metric: str) -> Optional[float]:
    for e in prediction.evidence:
        if e.metric == metric and e.value is not None:
            return float(e.value)
    return None


def _time_to_impact_minutes(prediction: Prediction) -> int:
    """Operator-facing impact window, from REAL signals only: a positive
    predictor eta (approach-from-below countdown) or the digital twin's
    do-nothing projection window (an already-throttling rack worsens within
    it). The last-resort LEAD_TIME_S default only applies to engine-less
    predictions (fixtures/WS replays) that carry no twin evidence."""
    if prediction.eta_seconds > 30:
        return max(1, round(prediction.eta_seconds / 60))
    horizon_min = _evidence_value(prediction, "projected_horizon_min")
    if horizon_min:
        return max(1, round(horizon_min))
    return max(1, round(LEAD_TIME_S / 60))


def prediction_lead_seconds(prediction: Prediction) -> float:
    """Decision-log KPI (PRD §8): seconds between this alert and the modeled
    incident, from real signals only — a positive predictor eta, else the
    twin's projected-worsening window. Returns the raw eta (possibly 0.0)
    only when no forward-looking signal exists at all, so the log never
    contains a fabricated number."""
    if prediction.eta_seconds > 30:
        return float(prediction.eta_seconds)
    horizon_min = _evidence_value(prediction, "projected_horizon_min")
    if horizon_min:
        return float(horizon_min) * 60.0
    return float(prediction.eta_seconds)


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


def _rack_load_snapshot(frame: TelemetryFrame, rack_id: str) -> Dict[str, Any]:
    """Lightweight rack load metrics for operator impact payloads."""
    rack = next((r for r in frame.racks if r.rack_id == rack_id), None)
    if rack is None:
        return {}
    util_pct = max(0.0, min(100.0, rack.util * 100.0))
    queue_pct = min(98.0, rack.queued_heavy * 12.0 + rack.queued_pods * 4.0)
    return {
        "gpuDemandGpus": round(rack.gpu_demand, 2),
        "gpuUtilizationPct": round(util_pct, 1),
        "queuePressurePct": round(queue_pct, 1),
    }


def _severity_to_risk_level(severity: str) -> str:
    return {"critical": "critical", "high": "warning", "medium": "watch"}.get(severity, "watch")


_SEVERITY_RANK = {"critical": 3, "high": 2, "medium": 1}
# Tie-break: thermal > bottleneck > node instability when scores match.
_TYPE_PRIORITY = {
    THERMAL_THROTTLE: 0,
    "SCHEDULING_BOTTLENECK": 1,
    "NODE_INSTABILITY": 2,
}


def alert_prediction_sort_key(prediction: Prediction) -> tuple[float, int, str]:
    """Deterministic ordering for alert pick + active-predictions panel."""
    score = _SEVERITY_RANK.get(prediction.severity, 0) * 100 + prediction.confidence * 50
    target = str(prediction.target.get("rack_id") or prediction.target.get("id", ""))
    type_rank = _TYPE_PRIORITY.get(prediction.type, 9)
    return (score, -type_rank, target)


def select_alert_prediction(predictions: List[Prediction]) -> Optional[Prediction]:
    """Pick the highest-severity actionable prediction with stable tie-breaks."""
    actionable = [
        p
        for p in predictions
        if p.type in {THERMAL_THROTTLE, "SCHEDULING_BOTTLENECK", "NODE_INSTABILITY"}
    ]
    if not actionable:
        return None
    return max(actionable, key=alert_prediction_sort_key)


def rank_actionable_predictions(predictions: List[Prediction]) -> List[Prediction]:
    """Actionable predictions in alert priority order (for fallback when recommend fails)."""
    actionable = [
        p
        for p in predictions
        if p.type in {THERMAL_THROTTLE, "SCHEDULING_BOTTLENECK", "NODE_INSTABILITY"}
    ]
    return sorted(actionable, key=alert_prediction_sort_key, reverse=True)


def _collapse_node_instability(predictions: List[Prediction]) -> List[Prediction]:
    """One node-instability card per rack — worst node only (stable UI)."""
    best_by_rack: Dict[str, Prediction] = {}
    others: List[Prediction] = []
    for p in predictions:
        if p.type != "NODE_INSTABILITY":
            others.append(p)
            continue
        rack_id = str(p.target.get("rack_id") or p.target.get("id", ""))
        prev = best_by_rack.get(rack_id)
        if prev is None or alert_prediction_sort_key(p) > alert_prediction_sort_key(prev):
            best_by_rack[rack_id] = p
    return others + list(best_by_rack.values())


def predictions_to_frontend(predictions: List[Prediction]) -> List[Dict[str, Any]]:
    """Active M3 predictions surfaced on the operator dashboard."""
    issue_map = {
        THERMAL_THROTTLE: "thermal_throttling",
        "SCHEDULING_BOTTLENECK": "scheduling_bottleneck",
        "NODE_INSTABILITY": "node_instability",
    }
    label_map = {
        THERMAL_THROTTLE: "Thermal throttling",
        "SCHEDULING_BOTTLENECK": "Scheduling bottleneck",
        "NODE_INSTABILITY": "Node instability",
    }
    seen: set[tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    ranked = sorted(
        _collapse_node_instability(predictions),
        key=alert_prediction_sort_key,
        reverse=True,
    )
    for p in ranked:
        if p.type not in issue_map:
            continue
        target_id = str(p.target.get("id", ""))
        key = (p.type, target_id)
        if key in seen:
            continue
        seen.add(key)
        rack_id = p.target.get("rack_id") or (target_id if p.target.get("kind") == "rack" else None)
        out.append(
            {
                "type": issue_map[p.type],
                "label": label_map[p.type],
                "targetKind": p.target.get("kind", "rack"),
                "targetId": target_id,
                "rackId": rack_id,
                "severity": p.severity,
                "riskLevel": _severity_to_risk_level(p.severity),
                "confidencePct": round(p.confidence * 100),
                "etaMinutes": _time_to_impact_minutes(p),
                "signals": _evidence_to_signals(p.evidence),
            }
        )
    return out[:8]


def frame_to_cluster_state(
    frame: TelemetryFrame,
    replay_status: str,
    trends: Optional[Dict] = None,
    history: Optional[Dict[str, List[Dict]]] = None,
    pod_rack: Optional[Dict[str, str]] = None,
    map_pin_racks: Optional[List[str]] = None,
    predictions: Optional[List[Prediction]] = None,
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
        if abs(trend) < 0.35 and rack.temp_c_mean_active >= throttle - 1.5:
            hist_slope = _slope_from_history(hist)
            if abs(hist_slope) >= abs(trend):
                trend = hist_slope
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
                "throttleTempC": round(throttle, 1),
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
    map_racks = select_map_racks(racks_out, pin_ids=map_pin_racks)
    floor_utils = [r["gpuUtilizationPct"] for r in map_racks if r["gpuUtilizationPct"] > 0]
    if not floor_utils:
        floor_utils = [r["gpuUtilizationPct"] for r in map_racks]

    return {
        "timestamp": _trace_iso(frame.t),
        "replayStatus": replay_status,
        "totalRacks": len(racks_out),
        "heavyJobsQueued": heavy_queued,
        "averageGpuUtilizationPct": round(
            sum(floor_utils) / max(1, len(floor_utils)), 1
        )
        if floor_utils
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
        "mapRacks": map_racks,
        "dcgmSampleCount": len(frame.samples),
        "dcgmThrottlingGpus": sum(1 for s in frame.samples if s.throttle_reasons),
        "activePredictions": predictions_to_frontend(predictions or []),
    }


def _slope_from_history(hist: List[Dict[str, Any]]) -> float:
    """°C/min from recent chart points when the live trend filter is flat at the ceiling."""
    if len(hist) < 2:
        return 0.0
    pts = hist[-min(12, len(hist)) :]
    if len(pts) < 2:
        return 0.0
    mean_t = sum(p["t"] for p in pts) / len(pts)
    mean_v = sum(p["temp"] for p in pts) / len(pts)
    s_tt = sum((p["t"] - mean_t) ** 2 for p in pts)
    if s_tt <= 0:
        return 0.0
    s_tv = sum((p["t"] - mean_t) * (p["temp"] - mean_v) for p in pts)
    slope_per_s = s_tv / s_tt
    return round(slope_per_s * 60.0, 2)


def _evidence_to_signals(evidence: List[Evidence]) -> List[Dict[str, Any]]:
    by_metric = {e.metric: e for e in evidence}
    throttling = float(by_metric["throttling_gpus"].value) if "throttling_gpus" in by_metric else 0.0
    headroom = float(by_metric["thermal_headroom_c"].value) if "thermal_headroom_c" in by_metric else None
    temp_ev = by_metric.get("rack_temp_c")
    throttle = temp_ev.threshold if temp_ev and temp_ev.threshold is not None else 84.0
    operator_metrics = (
        "rack_temp_c",
        "thermal_headroom_c",
        "throttling_gpus",
        "queued_heavy_jobs",
        "rack_util",
        "queued_heavy_gpu_minutes",
        "xid_errors",
        "ecc_errors_volatile",
        "hw_thermal_gpus",
        "clock_derated_gpus",
    )
    signals: List[Dict[str, Any]] = []
    for metric in operator_metrics:
        e = by_metric.get(metric)
        if e is None:
            continue
        signal = _format_operator_signal(
            e, throttling=throttling, headroom=headroom, throttle=throttle
        )
        if signal is not None:
            signals.append(signal)
    return signals


def _format_operator_signal(
    e: Evidence,
    *,
    throttling: float,
    headroom: Optional[float],
    throttle: float = 84.0,
) -> Optional[Dict[str, Any]]:
    if e.metric == "rack_temp_c":
        temp = e.current
        slope = e.slope_per_min or 0.0
        if temp is None:
            return None
        at_ceiling = temp >= throttle - 1.0 or (headroom is not None and headroom <= 1.0)
        if abs(slope) < 0.35 and (at_ceiling or throttling >= 5):
            value = f"{temp:.1f}°C · pinned at throttle line"
            trend = "rising"
            sev = "critical"
        elif slope >= 0.35:
            value = f"{temp:.1f}°C · +{slope:.1f}°C/min"
            trend = "rising"
            sev = "critical" if slope > 1.2 else "warning"
        elif slope <= -0.35:
            value = f"{temp:.1f}°C · {slope:.1f}°C/min"
            trend = "falling"
            sev = "watch"
        else:
            value = f"{temp:.1f}°C · stable"
            trend = "stable"
            sev = "watch"
        return {"name": "Rack temperature", "value": value, "trend": trend, "severity": sev}

    if e.metric == "thermal_headroom_c":
        if e.value is None:
            return None
        val = float(e.value)
        if val <= 0:
            value = f"Above {throttle:.0f}°C throttle limit"
            sev = "critical"
        elif val < 1.0:
            value = f"{val:.1f}°C below {throttle:.0f}°C limit"
            sev = "critical"
        else:
            value = f"{val:.1f}°C below {throttle:.0f}°C limit"
            sev = "warning" if val < 3.0 else "watch"
        return {"name": "Thermal headroom", "value": value, "trend": "falling", "severity": sev}

    if e.metric == "throttling_gpus":
        count = int(e.value or 0)
        if count <= 0:
            return None
        sev = "critical" if count >= 10 else "warning"
        return {
            "name": "GPU throttling",
            "value": f"{count} GPUs clock-capped",
            "trend": "rising",
            "severity": sev,
        }

    if e.metric == "queued_heavy_jobs":
        count = int(e.value or 0)
        if count <= 0:
            return None
        sev = "critical" if count >= 5 else "warning"
        return {
            "name": "Heavy jobs queued",
            "value": f"{count} jobs waiting",
            "trend": "rising",
            "severity": sev,
        }

    if e.metric == "rack_util":
        util = float(e.value or 0)
        return {
            "name": "Scheduled load",
            "value": f"{util * 100:.0f}% rack capacity",
            "trend": "rising" if util > 0.15 else "stable",
            "severity": "warning" if util > 0.15 else "watch",
        }

    if e.metric == "queued_heavy_gpu_minutes":
        pressure = float(e.value or 0)
        if pressure <= 0:
            return None
        return {
            "name": "Queue pressure",
            "value": f"{pressure:.0f} GPU·min inbound",
            "trend": "rising",
            "severity": "critical" if pressure >= 120 else "warning",
        }

    if e.metric == "xid_errors":
        count = int(e.value or 0)
        if count <= 0:
            return None
        return {
            "name": "XID driver faults",
            "value": f"{count} fault(s) this tick",
            "trend": "rising",
            "severity": "critical",
        }

    if e.metric == "ecc_errors_volatile":
        count = int(e.value or 0)
        if count <= 0:
            return None
        return {
            "name": "Volatile ECC errors",
            "value": f"{count} error(s) this tick",
            "trend": "rising",
            "severity": "warning" if count < 3 else "critical",
        }

    if e.metric == "hw_thermal_gpus":
        count = int(e.value or 0)
        if count <= 0:
            return None
        return {
            "name": "Hardware thermal limit",
            "value": f"{count} GPU(s) above HW limit",
            "trend": "rising",
            "severity": "critical" if count >= 3 else "warning",
        }

    if e.metric == "clock_derated_gpus":
        count = int(e.value or 0)
        if count <= 0:
            return None
        return {
            "name": "Clock derating",
            "value": f"{count} GPU(s) with reduced clocks",
            "trend": "rising",
            "severity": "warning",
        }

    return None


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
    rack_id = prediction.target.get("rack_id") or prediction.target.get("id", rec.from_rack or "rack-00")
    if prediction.type == "SCHEDULING_BOTTLENECK":
        title = f"Relieve queue pressure on {rec.from_rack}"
    elif prediction.type == "NODE_INSTABILITY":
        node = prediction.target.get("id", "node")
        title = f"Evacuate unstable node {node} on {rec.from_rack}"
    else:
        title = f"Migrate {rec.job_id} off {rec.from_rack}"
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
        "timeToImpactMinutes": _time_to_impact_minutes(prediction),
        "actionType": "migrate_job" if rec.action == "MIGRATE_JOB" else "monitor",
        "title": title,
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
    action = entry.get("operator_action", "")
    action_lower = action.lower()
    outcome = entry.get("outcome", "")
    type_map = {
        "APPROVE": "operator_approved",
        "OVERRIDE": "operator_overrode",
        "DISMISS": "operator_event",
        "SURFACED": "recommendation_generated",
    }
    rec = entry.get("recommendation") or {}
    alt = entry.get("operator_alternative") or {}
    if action == "SURFACED" and rec:
        msg = (
            f"Recommendation surfaced: migrate {rec.get('job_id', 'workload')} "
            f"{rec.get('from_rack', '?')} → {rec.get('to_rack', '?')}"
        )
    elif action == "OVERRIDE":
        reason = (alt.get("reason") or "").strip()
        job = rec.get("job_id")
        from_r = rec.get("from_rack")
        if reason:
            msg = f"Override — {reason}"
        elif job and from_r:
            msg = f"Override — {job} stays on {from_r}"
        else:
            msg = "Override — no migration applied"
    elif action == "APPROVE" and outcome == "AVERTED":
        job = rec.get("job_id")
        to_r = rec.get("to_rack")
        msg = f"Approved — incident averted{f' ({job} → {to_r})' if job and to_r else ''}"
    elif outcome:
        msg = f"{action_lower} → {outcome.lower()}"
    else:
        msg = action_lower or "logged"
    sev = "healthy" if outcome == "AVERTED" else ("critical" if action == "SURFACED" else "watch")
    if outcome == "AVERTED":
        entry_type = "incident_averted"
    else:
        entry_type = type_map.get(action, "agent_event")
    t = entry.get("t", 0)
    lead_s = float(entry.get("lead_time_seconds") or 0)
    return {
        "id": entry.get("decision_id", f"dec-{t}"),
        "timestamp": _trace_iso(int(t)) if t else _iso_now(),
        "type": entry_type,
        "message": msg,
        "severity": sev,
        "operatorAction": action,
        "outcome": outcome,
        "overrideReason": (alt.get("reason") or "").strip() or None,
        "leadTimeMinutes": max(1, round(lead_s / 60)) if lead_s > 30 else max(1, round(LEAD_TIME_S / 60)),
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
