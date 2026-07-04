"""DCGM-style telemetry events for the live dashboard feed (M5/M8 seam).

Uses synthesized GpuTelemetrySample fields from the sim source — the same shapes
a real DCGM exporter would produce (see sentinel/telemetry/dcgm_stub.py).
"""
from __future__ import annotations

from typing import Dict, List

from sentinel.server.mappers import telemetry_event
from sentinel.telemetry.sample import TelemetryFrame


def events_from_frame(frame: TelemetryFrame, *, max_rack_events: int = 4, max_gpu_events: int = 3) -> List[Dict]:
    """Turn one telemetry tick into operator-readable DCGM-style feed events."""
    out: List[Dict] = []
    t = frame.t
    cluster = frame.cluster

    out.append(
        telemetry_event(
            (
                f"DCGM cluster tick {frame.tick}: "
                f"{cluster.get('active_gpus', 0)} active GPUs · "
                f"{cluster.get('pending_pods', 0)} queued pods · "
                f"{cluster.get('throttling_gpus', 0)} throttling"
            ),
            event_type="metric_update",
            severity="watch" if cluster.get("throttling_gpus", 0) else "healthy",
            t=t,
        )
    )

    throttled = [s for s in frame.samples if s.throttle_reasons]
    for sample in sorted(throttled, key=lambda s: s.temp_c, reverse=True)[:max_gpu_events]:
        reasons = ",".join(sample.throttle_reasons)
        out.append(
            telemetry_event(
                (
                    f"DCGM {sample.gpu_id}: {sample.temp_c:.0f}°C · "
                    f"{sample.util * 100:.0f}% util · {sample.power_w:.0f}W · "
                    f"SM {sample.sm_clock_mhz} MHz · throttle {reasons}"
                ),
                event_type="metric_update",
                severity="critical" if "HW_THERMAL" in sample.throttle_reasons else "warning",
                rack_id=sample.rack_id,
                t=t,
            )
        )

    hot_racks = sorted(frame.racks, key=lambda r: (r.temp_c_max, r.util), reverse=True)
    emitted = 0
    for rack in hot_racks:
        if rack.util < 0.02 and rack.queued_heavy == 0:
            continue
        sev = "critical" if rack.throttling_gpus else ("warning" if rack.temp_c_max > 80 else "watch")
        out.append(
            telemetry_event(
                (
                    f"DCGM {rack.rack_id}: avg {rack.temp_c_mean_active:.1f}°C · "
                    f"peak {rack.temp_c_max:.1f}°C · {rack.util * 100:.0f}% util · "
                    f"{rack.queued_heavy} heavy queued · {rack.throttling_gpus} throttling GPUs"
                ),
                event_type="metric_update",
                severity=sev,
                rack_id=rack.rack_id,
                t=t,
            )
        )
        emitted += 1
        if emitted >= max_rack_events:
            break

    if frame.queue:
        heavy = sum(1 for q in frame.queue if q.heavy)
        if heavy:
            out.append(
                telemetry_event(
                    f"Scheduler: {len(frame.queue)} pods waiting ({heavy} heavy) — bin-pack preview updated",
                    event_type="job_event",
                    severity="watch",
                    t=t,
                )
            )

    return out
