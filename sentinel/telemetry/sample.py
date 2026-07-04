"""FROZEN telemetry contract — the shapes M3 (prediction) and M5 (dashboard)
build against. Human-readable spec + fixtures: CONTRACTS.md, fixtures/.

Do not change key names or semantics without bumping SCHEMA_VERSION and
telling the team. Adding new optional keys is allowed (consumers must
tolerate unknown keys).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SCHEMA_VERSION = 1
FRAME_TYPE_TELEMETRY = "telemetry"  # /stream also carries "prediction" (M3) and "kpi" (M6) frames


@dataclass(frozen=True)
class GpuTelemetrySample:
    """One GPU at one tick — DCGM-style, exactly DESIGN §3.3."""
    gpu_id: str                    # "openb-node-0123/gpu-3"
    node_sn: str
    rack_id: str
    model: str                     # G2 | T4 | P100 | G3 | V100M32 | V100M16 | A10
    t: int                         # trace-seconds
    util: float                    # 0..1 (driven by real gpu_milli load)
    temp_c: float
    power_w: float
    sm_clock_mhz: int              # < base clock  <=>  throttled
    mem_clock_mhz: int
    mem_used_mib: int
    throttle_reasons: tuple = ()   # subset of {"SW_THERMAL","HW_THERMAL","SW_POWER_CAP"}
    xid_errors: int = 0            # count this tick
    ecc_errors: dict = field(default_factory=lambda: {"volatile": 0, "aggregate": 0})

    def to_dict(self) -> dict:
        return {
            "gpu_id": self.gpu_id,
            "node_sn": self.node_sn,
            "rack_id": self.rack_id,
            "model": self.model,
            "t": self.t,
            "util": round(self.util, 3),
            "temp_c": round(self.temp_c, 1),
            "power_w": round(self.power_w, 1),
            "sm_clock_mhz": self.sm_clock_mhz,
            "mem_clock_mhz": self.mem_clock_mhz,
            "mem_used_mib": self.mem_used_mib,
            "throttle_reasons": list(self.throttle_reasons),
            "xid_errors": self.xid_errors,
            "ecc_errors": dict(self.ecc_errors),
        }


@dataclass(frozen=True)
class RackAggregate:
    """Per-rack rollup — what the prediction layer trends on and the map colors by.

    Load concentrates on a few nodes (bin-packing), so a plain mean over all
    GPUs would dilute the signal: `temp_c_mean_active` averages only GPUs with
    load, `temp_c_max` is the hottest GPU, `util` is demand/capacity.
    """
    rack_id: str
    gpu_model: Optional[str]
    num_nodes: int
    capacity_gpus: int
    gpu_demand: float              # GPU-equivalents currently placed on this rack
    util: float                    # gpu_demand / capacity_gpus
    active_gpus: int               # GPUs with any load
    temp_c_mean_active: float      # mean over active GPUs (model idle temp if none)
    temp_c_max: float
    power_w_total: float
    throttling_gpus: int
    active_pods: int
    queued_pods: int               # pending pods whose placement preview targets this rack
    queued_heavy: int

    def to_dict(self) -> dict:
        return {
            "rack_id": self.rack_id,
            "gpu_model": self.gpu_model,
            "num_nodes": self.num_nodes,
            "capacity_gpus": self.capacity_gpus,
            "gpu_demand": round(self.gpu_demand, 2),
            "util": round(self.util, 4),
            "active_gpus": self.active_gpus,
            "temp_c_mean_active": round(self.temp_c_mean_active, 1),
            "temp_c_max": round(self.temp_c_max, 1),
            "power_w_total": round(self.power_w_total, 1),
            "throttling_gpus": self.throttling_gpus,
            "active_pods": self.active_pods,
            "queued_pods": self.queued_pods,
            "queued_heavy": self.queued_heavy,
        }


@dataclass(frozen=True)
class QueuedPodInfo:
    """A pending pod, with the rack the bin-packing policy would place it on now."""
    name: str
    num_gpu: int
    gpu_milli: int
    gpu_demand: float
    qos: str
    heavy: bool
    waiting_s: int                 # t - creation_time
    target_rack: Optional[str]     # None if nothing currently fits

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "num_gpu": self.num_gpu,
            "gpu_milli": self.gpu_milli,
            "gpu_demand": round(self.gpu_demand, 2),
            "qos": self.qos,
            "heavy": self.heavy,
            "waiting_s": self.waiting_s,
            "target_rack": self.target_rack,
        }


@dataclass(frozen=True)
class TelemetryFrame:
    """One tick of cluster truth. Transport-agnostic: the same JSON is pushed
    over WS /stream and returned by GET /api/cluster/state (latest frame).

    `samples` contains only interesting GPUs (util > 0, temp above idle, or
    throttle/errors present). A GPU absent from `samples` is idle at ambient —
    consumers must treat missing as idle, not as an error.
    """
    tick: int                      # tick counter since window start
    t: int                         # trace-seconds
    trace_day: float               # t / 86400, for humans
    samples: tuple                 # GpuTelemetrySample, sorted by gpu_id
    racks: tuple                   # RackAggregate for ALL racks, rack index order
    queue: tuple                   # QueuedPodInfo, creation order
    cluster: dict                  # cluster-wide summary (see CONTRACTS.md)

    def to_dict(self) -> dict:
        return {
            "type": FRAME_TYPE_TELEMETRY,
            "v": SCHEMA_VERSION,
            "tick": self.tick,
            "t": self.t,
            "trace_day": round(self.trace_day, 3),
            "samples": [s.to_dict() for s in self.samples],
            "racks": [r.to_dict() for r in self.racks],
            "queue": [q.to_dict() for q in self.queue],
            "cluster": dict(self.cluster),
        }
