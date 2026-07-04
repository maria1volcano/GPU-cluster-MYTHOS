"""Engine — wires replayer + telemetry into per-tick TelemetryFrames.

This is what the M5 server loop drives:

    engine = Engine()
    engine.seek(DEMO_WINDOW[0])            # warm-started, deterministic
    for frame in engine.run():             # or engine.tick() one at a time
        broadcast(frame.to_dict())         # WS /stream + latest-state cache
        # wall pacing: sleep TICK_TRACE_S / SPEEDUP between ticks

    engine.apply_action("MIGRATE_JOB", job_id, to_rack)   # operator approve

Run `python3 -m sentinel.engine` for the M2 gate (heat emerges in the demo
window; migration bends the curve; determinism).
"""
from __future__ import annotations

from typing import Optional

from sentinel.config import DEMO_WINDOW, SEED, TICK_TRACE_S
from sentinel.telemetry.aggregate import build_frame
from sentinel.telemetry.sample import TelemetryFrame
from sentinel.telemetry.sim import SimTelemetrySource


class Engine:
    def __init__(self, topology=None, pods=None, seed: int = SEED):
        if topology is None or pods is None:
            from sentinel.data.loader import load_nodes, load_pods
            from sentinel.data.racks import derive_racks
            topology = topology or derive_racks(load_nodes())
            pods = pods or load_pods()
        from sentinel.replay.replayer import Replayer
        self.topology = topology
        self.replayer = Replayer(topology, pods)
        self.sim = SimTelemetrySource(topology, seed)
        self.tick_no = 0

    @property
    def t(self) -> int:
        return self.replayer.t

    def seek(self, t: int) -> None:
        self.replayer.seek(t)
        self.sim.warm_start(t, self.replayer.state)
        self.tick_no = 0

    def tick(self, dt: int = TICK_TRACE_S) -> TelemetryFrame:
        state = self.replayer.step(dt)
        samples = self.sim.step(self.replayer.t, state)
        frame = build_frame(self.tick_no, self.replayer.t, state, samples,
                            self.replayer.placement, self.replayer.events_applied)
        self.tick_no += 1
        return frame

    def run(self, window: tuple = DEMO_WINDOW, dt: int = TICK_TRACE_S):
        """Seek to window start, then yield one frame per tick to window end."""
        start, end = window
        self.seek(start)
        while self.replayer.t < end:
            yield self.tick(dt)

    def apply_action(self, action: str, job_id: str, to_rack: str) -> bool:
        return self.replayer.apply_action(action, job_id, to_rack)


