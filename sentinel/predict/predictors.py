"""The three typed predictors (DESIGN §2.4). Each returns a ``Prediction`` with
``prediction_id=""`` — the ``PredictionEngine`` assigns/reuses the id via episode
debounce so a persisting incident keeps one id and an updating eta.

Design notes grounded in the REAL demo-window data (rack-00, a dense G2 rack):
  - The hot rack oscillates right at its 84 °C throttle line with tens of GPUs
    already throttling from window open — ``temp_c_max`` is pinned above the
    line the whole time. So a naive "time to cross the line" is near-zero and
    noisy. We therefore treat an already-throttling rack as an *active* throttle
    (eta = 0, critical) and reserve the extrapolated eta for the genuine
    approach-from-below case. The real minutes-of-lead-time comes from the
    bottleneck predictor seeing heavy jobs queue onto the rack BEFORE they land
    and deepen the throttle (CONTRACTS §3: judge by throttling_gpus + queue).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sentinel.predict import physics
from sentinel.predict.config import (
    BOTTLENECK_PRESSURE_CRIT_GPU_MIN,
    BOTTLENECK_PRESSURE_HIGH_GPU_MIN,
    BOTTLENECK_QUEUED_HEAVY_MIN,
    BOTTLENECK_RACK_UTIL_MIN,
    DURATION_PRIOR_DEFAULT_MIN,
    DURATION_PRIOR_MIN,
    ETA_METHOD,
    MIN_CONFIDENCE,
    MIN_SLOPE_C_PER_S,
    SEVERITY_CRITICAL_ETA_S,
    SEVERITY_HIGH_ETA_S,
    THROTTLING_GPUS_IMMINENT,
    effective_bottleneck_queued_heavy_min,
    effective_bottleneck_rack_util_min,
    effective_lead_time_s,
)
from sentinel.predict.schema import (
    NODE_INSTABILITY,
    SCHEDULING_BOTTLENECK,
    THERMAL_THROTTLE,
    Evidence,
    Prediction,
)
from sentinel.telemetry.profiles import PROFILES


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _eta_severity(eta_seconds: float) -> str:
    if eta_seconds <= SEVERITY_CRITICAL_ETA_S:
        return "critical"
    if eta_seconds < SEVERITY_HIGH_ETA_S:
        return "high"
    return "medium"


def _expected_runtime_min(pod: Dict) -> float:
    """Learned duration prior for a queued pod, keyed on frozen-contract fields
    (whole-vs-fractional GPU + qos). Used to weight queue pressure by how long a
    job will likely occupy the rack — no oracle on the pod's real deletion time."""
    cls = ("whole" if pod.get("gpu_milli") == 1000 else "frac", pod.get("qos", ""))
    return DURATION_PRIOR_MIN.get(cls, DURATION_PRIOR_DEFAULT_MIN)


def _weighted_queue_pressure(queued_pods: List[Dict]) -> float:
    """Sum of gpu_demand x expected-runtime over heavy queued pods, in
    GPU-equivalent-minutes: the expected heavy GPU-work about to land here."""
    return sum(p.get("gpu_demand", 0.0) * _expected_runtime_min(p)
               for p in queued_pods if p.get("heavy"))


