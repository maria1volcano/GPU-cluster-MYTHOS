"""Deterministic migration recommender — DESIGN.md §2.5 (M4 guardrail)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from sentinel.data.models import Pod
from sentinel.data.racks import Topology
from sentinel.predict.schema import Prediction
from sentinel.replay.placement import BinPackPlacement
from sentinel.replay.state import ClusterState
from sentinel.telemetry.profiles import PROFILES


@dataclass
class Candidate:
    job_id: str
    from_rack: str
    to_rack: str
    score: float
    to_rack_free_capacity_frac: float
    to_rack_thermal_headroom_c: float
    expected_effect: str


class Recommender:
    def __init__(
        self,
        topology: Topology,
        state: ClusterState,
        placement: BinPackPlacement,
        pods_by_name: Dict[str, Pod],
        rack_temp_provider: Callable[[str], Optional[float]],
    ) -> None:
        self.topology = topology
        self.state = state
        self.placement = placement
        self.pods_by_name = pods_by_name
        self.rack_temp_provider = rack_temp_provider

    def _jobs_on_rack(self, rack_id: str) -> List[str]:
        return sorted(p for p, r in self.state.pod_rack.items() if r == rack_id)

    def _pick_movable_job(self, rack_id: str) -> Optional[str]:
        jobs = self._jobs_on_rack(rack_id)
        heavy = sorted(
            (j for j in jobs if self.pods_by_name.get(j) and self.pods_by_name[j].heavy),
            key=lambda j: self.pods_by_name[j].gpu_demand,
            reverse=True,
        )
        pending_heavy = sorted(
            (p.name for p in self.state.pending.values()
             if p.heavy and self.placement.preview_rack(p) == rack_id),
            key=lambda name: self.pods_by_name[name].gpu_demand,
            reverse=True,
        )
        pool = heavy or pending_heavy or sorted(jobs)
        return pool[0] if pool else None

    def candidates(self, prediction: Prediction, top_n: int = 3) -> List[Candidate]:
        target_kind = prediction.target.get("kind", "rack")
        if target_kind == "rack":
            from_rack = prediction.target["id"]
        elif target_kind == "node":
            from_rack = prediction.target.get("rack_id")
            if not from_rack:
                return []
        else:
            return []
        job_id = self._pick_movable_job(from_rack)
        if job_id is None:
            return []
        pod = self.pods_by_name[job_id]
        required_milli = pod.num_gpu * pod.gpu_milli

        scored: List[Candidate] = []
        for rack in self.topology.racks:
            rack_id = rack.rack_id
            if rack_id == from_rack:
                continue
            capacity = rack.capacity_gpus * 1000
            if capacity <= 0:
                continue
            free = capacity - self.state.rack_demand_milli[rack_id]
            if free < required_milli:
                continue
            if self.placement.choose_in_rack(pod, rack_id) is None:
                continue

            free_frac = free / capacity
            prof = PROFILES.get(rack.gpu_model) if rack.gpu_model else None
            if prof is None:
                continue
            temp = self.rack_temp_provider(rack_id)
            if temp is None:
                temp = prof.idle_temp
            headroom_c = prof.throttle_temp - temp
            span = max(1.0, prof.throttle_temp - prof.idle_temp)
            headroom_frac = max(0.0, min(1.0, headroom_c / span))
            score = round(free_frac * headroom_frac, 4)

            scored.append(
                Candidate(
                    job_id=job_id,
                    from_rack=from_rack,
                    to_rack=rack_id,
                    score=score,
                    to_rack_free_capacity_frac=round(free_frac, 3),
                    to_rack_thermal_headroom_c=round(headroom_c, 1),
                    expected_effect=(
                        f"{from_rack} sheds {required_milli / 1000:.2f} GPU(s) of load and its projected "
                        f"temperature drops back below the throttle line; {rack_id} has headroom to absorb it."
                    ),
                )
            )

        scored.sort(key=lambda c: (-c.score, c.to_rack))
        return scored[:top_n]
