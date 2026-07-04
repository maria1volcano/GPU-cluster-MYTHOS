"""Event-time stream replayer (M1, DESIGN §2.2).

Virtual clock over the merged event queue. `seek(t)` rebuilds state from
t=0 deterministically (fast — one pass over ~23k events), `step(dt)` advances
one tick applying all events in (t, t+dt]. Wall pacing (SPEEDUP) is the
server loop's job: sleep TICK_TRACE_S / SPEEDUP between steps.

`apply_action("MIGRATE_JOB", job_id, to_rack)` is the operator-approval seam:
frees the job's current placement (or dequeues a pending job) and places it
on the target rack. A pod migrated while pending skips its own later
POD_SCHEDULED event (it is already placed).

Run `python3 -m sentinel.replay.replayer` for the M1 gate.
"""
from __future__ import annotations

from typing import Optional

from sentinel.config import TICK_TRACE_S
from sentinel.data.models import Pod
from sentinel.data.racks import Topology
from sentinel.replay.events import (POD_CREATED, POD_DELETED, POD_SCHEDULED,
                                    build_event_queue)
from sentinel.replay.placement import BinPackPlacement
from sentinel.replay.state import ClusterState

MIGRATE_JOB = "MIGRATE_JOB"


class Replayer:
    def __init__(self, topology: Topology, pods: list[Pod]):
        self.topology = topology
        self.pods = {p.name: p for p in pods}
        self.events = build_event_queue(pods)
        self.state: ClusterState
        self.placement: BinPackPlacement
        self.reset()

    def reset(self) -> None:
        self.state = ClusterState(self.topology)
        self.placement = BinPackPlacement(self.topology, self.state)
        self.t = 0
        self._idx = 0
        self.events_applied = 0
        self.unplaceable: list = []
        self._apply_until(0)  # t==0 events apply, so reset() == seek(0) exactly

    # --- clock -------------------------------------------------------------
    def seek(self, t: int) -> None:
        """Deterministic fast-forward: reset and apply every event with
        event.t <= t. Same t => identical state, always."""
        self.reset()
        self._apply_until(t)
        self.t = t

    def step(self, dt: int = TICK_TRACE_S) -> ClusterState:
        self.t += dt
        self._apply_until(self.t)
        return self.state

    def _apply_until(self, t: int) -> None:
        while self._idx < len(self.events) and self.events[self._idx].t <= t:
            self._apply(self.events[self._idx])
            self._idx += 1
            self.events_applied += 1

    # --- event semantics -----------------------------------------------------
    def _apply(self, event) -> None:
        pod = self.pods[event.pod_name]
        if event.kind == POD_CREATED:
            self.state.enqueue(pod)
        elif event.kind == POD_SCHEDULED:
            self.state.dequeue(pod.name)
            if pod.name in self.state.placements or pod.name in self.state.active_cpu:
                return  # already placed by an operator migration
            if not pod.censored and pod.deletion_time <= event.t:
                return  # dead on arrival (no such pod in this trace; guard anyway)
            assignments = self.placement.choose(pod)
            if assignments is None:
                self.unplaceable.append(pod.name)  # keep pending; never happens in this trace
                self.state.enqueue(pod)
            else:
                self.state.place(pod, assignments, event.t)
        elif event.kind == POD_DELETED:
            if self.state.dequeue(pod.name) is None:
                self.state.free(pod.name, event.t)

    # --- operator action (DESIGN §4: Backend -> Replayer) ---------------------
    def apply_action(self, action: str, job_id: str, to_rack: str) -> bool:
        """Guardrail seam for agent/operator actions: every invalid input
        (unknown action aside) returns False, never raises."""
        if action != MIGRATE_JOB:
            raise ValueError(f"unknown action: {action}")
        pod = self.pods.get(job_id)
        if pod is None or not pod.is_gpu_pod:
            return False
        if to_rack not in self.topology.rack_by_id:
            return False  # unknown/hallucinated rack id
        if job_id in self.state.pending:
            assignments = self.placement.choose_in_rack(pod, to_rack)
            if assignments is None:
                return False  # target lacks capacity
            self.state.dequeue(job_id)
            self.state.place(pod, assignments, self.t)
            return True
        old = self.state.placements.get(job_id)
        if old is None:
            return False  # job not live (already finished)
        # Free first so the pod's own slots count as capacity (a same-rack
        # rebalance on a full rack must not be rejected); roll back on failure.
        self.state.free(job_id, self.t)
        assignments = self.placement.choose_in_rack(pod, to_rack)
        if assignments is None:
            self.state.place(pod, old, self.t)
            return False
        self.state.place(pod, assignments, self.t)
        return True


def main() -> int:
    """M1 gate: full-trace walk reproduces the verified trace dynamics."""
    from sentinel.data.loader import load_nodes, load_pods
    from sentinel.data.racks import derive_racks

    topology = derive_racks(load_nodes())
    pods = load_pods()
    r = Replayer(topology, pods)

    peak = (0, 0)          # (active pods, t)
    max_queue = (0, 0, 0)  # (depth, heavy, t)
    while r._idx < len(r.events):
        t = r.events[r._idx].t
        r.step(t - r.t)    # jump event-to-event (exact, no tick quantization)
        active = r.state.active_pods
        if active > peak[0]:
            peak = (active, r.t)
        depth = len(r.state.pending)
        heavy = sum(1 for p in r.state.pending.values() if p.heavy)
        if depth > max_queue[0]:
            max_queue = (depth, heavy, r.t)

    checks = [
        ("events applied", r.events_applied, 23_525),  # 8152 created + 7255 scheduled + 8118 deleted
        ("peak active pods", peak, (56, 11_821_651)),
        ("max queue depth (depth, heavy, t)", max_queue, (8, 8, 12_824_105)),
        ("unplaceable pods", len(r.unplaceable), 0),
        ("still running at trace end (censored)", r.state.active_pods, 34),
        ("queue empty at trace end", len(r.state.pending), 0),
    ]
    r2 = Replayer(topology, pods)
    r2.seek(12_824_105)
    r3 = Replayer(topology, pods)
    r3.seek(12_824_105)
    checks.append(("seek determinism", r2.state.snapshot_key() == r3.state.snapshot_key(), True))
    checks.append(("apply_action guardrails (unknown rack / unknown job -> False, no crash)",
                   (r2.apply_action(MIGRATE_JOB, "openb-pod-0001", "rack-999"),
                    r2.apply_action(MIGRATE_JOB, "no-such-pod", "rack-01")), (False, False)))
    demand = r2.state.total_demand_milli / 1000
    checks.append(("demand at queue peak plausible (40..130 GPU-eq)", 40 <= demand <= 130, True))
    top = max(r2.state.rack_demand_milli.items(), key=lambda kv: kv[1])
    print(f"at queue peak: demand={demand:.1f} GPU-eq, busiest rack={top[0]} "
          f"({top[1]/1000:.1f} GPU-eq), queue={len(r2.state.pending)}")

    ok = True
    for label, actual, expected in checks:
        good = actual == expected
        ok &= good
        print(f"[{'PASS' if good else 'FAIL'}] {label}: {actual}" + ("" if good else f" (expected {expected})"))
    print("REPLAYER GATE " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