class ThermalThrottlePredictor:
    """FR-2. Extrapolate the EWMA temperature trend to the rack's throttle line;
    fire when the smoothed mean is at/over the line (active throttle) or is
    projected to reach it within ``LEAD_TIME_S``."""

    type = THERMAL_THROTTLE

    def __init__(self, eta_method: str = ETA_METHOD):
        self.eta_method = eta_method

    def predict(self, t: int, rack: Dict, trend) -> Optional[Prediction]:
        active_gpus = rack.get("active_gpus", 0)
        if not rack.get("gpu_model") or active_gpus <= 0:
            return None  # empty / CPU-only rack — nothing thermal to trend

        throttling = rack.get("throttling_gpus", 0)
        headroom = trend.headroom_c
        slope_s = trend.slope_c_per_s
        target_temp = None

        # eta: active throttle or at/over the smoothed line -> imminent (0);
        # else project from the trend; else (flat/cooling, below line) skip.
        if throttling >= THROTTLING_GPUS_IMMINENT or headroom <= 0.0:
            eta = 0.0
            target_temp = physics.infer_target_temp(trend.level, slope_s)
        elif self.eta_method == "ode":
            # Invert the RC thermal ODE: exponential crossing of the line, using
            # the asymptote implied by the current slope. Correctly returns
            # "never" (None) when the rack heads to a sub-throttle steady state.
            eta, target_temp = physics.eta_from_trend(trend.level, slope_s, trend.throttle_temp)
            if eta is None:
                return None
        elif slope_s > MIN_SLOPE_C_PER_S:
            eta = headroom / slope_s          # naive linear baseline (A/B)
        else:
            return None

        if eta >= effective_lead_time_s():
            return None  # far enough out that it is not actionable yet

        # Confidence: trend fit quality + corroboration by observed throttling.
        # An observed active throttle is a fact, so floor confidence higher.
        throttle_frac = throttling / active_gpus if active_gpus else 0.0
        confidence = _clamp(0.45 + 0.30 * trend.fit_quality + 0.25 * throttle_frac, 0.0, 0.97)
        if eta <= 0.0 and throttling >= THROTTLING_GPUS_IMMINENT:
            confidence = max(confidence, 0.70)
        if not trend.ready:
            confidence *= 0.8
        confidence = round(confidence, 2)
        if confidence < MIN_CONFIDENCE:
            return None

        evidence = [
            Evidence(metric="rack_temp_c", slope_per_min=round(trend.slope_c_per_min, 2),
                     threshold=trend.throttle_temp, current=round(trend.level, 1)),
            Evidence(metric="thermal_headroom_c", value=round(headroom, 1)),
            Evidence(metric="throttling_gpus", value=float(throttling)),
            Evidence(metric="rack_util", value=round(rack.get("util", 0.0), 3)),
        ]
        # Inbound queue pressure belongs on the thermal card too (DESIGN §3.4's
        # THERMAL_THROTTLE example carries queued_heavy_jobs as evidence).
        if rack.get("queued_heavy", 0) > 0:
            evidence.append(Evidence(metric="queued_heavy_jobs",
                                     value=float(rack["queued_heavy"])))
        if target_temp is not None:
            evidence.append(Evidence(metric="projected_steady_temp_c", value=round(target_temp, 1)))
        return Prediction(
            prediction_id="",
            type=self.type,
            target={"kind": "rack", "id": rack["rack_id"]},
            eta_seconds=round(eta, 1),
            severity=_eta_severity(eta),
            confidence=confidence,
            evidence=evidence,
            t=t,
        )


class SchedulingBottleneckPredictor:
    """FR-3. Heavy jobs converging on a hot/occupied rack: rising queue depth of
    GPU-heavy pods whose placement preview targets this rack. Present-tense
    congestion (eta = 0) that, left alone, deepens the thermal throttle — this
    is the demo's lead-time signal ("N heavy jobs queued on Rack X")."""

    type = SCHEDULING_BOTTLENECK

    def __init__(self) -> None:
        # Hysteresis latch per rack: fire at >= threshold, hold an ongoing
        # incident while >= threshold-1, release below that. Stops the queue
        # oscillating 3<->2 around the threshold from splitting one incident
        # into a stream of new alert cards (deterministic state machine).
        self._latched: set = set()

    def predict(self, t: int, rack: Dict, trend, queued_pods: Optional[List[Dict]] = None) -> Optional[Prediction]:
        queued_heavy = rack.get("queued_heavy", 0)
        util = rack.get("util", 0.0)
        rack_id = rack.get("rack_id", "")
        fire_min = effective_bottleneck_queued_heavy_min()
        hold_min = max(1, fire_min - 1)
        firing = queued_heavy >= fire_min or (
            rack_id in self._latched and queued_heavy >= hold_min
        )
        if not firing or util < effective_bottleneck_rack_util_min():
            self._latched.discard(rack_id)
            return None
        self._latched.add(rack_id)

        throttling = rack.get("throttling_gpus", 0)
        # Duration/load-weighted pressure: how much heavy GPU-work (GPU-eq x
        # expected minutes) is queued for this rack, not just the raw count.
        # Falls back gracefully to count-based severity if the queue isn't given.
        pressure = _weighted_queue_pressure(queued_pods) if queued_pods else 0.0

        if pressure >= BOTTLENECK_PRESSURE_CRIT_GPU_MIN:
            severity = "critical"
        elif pressure >= BOTTLENECK_PRESSURE_HIGH_GPU_MIN or queued_heavy >= BOTTLENECK_QUEUED_HEAVY_MIN + 2 or throttling > 0:
            severity = "high"
        else:
            severity = "medium"
        confidence = round(_clamp(0.60 + 0.05 * queued_heavy, 0.0, 0.95), 2)

        evidence = [
            Evidence(metric="queued_heavy_jobs", value=float(queued_heavy),
                     threshold=float(effective_bottleneck_queued_heavy_min())),
            Evidence(metric="rack_util", value=round(util, 3),
                     threshold=effective_bottleneck_rack_util_min()),
            Evidence(metric="queued_pods", value=float(rack.get("queued_pods", queued_heavy))),
            Evidence(metric="throttling_gpus", value=float(throttling)),
        ]
        if queued_pods:
            evidence.append(Evidence(metric="queued_heavy_gpu_minutes", value=round(pressure, 1),
                                     threshold=BOTTLENECK_PRESSURE_HIGH_GPU_MIN))
        return Prediction(
            prediction_id="",
            type=self.type,
            target={"kind": "rack", "id": rack["rack_id"]},
            eta_seconds=0.0,
            severity=severity,
            confidence=confidence,
            evidence=evidence,
            t=t,
        )


