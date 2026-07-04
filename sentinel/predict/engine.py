"""Prediction layer (M3) — DESIGN.md §2.4.

Explainable-first: linear trend extrapolation + fixed thresholds, no
black-box ML (PRD NG4). Every fired `Prediction` carries the numeric
`evidence` that justified it (FR-11) — trend slope, threshold, and current
value — so the agent/dashboard never has to re-derive "why" from scratch.
"""
from __future__ import annotations

import itertools
from collections import defaultdict, deque
from statistics import mean
from typing import Deque, Dict, List, Optional, Tuple

from sentinel.config import Thresholds
from sentinel.models import Evidence, GpuTelemetrySample, Prediction, Rack
from sentinel.telemetry import DEFAULT_PROFILE, MODEL_PROFILES

_id_counter = itertools.count(1)


def _next_prediction_id() -> str:
    return f"pred-{next(_id_counter):04d}"


def _linear_slope_per_sec(points: List[Tuple[int, float]]) -> float:
    """Least-squares slope of value over time for a short rolling window."""
    n = len(points)
    if n < 2:
        return 0.0
    mean_t = mean(p[0] for p in points)
    mean_v = mean(p[1] for p in points)
    num = sum((t - mean_t) * (v - mean_v) for t, v in points)
    den = sum((t - mean_t) ** 2 for t, _ in points)
    return num / den if den else 0.0


class PredictionEngine:
    """Consumes telemetry frames + queue state, emits typed `Prediction`s."""

    def __init__(self, racks: Dict[str, Rack], thresholds: Thresholds, window_size: int = 6):
        self.racks = racks
        self.thresholds = thresholds
        self.window_size = window_size
        self._temp_history: Dict[str, Deque[Tuple[int, float]]] = defaultdict(lambda: deque(maxlen=window_size))
        self._util_history: Dict[str, Deque[Tuple[int, float]]] = defaultdict(lambda: deque(maxlen=window_size))

    def _rack_profile(self, rack_id: str) -> dict:
        model = self.racks[rack_id].gpu_model
        return MODEL_PROFILES.get(model, DEFAULT_PROFILE) if model else DEFAULT_PROFILE

    def ingest_tick(
        self,
        t: int,
        telemetry_samples: List[GpuTelemetrySample],
        pending_heavy_count: int,
    ) -> List[Prediction]:
        rack_temps: Dict[str, List[float]] = defaultdict(list)
        rack_utils: Dict[str, List[float]] = defaultdict(list)
        for s in telemetry_samples:
            rack_temps[s.rack_id].append(s.temp_c)
            rack_utils[s.rack_id].append(s.util)

        predictions: List[Prediction] = []
        for rack_id, temps in rack_temps.items():
            avg_temp = mean(temps)
            avg_util = mean(rack_utils[rack_id])
            self._temp_history[rack_id].append((t, avg_temp))
            self._util_history[rack_id].append((t, avg_util))

            thermal = self._thermal_throttle_prediction(rack_id, t, avg_temp)
            if thermal:
                predictions.append(thermal)

            bottleneck = self._scheduling_bottleneck_prediction(rack_id, t, avg_util, pending_heavy_count)
            if bottleneck:
                predictions.append(bottleneck)

        return predictions

    def latest_rack_temp(self, rack_id: str) -> Optional[float]:
        hist = self._temp_history.get(rack_id)
        return hist[-1][1] if hist else None

    def latest_rack_util(self, rack_id: str) -> Optional[float]:
        hist = self._util_history.get(rack_id)
        return hist[-1][1] if hist else None

    def _thermal_throttle_prediction(self, rack_id: str, t: int, avg_temp: float) -> Optional[Prediction]:
        profile = self._rack_profile(rack_id)
        throttle_temp = profile["throttle_temp"]
        headroom = throttle_temp - avg_temp

        history = list(self._temp_history[rack_id])
        slope_per_sec = _linear_slope_per_sec(history)
        slope_per_min = slope_per_sec * 60.0

        if headroom <= 0:
            eta_seconds = 0.0
        elif slope_per_sec > 1e-6:
            eta_seconds = headroom / slope_per_sec
        else:
            return None  # flat/cooling trend — nothing to predict

        lead_time = self.thresholds.thermal_throttle.lead_time_seconds
        if eta_seconds >= lead_time:
            return None

        fit_quality = min(1.0, len(history) / self.window_size)
        confidence = round(min(0.97, 0.55 + 0.35 * fit_quality), 2)
        severity = "critical" if eta_seconds <= 0 else ("high" if eta_seconds < lead_time / 2 else "medium")

        return Prediction(
            prediction_id=_next_prediction_id(),
            type="THERMAL_THROTTLE",
            target={"kind": "rack", "id": rack_id},
            eta_seconds=round(eta_seconds, 1),
            severity=severity,
            confidence=confidence,
            evidence=[
                Evidence(metric="rack_temp_c", slope_per_min=round(slope_per_min, 2), threshold=throttle_temp, current=round(avg_temp, 1)),
                Evidence(metric="thermal_headroom_c", value=round(headroom, 1)),
            ],
            t=t,
        )

    def _scheduling_bottleneck_prediction(
        self, rack_id: str, t: int, avg_util: float, pending_heavy_count: int
    ) -> Optional[Prediction]:
        th = self.thresholds.scheduling_bottleneck
        if avg_util < th.util_threshold or pending_heavy_count < th.queued_heavy_threshold:
            return None

        util_history = list(self._util_history[rack_id])
        slope_per_min = _linear_slope_per_sec(util_history) * 60.0
        fit_quality = min(1.0, len(util_history) / self.window_size)
        confidence = round(min(0.95, 0.6 + 0.3 * fit_quality), 2)
        severity = "high" if pending_heavy_count >= th.queued_heavy_threshold + 2 else "medium"

        return Prediction(
            prediction_id=_next_prediction_id(),
            type="SCHEDULING_BOTTLENECK",
            target={"kind": "rack", "id": rack_id},
            eta_seconds=0.0,  # already converging — this is a present-tense congestion signal
            severity=severity,
            confidence=confidence,
            evidence=[
                Evidence(metric="rack_util", value=round(avg_util, 3), threshold=th.util_threshold, slope_per_min=round(slope_per_min, 4)),
                Evidence(metric="queued_heavy_jobs", value=pending_heavy_count, threshold=th.queued_heavy_threshold),
            ],
            t=t,
        )
