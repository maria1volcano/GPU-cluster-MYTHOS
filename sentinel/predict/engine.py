"""PredictionEngine (M3) — the public surface of the prediction layer.

Consumes the FROZEN telemetry frame (CONTRACTS.md) in its **dict** form and
emits typed ``Prediction`` objects. One code path serves:

  - live replay      ``PredictionEngine.attach(Engine(), window)``
  - recorded fixtures ``for f in jsonl: engine.on_frame(f)``
  - websocket         ``on_frame(msg)`` for each "telemetry" frame

Episode debounce: while a ``(type, target)`` incident keeps firing it keeps ONE
``prediction_id`` and just updates its ``eta``, instead of a fresh alert every
tick. After ``EPISODE_COOLDOWN_TICKS`` quiet ticks the episode closes, so a
later recurrence is correctly a new incident.
"""
from __future__ import annotations

import itertools
from typing import Dict, Iterator, List, Optional, Tuple

from sentinel.predict.config import (
    ESTIMATOR,
    ETA_METHOD,
    EPISODE_COOLDOWN_TICKS,
    TWIN_ENABLED,
    TWIN_MIN_PEAK_THROTTLING,
)
from sentinel.predict.features import make_trend
from sentinel.predict.hotspot import rank_racks
from sentinel.predict.predictors import (
    NodeInstabilityPredictor,
    SchedulingBottleneckPredictor,
    ThermalThrottlePredictor,
)
from sentinel.predict.schema import THERMAL_THROTTLE, Evidence, Prediction, prediction_frame
from sentinel.predict.twin import DigitalTwin
from sentinel.telemetry.profiles import PROFILES

# Fallback throttle line for a rack whose model is unknown/missing a profile.
_DEFAULT_THROTTLE_TEMP = 85.0


class _Episode:
    __slots__ = ("prediction_id", "quiet_ticks")

    def __init__(self, prediction_id: str):
        self.prediction_id = prediction_id
        self.quiet_ticks = 0


