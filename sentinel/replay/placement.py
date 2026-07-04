"""Deterministic bin-packing placement (approved policy).

The trace records WHEN pods scheduled but not WHERE (no node column), so
placement is synthesized: pack the busiest rack first, tightest-fit node and
GPU within it. This mirrors GPU-packing schedulers (reduce fragmentation) and
concentrates real load so throttling emerges from real demand — nothing is
scripted.

Rules, all ties broken lexically for determinism:
  1. Candidate racks ordered by (highest current demand, lowest rack index).
     Racks at >= RACK_FILL_CAP utilization are deferred unless nothing else fits.
  2. Whole-GPU pods (gpu_milli == 1000): all num_gpu GPUs on ONE node (k8s
     semantics). Node chosen = fewest free whole GPUs that still fit.
  3. Fractional pods (num_gpu == 1, gpu_milli < 1000): GPU chosen = least
     free capacity that still fits => shared GPUs fill up before new ones open.
     (43.6% of GPU pods share — the headline openb signal.)
CPU-only pods get an empty assignment (tracked active, no rack).
"""
from __future__ import annotations

from typing import Optional

from sentinel.config import RACK_FILL_CAP
from sentinel.data.models import Pod
from sentinel.data.racks import Topology
from sentinel.replay.state import ClusterState


class BinPackPlacement:
    def __init__(self, topology: Topology, state: ClusterState):
        self.topology = topology
        self.state = state

    def choose(self, pod: Pod) -> Optional[tuple]:
        """Assignments tuple ((gpu_id, milli), ...) or None if nothing fits.
        Empty tuple for CPU-only pods."""
        if not pod.is_gpu_pod:
            return ()
        rack = self.preview_rack(pod)
        return None if rack is None else self.choose_in_rack(pod, rack)

    def preview_rack(self, pod: Pod) -> Optional[str]:
        """The rack `choose` would use, without applying. Also drives the
        queued_pods-per-rack attribution in the telemetry frame."""
        if not pod.is_gpu_pod:
            return None
        ranked = sorted(
            self.topology.racks,
            key=lambda r: (-self.state.rack_demand_milli[r.rack_id], r.rack_id),
        )
        preferred, overflow = [], []
        for rack in ranked:
            demand = self.state.rack_demand_milli[rack.rack_id] / 1000.0
            if RACK_FILL_CAP is not None and demand >= RACK_FILL_CAP * rack.capacity_gpus:
                overflow.append(rack)
            else:
                preferred.append(rack)
        for rack in preferred + overflow:
            if self.choose_in_rack(pod, rack.rack_id) is not None:
                return rack.rack_id
        return None

    def choose_in_rack(self, pod: Pod, rack_id: str) -> Optional[tuple]:
        rack = self.topology.rack_by_id[rack_id]
        if pod.gpu_milli == 1000:
            # Whole GPUs, single node: tightest node = fewest free GPUs that fit.
            best_sn, best_free = None, None
            for sn in rack.node_sns:
                free = self.state.free_whole_gpus[sn]
                if free >= pod.num_gpu and (best_free is None or free < best_free):
                    best_sn, best_free = sn, free
            if best_sn is None:
                return None
            node = self.topology.node_by_sn[best_sn]
            free_gpus = [g for g in node.gpu_ids() if self.state.load_milli[g] == 0]
            return tuple((g, 1000) for g in free_gpus[:pod.num_gpu])
        # Fractional (num_gpu == 1 in this trace): tightest GPU that fits.
        best_gpu, best_free = None, None
        for sn in rack.node_sns:
            for g in self.topology.node_by_sn[sn].gpu_ids():
                free = 1000 - self.state.load_milli[g]
                if free >= pod.gpu_milli and (best_free is None or free < best_free):
                    best_gpu, best_free = g, free
        if best_gpu is None:
            return None
        return ((best_gpu, pod.gpu_milli),)
