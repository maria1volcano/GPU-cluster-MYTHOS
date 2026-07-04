"""Multi-rack risk ranking (FR-2, cluster-wide view).

The per-rack thermal predictor answers "is THIS rack in trouble?". The risk
board answers the operator's next two questions:

  - "which rack is the hotspot / heats next?"  -> `hotspots`, ranked by a 0..1
    risk blending observed throttling, weighted incoming queue pressure, and the
    (Kalman) temperature slope, so a rack climbing toward the line ranks above a
    flat one even before it crosses.
  - "where should I move work TO?"             -> `targets`, GPU racks with spare
    capacity ranked coolest-first (low util, low temp) — exactly the candidate
    set M4's recommender scores and the demo migrates onto (the cool T4 racks).

Pure function over the frame + current trends; no state of its own.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from sentinel.predict.config import BOTTLENECK_PRESSURE_CRIT_GPU_MIN
from sentinel.predict.predictors import _clamp, _weighted_queue_pressure

# A ~1 C/min sustained climb is already a strong thermal trend; normalize to it.
_SLOPE_NORM_C_PER_MIN = 1.0


def rank_racks(frame: Dict,
               trend_of: Callable[[str], object],
               throttle_temp_of: Callable[[Optional[str]], float]) -> Dict[str, List[Dict]]:
    queue_by_rack: Dict[str, List[Dict]] = {}
    for q in frame.get("queue", ()):
        tr = q.get("target_rack")
        if tr is not None:
            queue_by_rack.setdefault(tr, []).append(q)

    hotspots: List[Dict] = []
    targets: List[Dict] = []
    for rack in frame.get("racks", ()):
        model = rack.get("gpu_model")
        if not model:
            continue  # CPU-only / empty rack — not a thermal actor
        cap = rack.get("capacity_gpus", 0)
        active = rack.get("active_gpus", 0)
        throttling = rack.get("throttling_gpus", 0)
        pressure = _weighted_queue_pressure(queue_by_rack.get(rack["rack_id"], []))
        trend = trend_of(rack["rack_id"])

        if active > 0:
            throttle_temp = throttle_temp_of(model)
            level = trend.level if trend is not None else rack.get("temp_c_mean_active", 0.0)
            slope_min = trend.slope_c_per_min if trend is not None else 0.0
            throttle_frac = throttling / active
            risk = _clamp(
                0.50 * throttle_frac
                + 0.30 * min(1.0, pressure / BOTTLENECK_PRESSURE_CRIT_GPU_MIN)
                + 0.20 * _clamp(slope_min / _SLOPE_NORM_C_PER_MIN, 0.0, 1.0),
                0.0, 1.0,
            )
            hotspots.append({
                "rack_id": rack["rack_id"], "gpu_model": model,
                "risk": round(risk, 3), "throttling_gpus": throttling,
                "headroom_c": round(throttle_temp - level, 1),
                "queued_heavy": rack.get("queued_heavy", 0),
                "queued_gpu_minutes": round(pressure, 1),
                "slope_c_per_min": round(slope_min, 3),
            })

        if active < cap:  # has spare GPUs -> a candidate migration target
            targets.append({
                "rack_id": rack["rack_id"], "gpu_model": model,
                "util": round(rack.get("util", 0.0), 4),
                "free_gpus": cap - active,
                "temp_c_mean_active": round(rack.get("temp_c_mean_active", 0.0), 1),
            })

    hotspots.sort(key=lambda h: (-h["risk"], h["rack_id"]))
    # Coolest, emptiest racks first — the best places to move work to.
    targets.sort(key=lambda tg: (tg["util"], tg["temp_c_mean_active"], tg["rack_id"]))
    return {"hotspots": hotspots, "targets": targets}
