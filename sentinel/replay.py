"""Event-time cluster simulator (M1) — DESIGN.md §2.2.

The openb pod trace records *when* a pod was scheduled but not *where*
(no node/GPU assignment column exists in `openb_pod_list_default.csv`).
Sentinel therefore derives placement with a deterministic best-fit
scheduler driven by the real `creation_time` / `scheduled_time` /
`deletion_time` events and real `gpu_milli` load — everything else
(utilization, queue pressure) falls straight out of that real event
stream, consistent with the "real vs. synthesized" table in DESIGN §2.1.

For demo reliability (see README "Stress scenario trigger" and PRD's
windowing discussion), `set_stress_override` lets the operator/demo
harness deterministically raise a rack's apparent utilization and queued
heavy-job count on top of whatever the organic replay produced, so the
North-Star flow (FR-2/3/5/9/11) doesn't depend on getting lucky with
where real congestion lands in a chosen window.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sentinel.models import Node, Pod, Rack


class ClusterSimulator:
    def __init__(self, nodes: List[Node], racks: Dict[str, Rack], pods: List[Pod]):
        self.nodes = {n.sn: n for n in nodes}
        self.racks = racks
        self.node_capacity_milli: Dict[str, int] = {n.sn: n.gpu * 1000 for n in nodes}
        self.committed_milli: Dict[str, int] = {n.sn: 0 for n in nodes}
        self.placement: Dict[str, str] = {}
        self.pending: Dict[str, Pod] = {}

        self.gpu_models: Dict[str, str] = {}
        self.gpu_node: Dict[str, str] = {}
        self.gpu_rack: Dict[str, str] = {}
        for n in nodes:
            for i in range(n.gpu):
                gpu_id = f"{n.sn}/gpu-{i}"
                self.gpu_models[gpu_id] = n.model
                self.gpu_node[gpu_id] = n.sn
                self.gpu_rack[gpu_id] = n.rack_id or ""

        self._nodes_sorted = sorted(nodes, key=lambda n: n.sn)
        self._events = self._build_events(pods)
        self._event_idx = 0
        self.t = 0

        self._rack_util_override: Dict[str, float] = {}
        self._queued_heavy_override: Optional[int] = None
        self._forced_pods: Dict[str, Pod] = {}

    @staticmethod
    def _build_events(pods: List[Pod]):
        # (time, priority, kind, pod) — priority orders same-timestamp events
        # SCHEDULE before FREE before CREATE so capacity accounting stays sane.
        events = []
        for p in pods:
            events.append((p.creation_time, 2, "CREATE", p))
            if p.scheduled_time is not None:
                events.append((p.scheduled_time, 0, "SCHEDULE", p))
            events.append((p.deletion_time, 1, "FREE", p))
        events.sort(key=lambda e: (e[0], e[1]))
        return events

    def run_until(self, t_end: int) -> None:
        """Advance the simulator, applying every event with time <= t_end."""
        n = len(self._events)
        while self._event_idx < n and self._events[self._event_idx][0] <= t_end:
            _, _, kind, pod = self._events[self._event_idx]
            self._event_idx += 1
            if kind == "CREATE":
                if pod.is_pending:
                    self.pending[pod.name] = pod
            elif kind == "SCHEDULE":
                self.pending.pop(pod.name, None)
                self._place(pod)
            elif kind == "FREE":
                self.pending.pop(pod.name, None)
                self._free(pod)
        self.t = t_end

    def _place(self, pod: Pod) -> None:
        req = pod.requested_gpu_milli
        if req <= 0:
            return
        best_node, best_free = None, None
        for n in self._nodes_sorted:
            cap = self.node_capacity_milli[n.sn]
            free = cap - self.committed_milli[n.sn]
            if free >= req and (best_free is None or free < best_free):
                best_node, best_free = n.sn, free
        if best_node is None:
            # Trace-wide capacity contention edge case: fall back to whichever
            # node currently has the most free room so accounting stays consistent.
            best_node = max(self._nodes_sorted, key=lambda n: self.node_capacity_milli[n.sn] - self.committed_milli[n.sn]).sn
        self.placement[pod.name] = best_node
        self.committed_milli[best_node] += req

    def _free(self, pod: Pod) -> None:
        node_sn = self.placement.pop(pod.name, None)
        if node_sn is not None:
            self.committed_milli[node_sn] = max(0, self.committed_milli[node_sn] - pod.requested_gpu_milli)

    # --- read model (real load) -------------------------------------------------
    def rack_capacity_milli(self, rack_id: str) -> int:
        rack = self.racks[rack_id]
        return sum(self.node_capacity_milli[nid] for nid in rack.node_ids)

    def rack_committed_milli(self, rack_id: str) -> int:
        rack = self.racks[rack_id]
        return sum(self.committed_milli[nid] for nid in rack.node_ids)

    def rack_util(self, rack_id: str) -> float:
        cap = self.rack_capacity_milli(rack_id)
        base = (self.rack_committed_milli(rack_id) / cap) if cap else 0.0
        return max(base, self._rack_util_override.get(rack_id, 0.0))

    def gpu_util(self, gpu_id: str) -> float:
        node_sn = self.gpu_node[gpu_id]
        cap = self.node_capacity_milli[node_sn]
        base = (self.committed_milli[node_sn] / cap) if cap else 0.0
        return max(base, self._rack_util_override.get(self.gpu_rack[gpu_id], 0.0))

    def pending_heavy_count(self) -> int:
        """Global count of currently-pending "heavy" pods.

        The trace has no field tying a pending pod to a target rack, so
        Sentinel attributes queue pressure to whichever rack is currently
        hottest/fullest (see `predict.engine`) rather than inventing a fake
        per-rack affinity — this keeps the number itself grounded in real
        pending-pod data (DESIGN §2.1 "real vs. synthesized").
        """
        if self._queued_heavy_override is not None:
            return self._queued_heavy_override
        return sum(1 for p in self.pending.values() if p.is_heavy)

    def jobs_on_rack(self, rack_id: str) -> List[str]:
        rack = self.racks[rack_id]
        node_set = set(rack.node_ids)
        return [name for name, node_sn in self.placement.items() if node_sn in node_set]

    # --- demo / operator controls -------------------------------------------------
    def force_place_on_rack(self, pod: Pod, rack_id: str) -> bool:
        """Forcibly places a *real* pod (from the loaded trace) onto a node
        in `rack_id`, used by the demo's stress-scenario trigger so the
        agent has a genuine, capacity-checked job to recommend migrating —
        rather than inventing one. Also registers the pod so
        `apply_action_migrate_job` can look it up later."""
        req = pod.requested_gpu_milli
        rack = self.racks.get(rack_id)
        if rack is None or req <= 0:
            return False
        best_node, best_free = None, None
        for nid in rack.node_ids:
            free = self.node_capacity_milli[nid] - self.committed_milli[nid]
            if free >= req and (best_free is None or free > best_free):
                best_node, best_free = nid, free
        if best_node is None:
            return False
        self.placement[pod.name] = best_node
        self.committed_milli[best_node] += req
        self._forced_pods[pod.name] = pod
        return True

    def set_stress_override(self, rack_id: str, util: float, queued_heavy: Optional[int] = None) -> None:
        """Deterministically raises a rack's apparent load for demo purposes
        (README "Stress scenario trigger"). Does not fabricate telemetry
        directly — it raises the *load* signal that the telemetry model (M2)
        reacts to, so temperature/clocks still evolve through the same
        physically-motivated model as organic load would produce."""
        self._rack_util_override[rack_id] = util
        if queued_heavy is not None:
            self._queued_heavy_override = queued_heavy

    def clear_stress_override(self, rack_id: str) -> None:
        self._rack_util_override.pop(rack_id, None)
        if not self._rack_util_override:
            self._queued_heavy_override = None

    def apply_action_migrate_job(self, job_id: str, to_rack: str) -> bool:
        """`apply_action(MIGRATE_JOB, job, to_rack)` (DESIGN §2.2) — moves an
        already-placed pod's committed load off its current node and onto a
        node with room in `to_rack`, recomputing both racks' load."""
        current_node = self.placement.get(job_id)
        if current_node is None:
            return False
        pod = None
        for _, _, kind, p in self._events:
            if kind in ("SCHEDULE", "CREATE") and p.name == job_id:
                pod = p
                break
        if pod is None:
            return False
        req = pod.requested_gpu_milli
        target_rack = self.racks.get(to_rack)
        if target_rack is None:
            return False
        best_node, best_free = None, None
        for nid in target_rack.node_ids:
            free = self.node_capacity_milli[nid] - self.committed_milli[nid]
            if free >= req and (best_free is None or free < best_free):
                best_node, best_free = nid, free
        if best_node is None:
            return False
        self.committed_milli[current_node] -= req
        self.committed_milli[best_node] += req
        self.placement[job_id] = best_node
        # Clear any stress override on the source rack — the whole point of
        # the migration is to relieve it; the telemetry model then relaxes
        # back down through the same thermal-inertia curve it heated up with.
        from_rack_id = self.nodes[current_node].rack_id
        if from_rack_id:
            self.clear_stress_override(from_rack_id)
        return True
