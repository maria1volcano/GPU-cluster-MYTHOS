"""Node and Pod models (DESIGN §3.1–3.2), straight from the openb CSVs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sentinel.config import HEAVY_GPU_DEMAND


@dataclass(frozen=True)
class Node:
    sn: str            # primary key, e.g. "openb-node-0000"
    cpu_milli: int
    memory_mib: int
    gpu: int           # physical GPU count on this node
    model: str         # G2 | T4 | P100 | G3 | V100M32 | V100M16 | A10

    def gpu_ids(self) -> list[str]:
        return [f"{self.sn}/gpu-{i}" for i in range(self.gpu)]


@dataclass(frozen=True)
class Pod:
    name: str          # primary key, e.g. "openb-pod-0001"
    cpu_milli: int
    memory_mib: int
    num_gpu: int
    gpu_milli: int     # per-GPU request in thousandths; 1000 = one whole GPU
    gpu_spec: str      # empty for every row in this trace
    qos: str           # LS | BE | Burstable | Guaranteed
    pod_phase: str     # Running | Failed | Pending | Succeeded
    creation_time: int
    deletion_time: int
    scheduled_time: Optional[int]  # None => never scheduled (Pending)
    censored: bool     # deletion_time == trace end => still running at trace end

    @property
    def is_gpu_pod(self) -> bool:
        return self.num_gpu > 0

    @property
    def gpu_demand(self) -> float:
        """Total demand in GPU-equivalents. Verified: every multi-GPU pod in the
        trace has gpu_milli == 1000, so num_gpu * gpu_milli / 1000 is uniform."""
        return self.num_gpu * self.gpu_milli / 1000.0

    @property
    def heavy(self) -> bool:
        return self.gpu_demand >= HEAVY_GPU_DEMAND

    @property
    def is_pending(self) -> bool:
        return self.scheduled_time is None
