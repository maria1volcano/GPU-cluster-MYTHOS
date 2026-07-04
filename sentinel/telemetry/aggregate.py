"""Rack-level rollups + TelemetryFrame assembly (the contract's producer)."""
from __future__ import annotations

from sentinel.data.models import Pod
from sentinel.replay.placement import BinPackPlacement
from sentinel.replay.state import ClusterState
from sentinel.telemetry.profiles import PROFILES
from sentinel.telemetry.sample import (GpuTelemetrySample, QueuedPodInfo,
                                       RackAggregate, TelemetryFrame)


def build_frame(tick: int, t: int, state: ClusterState,
                samples: list, placement: BinPackPlacement,
                events_applied: int) -> TelemetryFrame:
    topology = state.topology
    by_rack: dict = {r.rack_id: [] for r in topology.racks}
    for s in samples:
        by_rack[s.rack_id].append(s)

    # Pending pods + which rack the packing policy would send them to now.
    queue, queued_by_rack = [], {}
    for pod in state.pending.values():
        target = placement.preview_rack(pod)
        queue.append(QueuedPodInfo(
            name=pod.name, num_gpu=pod.num_gpu, gpu_milli=pod.gpu_milli,
            gpu_demand=pod.gpu_demand, qos=pod.qos, heavy=pod.heavy,
            waiting_s=t - pod.creation_time, target_rack=target,
        ))
        if target is not None:
            counts = queued_by_rack.setdefault(target, [0, 0])
            counts[0] += 1
            counts[1] += int(pod.heavy)

    racks = []
    for r in topology.racks:
        prof = PROFILES[r.gpu_model] if r.gpu_model else None
        idle = prof.idle_temp if prof else 30.0
        idle_w = prof.idle_w if prof else 0.0
        rs = by_rack[r.rack_id]
        active = [s for s in rs if s.util > 0]
        demand = state.rack_demand_milli[r.rack_id] / 1000.0
        queued = queued_by_rack.get(r.rack_id, [0, 0])
        racks.append(RackAggregate(
            rack_id=r.rack_id, gpu_model=r.gpu_model, num_nodes=len(r.node_sns),
            capacity_gpus=r.capacity_gpus, gpu_demand=demand,
            util=demand / r.capacity_gpus,
            active_gpus=len(active),
            temp_c_mean_active=(sum(s.temp_c for s in active) / len(active)) if active else idle,
            temp_c_max=max((s.temp_c for s in rs), default=idle),
            power_w_total=sum(s.power_w for s in rs) + idle_w * (r.capacity_gpus - len(rs)),
            throttling_gpus=sum(1 for s in rs if s.throttle_reasons),
            active_pods=state.rack_active_pods[r.rack_id],
            queued_pods=queued[0], queued_heavy=queued[1],
        ))

    pending = list(state.pending.values())
    cluster = {
        "active_pods": state.active_pods,
        "pending_pods": len(pending),
        "pending_heavy": sum(1 for p in pending if p.heavy),
        "gpu_demand": round(state.total_demand_milli / 1000.0, 2),
        "capacity_gpus": sum(r.capacity_gpus for r in topology.racks),
        "active_gpus": len(state.active_gpus),
        "throttling_gpus": sum(1 for s in samples if s.throttle_reasons),
        "events_applied": events_applied,
    }
    return TelemetryFrame(
        tick=tick, t=t, trace_day=t / 86400.0,
        samples=tuple(sorted(samples, key=lambda s: s.gpu_id)),
        racks=tuple(racks), queue=tuple(queue), cluster=cluster,
    )
