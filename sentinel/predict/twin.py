"""Digital-twin lookahead (M3 flagship) — DESIGN §2.4 forward simulation.

Instead of only extrapolating a temperature curve, we fork the live engine
(`Engine.fork()`) and roll the copy forward through the SAME synthesized-DCGM
thermal model that produces the real telemetry. This turns "the rack is hot
now" into "the rack will sustain throttle, peaking at N GPUs, over the next K
minutes" — a grounded forecast, because in this system telemetry is a
deterministic function of the (known, committed) scheduled load.

Two products:
  - `project(rack_id)`         — the no-action forecast (peak throttle, sustained
                                 duration, time-to-sustained-throttle).
  - `counterfactual(...)`      — fork twice from the same instant, apply a
                                 candidate migration on one, and quantify the
                                 delta ("migrating job X cuts projected peak
                                 throttling 45 -> 11 GPUs"). The shared future
                                 cancels out, so the delta is the pure causal
                                 effect of the action — exactly the "simulate
                                 the fix before you click" moment, and the basis
                                 for M6 outcome resolution.

Cost: forking copies a few small dicts and rolling forward is ~0.1 ms/tick, so a
20-tick projection is a couple of milliseconds — negligible vs the NFR-1 budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sentinel.predict.config import (
    TICK_TRACE_S,
    TWIN_HORIZON_TICKS,
    TWIN_SUSTAINED_TICKS,
)


@dataclass
class RackProjection:
    rack_id: str
    horizon_s: int
    peak_throttling: int          # max throttling GPUs over the horizon
    end_throttling: int           # throttling GPUs at the horizon end
    peak_temp_mean: float         # hottest projected active-mean temp
    sustained_ticks: int          # longest consecutive throttling run
    eta_sustained_s: Optional[int]  # secs to sustained-throttle onset (0 if already; None if never)
    trajectory: List[Tuple[int, int, float]]  # (t, throttling_gpus, temp_c_mean_active)


def _summarize(rack_id: str, t_now: int, traj: List[Tuple[int, int, float]],
               horizon_s: int, sustained_ticks: int) -> RackProjection:
    peak = max((x[1] for x in traj), default=0)
    end = traj[-1][1] if traj else 0
    peak_temp = max((x[2] for x in traj), default=0.0)

    # Longest consecutive run of throttling>=1, and the onset time of the first
    # run that reaches `sustained_ticks`.
    longest = run = 0
    eta_sustained: Optional[int] = None
    run_start_t: Optional[int] = None
    for (tt, thr, _temp) in traj:
        if thr >= 1:
            if run == 0:
                run_start_t = tt
            run += 1
            longest = max(longest, run)
            if eta_sustained is None and run >= sustained_ticks:
                eta_sustained = max(0, run_start_t - t_now)
        else:
            run = 0
            run_start_t = None
    return RackProjection(
        rack_id=rack_id, horizon_s=horizon_s, peak_throttling=peak,
        end_throttling=end, peak_temp_mean=round(peak_temp, 1),
        sustained_ticks=longest, eta_sustained_s=eta_sustained, trajectory=traj,
    )


class DigitalTwin:
    def __init__(self, engine, horizon_ticks: int = TWIN_HORIZON_TICKS,
                 sustained_ticks: int = TWIN_SUSTAINED_TICKS):
        self.engine = engine
        self.horizon_ticks = horizon_ticks
        self.sustained_ticks = sustained_ticks

    def _roll(self, forked_engine, rack_id: str) -> List[Tuple[int, int, float]]:
        traj = []
        for _ in range(self.horizon_ticks):
            frame = forked_engine.tick()
            r = next(rr for rr in frame.racks if rr.rack_id == rack_id)
            traj.append((frame.t, r.throttling_gpus, r.temp_c_mean_active))
        return traj

    def project(self, rack_id: str) -> RackProjection:
        """No-action forecast for `rack_id` over the horizon."""
        t_now = self.engine.t
        traj = self._roll(self.engine.fork(), rack_id)
        return _summarize(rack_id, t_now, traj, self.horizon_ticks * TICK_TRACE_S,
                          self.sustained_ticks)

    def counterfactual(self, rack_id: str, job_id: str, to_rack: str,
                       baseline: Optional[RackProjection] = None):
        """Quantify a candidate migration's effect on `rack_id`.

        Returns (baseline_projection, action_projection, applied: bool). If the
        action is invalid (`Engine.apply_action` returns False), action_projection
        is None and applied is False — the guardrail seam, surfaced honestly.
        """
        base = baseline if baseline is not None else self.project(rack_id)
        fork = self.engine.fork()
        applied = fork.apply_action("MIGRATE_JOB", job_id, to_rack)
        if not applied:
            return base, None, False
        t_now = self.engine.t
        traj = self._roll(fork, rack_id)
        acted = _summarize(rack_id, t_now, traj, self.horizon_ticks * TICK_TRACE_S,
                           self.sustained_ticks)
        return base, acted, True