def main() -> int:
    """M2 gate: the demo-window dynamics M3 needs, all emergent from real load:
    hot G2 rack starts below the throttle line, the day-148.43 heavy-job burst
    pushes its active-mean over the line SUSTAINED, queue pressure spikes, and
    an operator migration measurably cools the rack vs. doing nothing."""
    engine = Engine()
    start, end = DEMO_WINDOW
    burst_t = start + 4_110           # first tick past the trace's queue peak
    line = 84.0                       # G2 throttle_temp (profiles.py)

    hottest_rack: Optional[str] = None
    first = None
    queued_heavy_peak = (0, None)
    sustained = 0                     # longest run of ticks with mean_active >= line after burst
    run = 0
    frames = []
    for frame in engine.run():
        frames.append(frame)
        if first is None:
            first = frame
        busy = max(frame.racks, key=lambda r: r.gpu_demand)
        if hottest_rack is None and busy.gpu_demand > 0:
            hottest_rack = busy.rack_id
        rack = next(r for r in frame.racks if r.rack_id == hottest_rack)
        if rack.queued_heavy > queued_heavy_peak[0]:
            queued_heavy_peak = (rack.queued_heavy, frame.t)
        if frame.t >= burst_t:
            run = run + 1 if rack.temp_c_mean_active >= line else 0
            sustained = max(sustained, run)

    def rack_row(frame, rack_id):
        return next(r for r in frame.racks if r.rack_id == rack_id)

    last = frames[-1]
    r0_first, r0_last = rack_row(first, hottest_rack), rack_row(last, hottest_rack)
    print(f"demo window {start}..{end} (day {start/86400:.2f}..{end/86400:.2f}), "
          f"{len(frames)} ticks, hottest rack = {hottest_rack}")
    print(f"  {hottest_rack} demand: {r0_first.gpu_demand:.1f} -> {r0_last.gpu_demand:.1f} GPU-eq | "
          f"temp_mean_active: {r0_first.temp_c_mean_active:.1f} -> {r0_last.temp_c_mean_active:.1f} °C | "
          f"temp_max: {r0_first.temp_c_max:.1f} -> {r0_last.temp_c_max:.1f} °C")
    print(f"  peak queued_heavy on {hottest_rack}: {queued_heavy_peak[0]} at t={queued_heavy_peak[1]}; "
          f"longest post-burst run over {line}°C: {sustained} ticks")

    expected_frames = -(-(end - start) // TICK_TRACE_S)
    checks = [
        ("frames emitted", len(frames), expected_frames),
        ("42 rack aggregates every frame", all(len(f.racks) == 42 for f in frames), True),
        ("hottest rack is a G2 rack", rack_row(last, hottest_rack).gpu_model, "G2"),
        ("window opens BELOW the throttle line",
         rack_row(first, hottest_rack).temp_c_mean_active < line, True),
        (">=3 heavy jobs queued on hot rack at some tick", queued_heavy_peak[0] >= 3, True),
        ("post-burst mean_active over the line sustained >= 10 ticks (5 sim-min)",
         sustained >= 10, True),
    ]

    # Determinism: a second engine reproduces frame N bit-for-bit.
    engine2 = Engine()
    frames2 = []
    for f in engine2.run():
        frames2.append(f)
        if len(frames2) == 100:
            break
    checks.append(("determinism (frame 99 identical)",
                   frames[99].to_dict() == frames2[99].to_dict(), True))

    # Counterfactual: at the queue peak, migrate every heavy job queued for the
    # hot rack to rack-01 (same model, empty). 15 sim-minutes later the hot rack
    # must be measurably cooler and less throttled than the no-action run.
    engine3 = Engine()
    engine3.seek(start)
    target_frame = None
    while engine3.t < burst_t:
        target_frame = engine3.tick()
    heavy_queued = [q for q in target_frame.queue if q.heavy and q.target_rack == hottest_rack]
    migrated = [q.name for q in heavy_queued
                if engine3.apply_action("MIGRATE_JOB", q.name, "rack-01")]
    post = engine3.tick()
    post_hot = rack_row(post, hottest_rack)
    landed = next(r for r in post.racks if r.rack_id == "rack-01").gpu_demand
    horizon = burst_t + 450           # migrated jobs are short-lived; compare mid-flight
    acted = baseline = None
    while engine3.t < horizon:
        acted = engine3.tick()
    for f in frames:                  # no-action run at the same instant
        if f.t == acted.t:
            baseline = f
            break
    a, b = rack_row(acted, hottest_rack), rack_row(baseline, hottest_rack)
    print(f"  counterfactual at t=+{horizon - start}s: no-action {b.throttling_gpus} throttled / "
          f"{b.gpu_demand:.1f} GPU-eq / mean {b.temp_c_mean_active:.2f}°C  vs  migrated "
          f"{a.throttling_gpus} throttled / {a.gpu_demand:.1f} GPU-eq / mean {a.temp_c_mean_active:.2f}°C; "
          f"landed on rack-01: {landed:.1f} GPU-eq")
    checks.append(("heavy jobs queued on hot rack at queue peak", len(heavy_queued) >= 3, True))
    checks.append(("all queued heavy jobs migrated", len(migrated), len(heavy_queued)))
    checks.append(("migrated demand landed on rack-01", landed >= 3.0, True))
    checks.append(("hot-rack heavy queue drained by the action", post_hot.queued_heavy, 0))
    checks.append(("action relieves the hot rack (fewer throttled GPUs, less demand)",
                   a.throttling_gpus < b.throttling_gpus and a.gpu_demand < b.gpu_demand, True))

    ok = True
    for label, actual, expected in checks:
        good = actual == expected
        ok &= good
        print(f"[{'PASS' if good else 'FAIL'}] {label}: {actual}" + ("" if good else f" (expected {expected})"))
    print("ENGINE GATE " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
