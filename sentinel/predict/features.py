"""Rolling per-rack trend features (DESIGN §2.4).

Turns the noisy ``RackAggregate.temp_c_mean_active`` series into a smoothed
(level, slope, fit-quality) estimate that the thermal predictor extrapolates.
Two interchangeable estimators behind one interface (select via
``sentinel.predict.config.ESTIMATOR``):

  - ``RackTrend``        — EWMA smoothing + OLS slope over a window. Simple,
    transparent baseline. Kept for A/B comparison.
  - ``KalmanRackTrend``  — constant-velocity Kalman filter tracking [temp, dT/dt]
    with a covariance, so we get a principled slope AND an uncertainty we can
    turn into calibrated confidence. Handles the composition dip (a fresh-GPU
    mean dip) as measurement noise instead of a fake downward slope.

Why this matters (CONTRACTS §3): when a burst lands, the fresh still-cool GPUs
drag ``temp_c_mean_active`` DOWN before it climbs. Smoothing/filtering first
means that dip-then-climb reads as one rising trend, not two spikes.

Pure stdlib + deterministic: same frames in => same features out (CONTRACTS §6).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Deque, Optional, Tuple

from sentinel.predict.config import (
    ESTIMATOR,
    EWMA_ALPHA,
    KALMAN_Q_SLOPE,
    KALMAN_Q_TEMP,
    KALMAN_R_TEMP,
    MIN_TREND_POINTS,
    TICK_TRACE_S,
    TREND_WINDOW_TICKS,
)


def _linfit(points) -> Tuple[float, float]:
    """Least-squares slope (per second) and R^2 for (t_seconds, value) points.

    Returns (0.0, 0.0) when the fit is undefined (< 2 points or no time spread).
    """
    n = len(points)
    if n < 2:
        return 0.0, 0.0
    mean_t = sum(p[0] for p in points) / n
    mean_v = sum(p[1] for p in points) / n
    s_tt = sum((t - mean_t) ** 2 for t, _ in points)
    if s_tt == 0.0:
        return 0.0, 0.0
    s_tv = sum((t - mean_t) * (v - mean_v) for t, v in points)
    slope = s_tv / s_tt
    # R^2 = explained / total variance of v.
    ss_tot = sum((v - mean_v) ** 2 for _, v in points)
    if ss_tot == 0.0:
        return slope, 1.0
    ss_res = sum((v - (mean_v + slope * (t - mean_t))) ** 2 for t, v in points)
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r2


class RackTrend:
    """EWMA level + OLS slope over a rolling window (baseline estimator)."""

    __slots__ = ("rack_id", "throttle_temp", "_level", "_window", "_n")

    def __init__(self, rack_id: str, throttle_temp: float):
        self.rack_id = rack_id
        self.throttle_temp = throttle_temp
        self._level: Optional[float] = None
        self._window: Deque[Tuple[int, float]] = deque(maxlen=TREND_WINDOW_TICKS)
        self._n = 0

    def update(self, t: int, temp_c_mean_active: float) -> None:
        if self._level is None:
            self._level = temp_c_mean_active
        else:
            self._level = EWMA_ALPHA * temp_c_mean_active + (1.0 - EWMA_ALPHA) * self._level
        self._window.append((t, self._level))
        self._n += 1

    @property
    def level(self) -> float:
        return self._level if self._level is not None else 0.0

    @property
    def ready(self) -> bool:
        return len(self._window) >= MIN_TREND_POINTS

    @property
    def slope_c_per_s(self) -> float:
        return _linfit(self._window)[0]

    @property
    def slope_c_per_min(self) -> float:
        return self.slope_c_per_s * 60.0

    @property
    def fit_quality(self) -> float:
        """0..1 confidence in the trend (OLS R^2, discounted until warmed up)."""
        r2 = _linfit(self._window)[1]
        return r2 * min(1.0, self._n / MIN_TREND_POINTS)

    @property
    def headroom_c(self) -> float:
        return self.throttle_temp - self.level


class KalmanRackTrend:
    """Constant-velocity Kalman filter on rack temperature.

    State x = [temp, slope]; we observe temp only. The filter yields a smoothed
    level and slope plus a covariance P; we map the slope variance to a 0..1
    fit-quality so a jittery, uncertain trend produces lower confidence than a
    clean climb — without any window bookkeeping.
    """

    __slots__ = ("rack_id", "throttle_temp", "_x", "_p", "_n", "_dt")

    def __init__(self, rack_id: str, throttle_temp: float, dt: float = float(TICK_TRACE_S)):
        self.rack_id = rack_id
        self.throttle_temp = throttle_temp
        self._dt = dt
        self._x = [0.0, 0.0]                 # [temp, slope_per_s]
        # Covariance P (2x2, row-major): start uncertain on both.
        self._p = [[4.0, 0.0], [0.0, 1e-3]]
        self._n = 0

    def update(self, t: int, temp_c_mean_active: float) -> None:
        z = temp_c_mean_active
        if self._n == 0:
            self._x = [z, 0.0]
            self._n = 1
            return
        dt = self._dt
        x0, x1 = self._x
        p = self._p
        # --- predict: x = F x ; P = F P F^T + Q  (F = [[1,dt],[0,1]]) ---------
        x0p = x0 + dt * x1
        x1p = x1
        p00 = p[0][0] + dt * (p[1][0] + p[0][1]) + dt * dt * p[1][1] + KALMAN_Q_TEMP
        p01 = p[0][1] + dt * p[1][1]
        p10 = p[1][0] + dt * p[1][1]
        p11 = p[1][1] + KALMAN_Q_SLOPE
        # --- update with measurement z (H = [1, 0]) --------------------------
        s = p00 + KALMAN_R_TEMP              # innovation covariance
        k0 = p00 / s                         # Kalman gain
        k1 = p10 / s
        y = z - x0p                          # innovation
        self._x = [x0p + k0 * y, x1p + k1 * y]
        self._p = [
            [(1 - k0) * p00, (1 - k0) * p01],
            [p10 - k1 * p00, p11 - k1 * p01],
        ]
        self._n += 1

    @property
    def level(self) -> float:
        return self._x[0]

    @property
    def ready(self) -> bool:
        return self._n >= MIN_TREND_POINTS

    @property
    def slope_c_per_s(self) -> float:
        return self._x[1]

    @property
    def slope_c_per_min(self) -> float:
        return self._x[1] * 60.0

    @property
    def fit_quality(self) -> float:
        """0..1 confidence in the slope, as its signal-to-noise ratio.

        z = |slope| / std(slope) is a t-like statistic: how many standard
        deviations the estimated slope sits from zero. A slope we are confident
        is non-zero (steady climb) gives high z; a flat/uncertain trend gives
        z ~ 0. `z/(1+z)` squashes it to 0..1. Discounted until warmed up.
        """
        slope_std = math.sqrt(max(self._p[1][1], 1e-12))
        z = abs(self._x[1]) / slope_std
        conf = z / (1.0 + z)
        return conf * min(1.0, self._n / MIN_TREND_POINTS)

    @property
    def headroom_c(self) -> float:
        return self.throttle_temp - self.level


def make_trend(rack_id: str, throttle_temp: float, estimator: str = ESTIMATOR):
    """Factory: build the requested estimator for one rack (defaults to config)."""
    if estimator == "kalman":
        return KalmanRackTrend(rack_id, throttle_temp)
    return RackTrend(rack_id, throttle_temp)
