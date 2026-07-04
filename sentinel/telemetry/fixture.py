"""Generate the contract fixtures from the real pipeline (CONTRACTS.md §7).

Run:  python3 -m sentinel.telemetry.fixture

Writes (deterministic — regenerating produces identical bytes):
  fixtures/telemetry_frame.golden.json   one frame at the day-148.43 queue peak
                                         (heavy jobs queued, hot rack over the line)
  fixtures/telemetry_frames.sample.jsonl every 10th frame across the demo window
"""
from __future__ import annotations

import json

from sentinel.config import DEMO_WINDOW, FIXTURES_DIR
from sentinel.engine import Engine

GOLDEN_TICK = 137   # t = window start + 4,140s: just past the queue peak


def main() -> int:
    FIXTURES_DIR.mkdir(exist_ok=True)
    engine = Engine()
    golden = None
    sampled = []
    for frame in engine.run():
        if frame.tick == GOLDEN_TICK:
            golden = frame
        if frame.tick % 10 == 0:
            sampled.append(frame)

    golden_path = FIXTURES_DIR / "telemetry_frame.golden.json"
    golden_path.write_text(json.dumps(golden.to_dict(), indent=2) + "\n")
    series_path = FIXTURES_DIR / "telemetry_frames.sample.jsonl"
    with open(series_path, "w") as f:
        for frame in sampled:
            f.write(json.dumps(frame.to_dict()) + "\n")

    hot = max(golden.racks, key=lambda r: r.gpu_demand)
    print(f"golden frame: tick {golden.tick}, t={golden.t} (day {golden.trace_day:.2f}), "
          f"{len(golden.samples)} samples, queue={len(golden.queue)}")
    print(f"  hot rack {hot.rack_id}: {hot.gpu_demand:.1f} GPU-eq, "
          f"mean_active {hot.temp_c_mean_active:.1f}°C, {hot.throttling_gpus} throttling, "
          f"{hot.queued_heavy} heavy queued")
    print(f"wrote {golden_path.relative_to(FIXTURES_DIR.parent)} and "
          f"{series_path.relative_to(FIXTURES_DIR.parent)} ({len(sampled)} frames)")
    assert hot.queued_heavy >= 3, "golden frame must show the queued-heavy signal"
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
