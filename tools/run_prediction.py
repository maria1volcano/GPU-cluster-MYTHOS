"""M3 demo harness — run the prediction layer and print predictions as they fire.

    python3 -m tools.run_prediction --live        # drive M0-M2 Engine over DEMO_WINDOW
    python3 -m tools.run_prediction --fixtures     # replay fixtures/telemetry_frames.sample.jsonl
    python3 -m tools.run_prediction --golden       # single golden frame (the money moment)

Live mode is the M3 gate: it should emit a THERMAL_THROTTLE on the hot G2 rack
(rack-00) plus a matching SCHEDULING_BOTTLENECK (>=3 heavy jobs queued) during
the demo window, each with attached numeric evidence (FR-11).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, Iterator

from sentinel.config import DEMO_WINDOW, FIXTURES_DIR
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import Prediction


def _fmt_evidence(pred: Prediction) -> str:
    bits = []
    for e in pred.evidence:
        d = e.to_dict()
        metric = d.pop("metric")
        inner = ", ".join(f"{k}={v}" for k, v in d.items())
        bits.append(f"{metric}({inner})")
    return " | ".join(bits)


def _print_pred(pred: Prediction) -> None:
    tgt = pred.target["id"]
    eta = "now" if pred.eta_seconds <= 0 else f"~{pred.eta_seconds / 60:.1f} min"
    print(f"  [{pred.severity.upper():8}] {pred.type:22} {tgt:8} "
          f"eta={eta:9} conf={pred.confidence:.2f}  {pred.prediction_id}")
    print(f"             evidence: {_fmt_evidence(pred)}")


def _frames_from_jsonl(path) -> Iterator[Dict]:
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _live_frames(window) -> Iterator[Dict]:
    from sentinel.engine import Engine
    for frame in Engine().run(window):
        yield frame.to_dict()


def _show_whatif(eng, pe, frame: Dict) -> None:
    """Print the digital-twin forecast + counterfactual 'simulate the fix' for
    the current hotspot: projected peak throttling doing nothing vs migrating a
    few running heavy jobs onto the coolest target rack."""
    from sentinel.config import TICK_TRACE_S
    from sentinel.predict.twin import DigitalTwin, _summarize

    board = pe.risk_board(frame)
    if not board["hotspots"] or not board["targets"]:
        return
    hot, tgt = board["hotspots"][0], board["targets"][0]
    rid, to = hot["rack_id"], tgt["rack_id"]
    print(f"    -- digital twin: hotspot {rid} (risk {hot['risk']}, {hot['throttling_gpus']} throttling, "
          f"{hot['queued_gpu_minutes']} GPU-min queued) -> target {to} "
          f"({tgt['gpu_model']}, {tgt['free_gpus']} free, {tgt['temp_c_mean_active']}C) --")
    state = eng.replayer.state
    heavy_running = [n for n, rk in state.pod_rack.items()
                     if rk == rid and eng.replayer.pods[n].heavy]
    twin = DigitalTwin(eng)
    base = twin.project(rid)
    print(f"       do nothing : projected peak throttling {base.peak_throttling} GPUs over {base.horizon_s // 60} min")
    for n in (3, 6):
        fork = eng.fork()
        moved = sum(1 for j in heavy_running[:n] if fork.apply_action("MIGRATE_JOB", j, to))
        proj = _summarize(rid, eng.t, twin._roll(fork, rid),
                          twin.horizon_ticks * TICK_TRACE_S, twin.sustained_ticks)
        print(f"       migrate {moved} -> {to}: projected peak throttling {proj.peak_throttling} GPUs "
              f"(averted {base.peak_throttling - proj.peak_throttling})")


def run_live(window, enable_instability: bool = True) -> int:
    """Live mode drives the real Engine so the digital twin can fork it: same
    prediction cards as offline, plus a one-time twin/counterfactual readout at
    the first thermal episode."""
    from sentinel.engine import Engine
    eng = Engine()
    pe = PredictionEngine(enable_instability=enable_instability, engine=eng)
    total = 0
    seen_types: Dict[str, int] = {}
    active_ids: Dict[str, str] = {}
    whatif_shown = False
    for frame, preds in pe.attach(eng, window):
        if preds:
            day = frame.get("trace_day", frame["t"] / 86400.0)
            printed_header = False
            for p in preds:
                if active_ids.get(p.type) != p.prediction_id:
                    if not printed_header:
                        print(f"t={frame['t']} (day {day:.3f}, tick {frame.get('tick', '?')}):")
                        printed_header = True
                    _print_pred(p)
                    active_ids[p.type] = p.prediction_id
                seen_types[p.type] = seen_types.get(p.type, 0) + 1
                total += 1
            if not whatif_shown and any(p.type == "THERMAL_THROTTLE" for p in preds):
                _show_whatif(eng, pe, frame)
                whatif_shown = True
    return _summary(total, seen_types)


def _summary(total: int, seen_types: Dict[str, int]) -> int:
    print("\n--- summary ---")
    print(f"total prediction-ticks: {total}")
    for tp, n in sorted(seen_types.items()):
        print(f"  {tp:22} fired on {n} ticks")
    gate_ok = "THERMAL_THROTTLE" in seen_types and "SCHEDULING_BOTTLENECK" in seen_types
    print("M3 PREDICTORS " + ("OK" if gate_ok else "INCOMPLETE (expected THERMAL_THROTTLE + SCHEDULING_BOTTLENECK)"))
    return 0 if gate_ok else 1


def run(frames: Iterator[Dict], enable_instability: bool = True) -> int:
    engine = PredictionEngine(enable_instability=enable_instability)
    total = 0
    seen_types: Dict[str, int] = {}
    active_ids: Dict[str, str] = {}  # type -> prediction_id currently on screen
    for frame in frames:
        preds = engine.on_frame(frame)
        # Print a tick header + card only when a NEW episode (new prediction_id
        # for that type) starts, to keep the stream readable during long runs.
        if preds:
            day = frame.get("trace_day", frame["t"] / 86400.0)
            printed_header = False
            for p in preds:
                is_new = active_ids.get(p.type) != p.prediction_id
                if is_new:
                    if not printed_header:
                        print(f"t={frame['t']} (day {day:.3f}, tick {frame.get('tick', '?')}):")
                        printed_header = True
                    _print_pred(p)
                    active_ids[p.type] = p.prediction_id
                seen_types[p.type] = seen_types.get(p.type, 0) + 1
                total += 1
    return _summary(total, seen_types)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the M3 prediction layer.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--live", action="store_true", help="drive the M0-M2 Engine over DEMO_WINDOW")
    src.add_argument("--fixtures", action="store_true", help="replay the sample .jsonl trajectory")
    src.add_argument("--golden", action="store_true", help="single golden frame")
    ap.add_argument("--no-instability", action="store_true", help="disable the P1 node-instability predictor")
    args = ap.parse_args(argv)

    if args.live:
        return run_live(DEMO_WINDOW, enable_instability=not args.no_instability)
    if args.golden:
        frames: Iterator[Dict] = iter([json.load(open(FIXTURES_DIR / "telemetry_frame.golden.json"))])
    else:  # default: fixtures
        frames = _frames_from_jsonl(FIXTURES_DIR / "telemetry_frames.sample.jsonl")

    return run(frames, enable_instability=not args.no_instability)


if __name__ == "__main__":
    sys.exit(main())
