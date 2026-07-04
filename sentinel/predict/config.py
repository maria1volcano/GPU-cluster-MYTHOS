"""Prediction-layer tunables (M3).

Kept separate from ``sentinel/config.py`` (Omar's FROZEN M0-M2 config) so M3
never edits the foundation. The cross-layer handshake values that already live
there — ``LEAD_TIME_S`` and ``HEAVY_GPU_DEMAND`` — are imported, not re-declared,
so there is a single source of truth.

All knobs are deterministic (no randomness in prediction) so the same frame
sequence always yields the same predictions (CONTRACTS §6).
"""
from __future__ import annotations

from sentinel.config import HEAVY_GPU_DEMAND, LEAD_TIME_S, TEMP_TAU_S, TICK_TRACE_S

# Re-export the handshake constants for a tidy `from sentinel.predict.config import *` feel.
__all__ = [
    "LEAD_TIME_S", "HEAVY_GPU_DEMAND", "TICK_TRACE_S", "TEMP_TAU_S",
    "EWMA_ALPHA", "TREND_WINDOW_TICKS", "MIN_TREND_POINTS", "MIN_SLOPE_C_PER_S",
    "MIN_CONFIDENCE", "THROTTLING_GPUS_IMMINENT",
    "BOTTLENECK_QUEUED_HEAVY_MIN", "BOTTLENECK_RACK_UTIL_MIN",
    "SEVERITY_CRITICAL_ETA_S", "SEVERITY_HIGH_ETA_S",
    "EPISODE_COOLDOWN_TICKS",
    "ETA_METHOD", "ESTIMATOR", "KALMAN_Q_TEMP", "KALMAN_Q_SLOPE", "KALMAN_R_TEMP",
    "TWIN_ENABLED", "TWIN_HORIZON_TICKS", "TWIN_SUSTAINED_TICKS", "TWIN_MIN_PEAK_THROTTLING",
    "DURATION_PRIOR_MIN", "DURATION_PRIOR_DEFAULT_MIN",
    "BOTTLENECK_PRESSURE_HIGH_GPU_MIN", "BOTTLENECK_PRESSURE_CRIT_GPU_MIN",
]

# --- Trend estimator + ETA method (FR-2) -------------------------------------
# How we turn the (noisy) rack temperature series into a level + slope:
#   "ewma"   -> EWMA smoothing + OLS slope over a window (simple baseline)
#   "kalman" -> constant-velocity Kalman filter (level+slope+uncertainty)
ESTIMATOR = "kalman"
# How we turn (level, slope, throttle line) into a time-to-throttle:
#   "ode"    -> invert the RC thermal ODE (exponential crossing; correct)
#   "linear" -> eta = headroom / slope (naive baseline, for A/B comparison)
ETA_METHOD = "ode"

# Kalman noise model (constant-velocity). Q = process noise (how much we let the
# true temp/slope drift per tick), R = measurement noise (composition jitter on
# temp_c_mean_active). Tuned so the filter tracks the ~2 C oscillation without
# chasing every dip. Deterministic given the frame sequence.
KALMAN_Q_TEMP = 0.02
KALMAN_Q_SLOPE = 5e-6
KALMAN_R_TEMP = 0.75

# temp_c_mean_active is composition-noisy (CONTRACTS §3 gotcha: a burst of fresh
# cool GPUs dips the mean before it climbs). EWMA-smooth it BEFORE extrapolating.
# alpha per tick; ~ 2/(N+1) for an N-tick span. 0.3 ≈ 6-tick span (3 sim-min).
EWMA_ALPHA = 0.3
# Rolling window (in ticks) for the robust temperature slope. 20 ticks = 10
# sim-min at TICK_TRACE_S=30 — long enough to see through the oscillation.
TREND_WINDOW_TICKS = 20
MIN_TREND_POINTS = 5                # need this many points before trusting a slope
MIN_SLOPE_C_PER_S = 1e-4            # below this the trend is flat/cooling -> no eta

# Fire only above this confidence (fit quality x corroboration). Keeps the
# oscillating-at-the-line noise from spamming low-quality alerts.
MIN_CONFIDENCE = 0.55
# When at least this many GPUs on the rack are already throttling, the throttle
# is not "predicted" — it is happening. Fire eta=0 / critical regardless of slope.
THROTTLING_GPUS_IMMINENT = 1

# --- Duration-weighted queue pressure (FR-3) ---------------------------------
# Job runtimes vary by CLASS by orders of magnitude (measured on the trace:
# whole-GPU BE median ~3 min vs whole-GPU Burstable ~61 min), so counting heavy
# jobs treats a 3-min burst like a 1-hour training run. We weight each queued
# heavy job by gpu_demand x expected-runtime, keyed on fields present in the
# FROZEN QueuedPodInfo (whole-vs-fractional from gpu_milli, and qos). This is a
# learned prior (median minutes per class), NOT the oracle deletion time.
# Recompute with: python3 -m tools.eval_prediction (duration-by-class section).
DURATION_PRIOR_MIN = {
    ("whole", "LS"): 14.0,
    ("whole", "BE"): 3.2,
    ("whole", "Burstable"): 61.0,
    ("whole", "Guaranteed"): 47.0,
    ("frac", "LS"): 9.6,
    ("frac", "BE"): 5.0,
}
DURATION_PRIOR_DEFAULT_MIN = 10.0
# Severity bands on weighted pressure = sum(gpu_demand x expected_min) of heavy
# jobs queued for a rack, in GPU-equivalent-minutes.
BOTTLENECK_PRESSURE_HIGH_GPU_MIN = 100.0
BOTTLENECK_PRESSURE_CRIT_GPU_MIN = 300.0

# --- Scheduling-bottleneck predictor (FR-3) ----------------------------------
# "3 heavy jobs queued on Rack 7" (PRD §5). Uses the frame's queued_heavy, which
# counts pending pods whose bin-packing placement preview targets this rack.
BOTTLENECK_QUEUED_HEAVY_MIN = 3
# Rack must also be meaningfully occupied for a queue to be a *bottleneck* (vs.
# an empty rack that can just absorb the jobs). Low because bin-packing keeps
# util = demand/capacity modest even on the hot rack (dense on a few nodes).
BOTTLENECK_RACK_UTIL_MIN = 0.05

# --- Severity banding (by eta) -----------------------------------------------
SEVERITY_CRITICAL_ETA_S = 0.0       # eta <= this -> critical (already/at throttle)
SEVERITY_HIGH_ETA_S = LEAD_TIME_S / 2.0   # eta < this -> high, else medium

# --- Digital-twin lookahead (FR-2, forward simulation) -----------------------
# Fork the live engine and roll it forward to turn "the rack is hot now" into
# "the rack will SUSTAIN throttle for the next N min". Compute is ~0.1 ms/tick,
# so a short rollout per prediction is affordable.
TWIN_ENABLED = True
TWIN_HORIZON_TICKS = 20        # look 20 ticks (~10 sim-min) ahead
TWIN_SUSTAINED_TICKS = 4       # "sustained" = throttling this many consecutive ticks
TWIN_MIN_PEAK_THROTTLING = 5   # only treat as an incident if projected peak >= this

# --- Episode debounce --------------------------------------------------------
# One incident = one episode. While a (type, target) keeps firing, update the
# same prediction_id + eta instead of emitting a new alert every tick. Close the
# episode after this many consecutive quiet ticks so a later recurrence is new.
EPISODE_COOLDOWN_TICKS = 3
