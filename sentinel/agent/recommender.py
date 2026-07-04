"""Deterministic migration recommender — DESIGN.md §2.5.

Produces **validated** candidate actions before the LLM ever sees the
prediction. This is the guardrail described in DESIGN §2.5: the LLM may
only choose among these candidates, it can never invent an unsafe
placement, because every candidate here has already been checked for real
spare capacity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from sentinel.models import Pod, Prediction, Rack
from sentinel.replay import ClusterSimulator
from sentinel.telemetry import DEFAULT_PROFILE, MODEL_PROFILES


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
        racks: Dict[str, Rack],
        simulator: ClusterSimulator,
        pods_by_name: Dict[str, Pod],
        rack_temp_provider: Callable[[str], Optional[float]],
    ) -> None:
        self.racks = racks
        self.simulator = simulator
        self.pods_by_name = pods_by_name
        self.rack_temp_provider = rack_temp_provider

    def _pick_movable_job(self, rack_id: str) -> Optional[str]:
        jobs = self.simulator.jobs_on_rack(rack_id)
        heavy = sorted(j for j in jobs if self.pods_by_name.get(j) and self.pods_by_name[j].is_heavy)
        pool = heavy or sorted(jobs)
        return pool[0] if pool else None

    def candidates(self, prediction: Prediction, top_n: int = 3) -> List[Candidate]:
        if prediction.target.get("kind") != "rack":
            return []
        from_rack = prediction.target["id"]
        job_id = self._pick_movable_job(from_rack)
        if job_id is None:
            return []
        pod = self.pods_by_name[job_id]
        required_milli = pod.requested_gpu_milli

        scored: List[Candidate] = []
        for rack_id, rack in self.racks.items():
            if rack_id == from_rack:
                continue
            capacity = self.simulator.rack_capacity_milli(rack_id)
            if capacity <= 0:
                continue
            free = capacity - self.simulator.rack_committed_milli(rack_id)
            if free < required_milli:
                continue  # guardrail: candidate must actually have spare capacity

            free_frac = free / capacity
            profile = MODEL_PROFILES.get(rack.gpu_model, DEFAULT_PROFILE)
            temp = self.rack_temp_provider(rack_id)
            if temp is None:
                temp = profile["idle_temp"]
            headroom_c = profile["throttle_temp"] - temp
            span = max(1.0, profile["throttle_temp"] - profile["idle_temp"])
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
                        f"{from_rack} sheds {required_milli/1000:.2f} GPU(s) of load and its projected "
                        f"temperature drops back below the throttle line; {rack_id} has headroom to absorb it."
                    ),
                )
            )

        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:top_n]
