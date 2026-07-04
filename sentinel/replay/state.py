"""Live cluster state: placements, per-GPU load, pending queue, rack rollups.

GPU load is in milli (0..1000). CPU-only pods are tracked as active but hold
no GPU assignment and no rack attribution (they don't affect telemetry).
CPU/memory capacity is intentionally not modeled: concurrent demand in this
trace never approaches fleet capacity, and GPU load is the demo's signal.
"""
from __future__ import annotations

from typing import Optional

from sentinel.data.models import Pod
from sentinel.data.racks import Topology


class ClusterState:
    def __init__(self, topology: Topology):
        self.topology = topology
        self.load_milli: dict = {g: 0 for g in topology.gpu_ids()}
        self.free_whole_gpus: dict = {n.sn: n.gpu for n in topology.nodes}
        self.active_gpus: set = set()     # gpu_ids with load > 0
        self.load_changed_t: dict = {}    # gpu_id -> event time its load last changed
        self.placements: dict = {}        # pod_name -> tuple of (gpu_id, milli)
        self.pod_rack: dict = {}          # pod_name -> rack_id (GPU pods only)
        self.pending: dict = {}           # pod_name -> Pod, insertion order
        self.active_cpu: set = set()      # active CPU-only pod names
        self.rack_demand_milli: dict = {r.rack_id: 0 for r in topology.racks}
        self.rack_active_pods: dict = {r.rack_id: 0 for r in topology.racks}

    # --- queue -----------------------------------------------------------
    def enqueue(self, pod: Pod) -> None:
        self.pending[pod.name] = pod

    def dequeue(self, pod_name: str) -> Optional[Pod]:
        return self.pending.pop(pod_name, None)

    # --- placement -------------------------------------------------------
    def place(self, pod: Pod, assignments: tuple, t: int) -> None:
        if not assignments:               # CPU-only pod
            self.active_cpu.add(pod.name)
            return
        rack_id = self.topology.rack_of[assignments[0][0].split("/")[0]]
        for gpu_id, milli in assignments:
            was_free = self.load_milli[gpu_id] == 0
            self.load_milli[gpu_id] += milli
            self.active_gpus.add(gpu_id)
            self.load_changed_t[gpu_id] = t
            if was_free:
                self.free_whole_gpus[gpu_id.split("/")[0]] -= 1
            self.rack_demand_milli[rack_id] += milli
        self.placements[pod.name] = assignments
        self.pod_rack[pod.name] = rack_id
        self.rack_active_pods[rack_id] += 1

    def free(self, pod_name: str, t: int) -> bool:
        if pod_name in self.active_cpu:
            self.active_cpu.discard(pod_name)
            return True
        assignments = self.placements.pop(pod_name, None)
        if assignments is None:
            return False
        rack_id = self.pod_rack.pop(pod_name)
        for gpu_id, milli in assignments:
            self.load_milli[gpu_id] -= milli
            self.load_changed_t[gpu_id] = t
            if self.load_milli[gpu_id] == 0:
                self.free_whole_gpus[gpu_id.split("/")[0]] += 1
                self.active_gpus.discard(gpu_id)
            self.rack_demand_milli[rack_id] -= milli
        self.rack_active_pods[rack_id] -= 1
        return True

    # --- rollups ---------------------------------------------------------
    @property
    def active_pods(self) -> int:
        return len(self.placements) + len(self.active_cpu)

    @property
    def total_demand_milli(self) -> int:
        return sum(self.rack_demand_milli.values())

    def snapshot_key(self) -> tuple:
        """Deterministic fingerprint of the full state (for gates/tests)."""
        return (
            tuple(sorted((k, v) for k, v in self.load_milli.items() if v)),
            tuple(sorted(self.placements)),
            tuple(self.pending),
            tuple(sorted(self.active_cpu)),
        )