class PredictionEngine:
    def __init__(self, enable_instability: bool = True, engine=None,
                 estimator: str = ESTIMATOR, eta_method: str = ETA_METHOD):
        self.estimator = estimator
        self.thermal = ThermalThrottlePredictor(eta_method=eta_method)
        self.bottleneck = SchedulingBottleneckPredictor()
        self.instability = NodeInstabilityPredictor() if enable_instability else None
        self._trends: Dict = {}
        self._episodes: Dict[Tuple[str, str], _Episode] = {}
        self._id_counter = itertools.count(1)
        self.last_tick: int = -1
        # Optional live engine enables the digital-twin lookahead (forward
        # simulation). Absent (fixtures / WS replay), predictions are the pure
        # analytic path — same interface, just no forward projection.
        self._engine = engine
        self._twin: Optional[DigitalTwin] = DigitalTwin(engine) if (engine is not None and TWIN_ENABLED) else None

    # --- helpers ---------------------------------------------------------
    def _throttle_temp(self, gpu_model: Optional[str]) -> float:
        prof = PROFILES.get(gpu_model) if gpu_model else None
        return prof.throttle_temp if prof else _DEFAULT_THROTTLE_TEMP

    def _trend_for(self, rack: Dict):
        rack_id = rack["rack_id"]
        trend = self._trends.get(rack_id)
        if trend is None:
            trend = make_trend(rack_id, self._throttle_temp(rack.get("gpu_model")), self.estimator)
            self._trends[rack_id] = trend
        return trend

    def _assign_id(self, pred: Prediction) -> Prediction:
        """Attach a stable id via episode tracking, mutating pred in place."""
        key = (pred.type, pred.target["id"])
        ep = self._episodes.get(key)
        if ep is None:
            ep = _Episode(f"pred-{next(self._id_counter):04d}")
            self._episodes[key] = ep
        ep.quiet_ticks = 0
        pred.prediction_id = ep.prediction_id
        return pred

    def _age_episodes(self, fired_keys: set) -> None:
        """Advance cooldown on episodes that did NOT fire this tick; close the
        stale ones so a later recurrence gets a fresh id."""
        for key in list(self._episodes.keys()):
            if key in fired_keys:
                continue
            ep = self._episodes[key]
            ep.quiet_ticks += 1
            if ep.quiet_ticks >= EPISODE_COOLDOWN_TICKS:
                del self._episodes[key]

    # --- main entry ------------------------------------------------------
    def on_frame(self, frame: Dict) -> List[Prediction]:
        """Ingest one telemetry frame (dict); return this tick's predictions.

        Accepts a ``TelemetryFrame`` object too (anything with ``to_dict``),
        for convenience when wiring straight to the live Engine.
        """
        if hasattr(frame, "to_dict"):
            frame = frame.to_dict()
        t = frame["t"]
        self.last_tick = frame.get("tick", self.last_tick + 1)

        # Group the pending queue by the rack the placement policy targets, so
        # the bottleneck predictor can weight pressure by each pod's class.
        queue_by_rack: Dict[str, List[Dict]] = {}
        for q in frame.get("queue", ()):
            tr = q.get("target_rack")
            if tr is not None:
                queue_by_rack.setdefault(tr, []).append(q)

        predictions: List[Prediction] = []
        for rack in frame.get("racks", ()):  # racks is always complete (CONTRACTS §1)
            trend = self._trend_for(rack)
            trend.update(t, rack.get("temp_c_mean_active", 0.0))
            thermal = self.thermal.predict(t, rack, trend)
            if thermal is not None:
                predictions.append(thermal)
            bottleneck = self.bottleneck.predict(t, rack, trend, queue_by_rack.get(rack["rack_id"]))
            if bottleneck is not None:
                predictions.append(bottleneck)

        if self.instability is not None:
            predictions.extend(self.instability.predict_from_samples(t, list(frame.get("samples", ()))))

        self._enrich_with_twin(predictions)

        fired_keys = set()
        for pred in predictions:
            self._assign_id(pred)
            fired_keys.add((pred.type, pred.target["id"]))
        self._age_episodes(fired_keys)
        return predictions

    def _enrich_with_twin(self, predictions: List[Prediction]) -> None:
        """Attach a digital-twin forward projection to each thermal prediction:
        how bad it gets (projected peak throttling) and for how long, from
        rolling the forked engine ahead. No-op without a live engine."""
        if self._twin is None:
            return
        for p in predictions:
            if p.type != THERMAL_THROTTLE:
                continue
            proj = self._twin.project(p.target["id"])
            p.evidence.append(Evidence(metric="projected_peak_throttling_gpus",
                                       value=float(proj.peak_throttling)))
            p.evidence.append(Evidence(metric="projected_peak_temp_c", value=proj.peak_temp_mean))
            p.evidence.append(Evidence(metric="projected_horizon_min",
                                       value=round(proj.horizon_s / 60.0, 1)))
            # A large sustained projected peak upgrades a merely-"medium" alert.
            if proj.peak_throttling >= TWIN_MIN_PEAK_THROTTLING and p.severity == "medium":
                p.severity = "high"

    def counterfactual(self, rack_id: str, job_id: str, to_rack: str):
        """Quantify a candidate migration via the twin (for M4's recommender /
        M6 outcome resolution). Returns (baseline, action, applied) or None if
        no live engine is attached."""
        if self._twin is None:
            return None
        return self._twin.counterfactual(rack_id, job_id, to_rack)

    def score_action(self, rack_id: str, job_id: str, to_rack: str) -> Optional[Dict]:
        """M4-facing summary of a candidate migration's projected effect: how
        many peak/steady throttling GPUs it averts on `rack_id` over the twin
        horizon. Drop-in for `Recommendation.expected_effect`. Returns None
        without a live engine; ``applied=False`` when the action is invalid
        (the guardrail seam)."""
        res = self.counterfactual(rack_id, job_id, to_rack)
        if res is None:
            return None
        base, acted, applied = res
        if not applied or acted is None:
            return {"applied": False, "job_id": job_id, "to_rack": to_rack}
        return {
            "applied": True,
            "job_id": job_id,
            "from_rack": rack_id,
            "to_rack": to_rack,
            "horizon_min": round(base.horizon_s / 60.0, 1),
            "baseline_peak_throttling": base.peak_throttling,
            "action_peak_throttling": acted.peak_throttling,
            "averted_peak_throttling_gpus": base.peak_throttling - acted.peak_throttling,
            "baseline_end_throttling": base.end_throttling,
            "action_end_throttling": acted.end_throttling,
            "averted_end_throttling_gpus": base.end_throttling - acted.end_throttling,
        }

    def on_frame_as_frame(self, frame: Dict) -> Dict:
        """``on_frame`` but returns a WS-ready "prediction" stream frame."""
        preds = self.on_frame(frame)
        return prediction_frame(frame["t"], frame.get("tick", self.last_tick), preds)

    def risk_board(self, frame: Dict) -> Dict[str, List[Dict]]:
        """Cluster-wide FR-2 view: racks ranked by throttle risk (``hotspots``)
        and cool racks with capacity ranked as migration ``targets``. Uses the
        trends already built by ``on_frame`` (call it first for slope-aware
        risk). For the dashboard/recommender, not the Prediction stream."""
        if hasattr(frame, "to_dict"):
            frame = frame.to_dict()
        return rank_racks(frame, self._trends.get,
                          lambda m: self._throttle_temp(m))

    # --- live wiring -----------------------------------------------------
    def attach(self, engine, window=None) -> Iterator[Tuple[Dict, List[Prediction]]]:
        """Drive M0-M2's ``Engine`` over ``window`` and yield
        ``(telemetry_frame_dict, predictions)`` per tick. Enables the digital
        twin (forward simulation forks this same engine at each tick).

        Example:
            from sentinel.engine import Engine
            for frame, preds in PredictionEngine().attach(Engine()):
                ...
        """
        from sentinel.config import DEMO_WINDOW
        self._engine = engine
        if TWIN_ENABLED and self._twin is None:
            self._twin = DigitalTwin(engine)
        run = engine.run(window if window is not None else DEMO_WINDOW)
        for frame in run:
            fd = frame.to_dict()
            yield fd, self.on_frame(fd)