class NodeInstabilityPredictor:
    """FR-4 (P1). Score a node from XID/ECC events and from sustained hardware
    thermal stress (HW_THERMAL + clock derating on multiple GPUs). The latter
    surfaces believable instability during the demo's hot-rack stress window
    when rare Poisson XID draws haven't fired yet."""

    type = NODE_INSTABILITY

    XID_WEIGHT = 1.0
    ECC_WEIGHT = 0.25
    FIRE_SCORE = 1.0
    HW_THERMAL_THRESHOLD = 2
    CLOCK_STRESS_THRESHOLD = 3
    CLOCK_DERATE_FRAC = 0.90

    def predict_from_samples(self, t: int, samples: List[Dict]) -> List[Prediction]:
        by_node: Dict[str, Dict[str, float]] = {}
        for s in samples:
            xid = s.get("xid_errors", 0) or 0
            ecc = (s.get("ecc_errors") or {}).get("volatile", 0) or 0
            reasons = tuple(s.get("throttle_reasons") or ())
            model = s.get("model")
            prof = PROFILES.get(model) if model else None
            sm = s.get("sm_clock_mhz") or 0
            hw = 1 if "HW_THERMAL" in reasons else 0
            clock_stress = (
                1
                if prof
                and sm
                and sm < prof.base_sm_mhz * self.CLOCK_DERATE_FRAC
                and reasons
                else 0
            )
            if xid == 0 and ecc == 0 and hw == 0 and clock_stress == 0:
                continue
            agg = by_node.setdefault(
                s["node_sn"],
                {
                    "xid": 0.0,
                    "ecc": 0.0,
                    "hw_thermal": 0.0,
                    "clock_stress": 0.0,
                    "rack_id": s.get("rack_id"),
                },
            )
            agg["xid"] += xid
            agg["ecc"] += ecc
            agg["hw_thermal"] += hw
            agg["clock_stress"] += clock_stress

        out: List[Prediction] = []
        for node_sn, agg in sorted(by_node.items()):
            score = self.XID_WEIGHT * agg["xid"] + self.ECC_WEIGHT * agg["ecc"]
            if agg["hw_thermal"] >= self.HW_THERMAL_THRESHOLD:
                score += 1.5
            if agg["clock_stress"] >= self.CLOCK_STRESS_THRESHOLD:
                score += 1.0
            if score < self.FIRE_SCORE:
                continue
            severity = (
                "critical"
                if agg["xid"] > 0 or agg["hw_thermal"] >= 3
                else "high"
                if agg["hw_thermal"] >= self.HW_THERMAL_THRESHOLD
                else "medium"
            )
            confidence = round(_clamp(0.5 + 0.1 * score, 0.0, 0.9), 2)
            evidence: List[Evidence] = []
            if agg["xid"]:
                evidence.append(Evidence(metric="xid_errors", value=agg["xid"]))
            if agg["ecc"]:
                evidence.append(Evidence(metric="ecc_errors_volatile", value=agg["ecc"]))
            if agg["hw_thermal"]:
                evidence.append(
                    Evidence(metric="hw_thermal_gpus", value=agg["hw_thermal"])
                )
            if agg["clock_stress"]:
                evidence.append(
                    Evidence(metric="clock_derated_gpus", value=agg["clock_stress"])
                )
            target: Dict[str, str] = {"kind": "node", "id": node_sn}
            if agg.get("rack_id"):
                target["rack_id"] = str(agg["rack_id"])
            out.append(
                Prediction(
                    prediction_id="",
                    type=self.type,
                    target=target,
                    eta_seconds=0.0,
                    severity=severity,
                    confidence=confidence,
                    evidence=evidence,
                    t=t,
                )
            )
        return out
