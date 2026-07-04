"""Physically-grounded thermal math (M3 upgrade).

The telemetry model heats each GPU as a first-order RC system (Newton's law of
cooling), exactly as `sentinel/telemetry/sim.py` implements it:

    temp += (target - temp) * (1 - exp(-dt / tau))        # tau = TEMP_TAU_S

so the continuous trajectory toward a fixed target is exponential:

    T(t) = target + (T0 - target) * exp(-t / tau).

A naive *linear* extrapolation (`eta = headroom / slope`) is systematically
wrong for this system: it ignores the asymptote, so it under-estimates the time
near saturation and — worse — it will always predict an eventual crossing even
when the rack is heading toward a target *below* its throttle line (it never
actually throttles). Inverting the real ODE fixes both.

Two ways to get the asymptote `target`:
  - measured: back it out from the smoothed slope, `target = T + tau * dT/dt`
    (rearranged from the ODE). No extra inputs — just the trend we already fit.
  - modeled: compute it from util + coupling (the sim's own formula) when we
    have per-GPU load; used by the digital twin.
"""
from __future__ import annotations

import math
from typing import Optional

from sentinel.config import TEMP_TAU_S

# Guard: an asymptote this-close to (or below) the line means "won't throttle".
_TARGET_MARGIN_C = 0.05


def infer_target_temp(current_temp: float, slope_c_per_s: float, tau: float = TEMP_TAU_S) -> float:
    """Back the RC asymptote out of the measured slope: target = T + tau*(dT/dt).

    Derivation: dT/dt = (target - T) / tau  =>  target = T + tau * dT/dt.
    """
    return current_temp + tau * slope_c_per_s


def time_to_threshold(
    current_temp: float,
    target_temp: float,
    threshold_temp: float,
    tau: float = TEMP_TAU_S,
) -> Optional[float]:
    """Seconds until the exponential trajectory T(t) = target + (T0-target)e^{-t/tau}
    crosses `threshold_temp`. Returns:
      - 0.0        if already at/above the threshold,
      - None       if the trajectory asymptotes at/below the threshold
                   (it never throttles — a case linear extrapolation cannot express),
      - t > 0      the analytic crossing time otherwise.
    """
    if current_temp >= threshold_temp:
        return 0.0
    if target_temp <= threshold_temp + _TARGET_MARGIN_C:
        return None  # heads toward a sub-throttle steady state; no crossing
    # t = tau * ln[(target - T0) / (target - threshold)]
    return tau * math.log((target_temp - current_temp) / (target_temp - threshold_temp))


def eta_from_trend(
    current_temp: float,
    slope_c_per_s: float,
    threshold_temp: float,
    tau: float = TEMP_TAU_S,
):
    """Convenience: (eta_seconds, inferred_target). `eta_seconds` follows
    `time_to_threshold` semantics (0.0 already over, None never, else > 0).

    A non-positive slope while below the line -> not heating -> (None, target).
    """
    if current_temp >= threshold_temp:
        return 0.0, infer_target_temp(current_temp, slope_c_per_s, tau)
    if slope_c_per_s <= 0.0:
        return None, infer_target_temp(current_temp, slope_c_per_s, tau)
    target = infer_target_temp(current_temp, slope_c_per_s, tau)
    return time_to_threshold(current_temp, target, threshold_temp, tau), target
