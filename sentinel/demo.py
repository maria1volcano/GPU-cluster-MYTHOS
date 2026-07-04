"""End-to-end M4/M6/M7 demo on the M0–M2 engine + Parv's M3 prediction layer.

Run:

    python3 -m sentinel.demo
    python3 -m sentinel.demo --tts
    python3 -m sentinel.demo --keep-state
"""
from __future__ import annotations

import argparse
import copy
import logging
import sys
import time
from typing import Callable, Optional, Tuple

from sentinel import config
from sentinel.agent.agent import Agent
from sentinel.agent.recommender import Recommender
from sentinel.config import Thresholds
from sentinel.decision_log import DecisionLog
from sentinel.engine import Engine
from sentinel.learning import OverrideLearner
from sentinel.models import Recommendation
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import Prediction, THERMAL_THROTTLE
from sentinel.tts import AlertSpeaker, build_alert_text


def _hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def _hottest_g2_rack(engine: Engine) -> str:
    g2 = [r for r in engine.topology.racks if r.gpu_model == "G2"]
    return max(g2, key=lambda r: r.capacity_gpus).rack_id


def _cool_migration_target(engine: Engine, exclude: str) -> str:
    t4 = [r for r in engine.topology.racks if r.gpu_model == "T4" and r.rack_id != exclude]
    return t4[0].rack_id if t4 else next(r.rack_id for r in engine.topology.racks if r.rack_id != exclude)


def _rack_temp_provider(predictor: PredictionEngine) -> Callable[[str], Optional[float]]:
    def _temp(rack_id: str) -> Optional[float]:
        trend = predictor._trends.get(rack_id)
        return trend.level if trend is not None else None

    return _temp


def run_live_loop(
    engine: Engine,
    predictor: PredictionEngine,
    agent: Agent,
    target_rack: str,
) -> Tuple[Optional[Prediction], Optional[Recommendation], Optional[int], int, Optional[object]]:
    """Tick until a THERMAL_THROTTLE fires on the target rack."""
    prediction: Optional[Prediction] = None
    recommendation: Optional[Recommendation] = None
    alert_tick = 0
    alert_frame = None

    for frame in engine.run():
        alert_tick += 1
        preds = predictor.on_frame(frame)
        for p in preds:
            if p.type == THERMAL_THROTTLE and p.target["id"] == target_rack:
                prediction = p
                alert_frame = frame
                trend = predictor._trends.get(target_rack)
                temp = trend.level if trend else 0.0
                rack = next(r for r in frame.racks if r.rack_id == target_rack)
                print(
                    f"[t={frame.t}] tick {frame.tick}: {target_rack} "
                    f"temp={temp:.1f}°C util={rack.util:.2f} "
                    f"queued_heavy={rack.queued_heavy} throttling={rack.throttling_gpus} "
                    f"-> {p.type} eta={p.eta_seconds:.0f}s"
                )
                agent.recommender = Recommender(
                    topology=engine.topology,
                    state=engine.replayer.state,
                    placement=engine.replayer.placement,
                    pods_by_name=engine.replayer.pods,
                    rack_temp_provider=_rack_temp_provider(predictor),
                )
                recommendation = agent.recommend(p)
                return prediction, recommendation, frame.t, alert_tick, alert_frame

        if frame.tick % 50 == 0:
            trend = predictor._trends.get(target_rack)
            if trend is not None:
                rack = next(r for r in frame.racks if r.rack_id == target_rack)
                print(
                    f"[t={frame.t}] tick {frame.tick}: {target_rack} — "
                    f"temp={trend.level:.1f}°C util={rack.util:.2f} "
                    f"queued_heavy={rack.queued_heavy}"
                )

    return None, None, None, alert_tick, None


def print_recommendation_card(prediction: Prediction, recommendation: Optional[Recommendation]) -> None:
    _hr("M4 — One-tap recommendation card")
    print(
        f"Prediction: {prediction.type} on {prediction.target['id']} "
        f"(eta={prediction.eta_seconds:.0f}s, confidence={prediction.confidence}, "
        f"severity={prediction.severity})"
    )
    if recommendation is None:
        print("No safe migration candidate — alert surfaced without a one-tap action.")
        return
    border = "─" * 70
    print(f"\n  ╭{border}╮")
    for line in recommendation.as_card().splitlines():
        print(f"  │ {line}")
    print(f"  ╰{border}╯")
    print(f"\n  [ Approve ]   [ Override ]      (source: {recommendation.source})")

    _hr("Explainability — trend evidence behind this alert")
    for e in prediction.evidence:
        parts = [f"metric={e.metric}"]
        if e.current is not None:
            parts.append(f"current={e.current}")
        if e.slope_per_min is not None:
            parts.append(f"slope={e.slope_per_min}/min")
        if e.threshold is not None:
            parts.append(f"threshold={e.threshold}")
        if e.value is not None:
            parts.append(f"value={e.value}")
        print("  - " + ", ".join(parts))


def resolve_outcome(
    engine: Engine,
    predictor: PredictionEngine,
    prediction: Prediction,
    recommendation: Recommendation,
    before_frame,
) -> str:
    """After approve + migration, resolve via digital twin or live frame delta."""
    rack_id = prediction.target["id"]
    before_rack = next(r for r in before_frame.racks if r.rack_id == rack_id)

    cf = predictor.score_action(rack_id, recommendation.job_id, recommendation.to_rack)
    applied = engine.apply_action("MIGRATE_JOB", recommendation.job_id, recommendation.to_rack)
    print(
        f"\nOperator approves -> apply_action(MIGRATE_JOB, {recommendation.job_id}, "
        f"{recommendation.to_rack}) -> applied={applied}"
    )
    if not applied:
        return "UNKNOWN"

    if cf and cf.get("applied"):
        averted = cf.get("averted_peak_throttling_gpus", 0)
        print(f"  Twin counterfactual: projected peak throttling averted = {averted:.0f} GPUs")
        if averted > 0:
            return "AVERTED"

    for _ in range(5):
        frame = engine.tick()
        predictor.on_frame(frame)

    after_rack = next(r for r in frame.racks if r.rack_id == rack_id)
    if (
        after_rack.throttling_gpus < before_rack.throttling_gpus
        or after_rack.gpu_demand < before_rack.gpu_demand
    ):
        return "AVERTED"
    return "UNKNOWN"


def demonstrate_learning(
    log: DecisionLog,
    thresholds: Thresholds,
    live_prediction: Prediction,
    live_recommendation: Optional[Recommendation],
    t: int,
) -> None:
    _hr("M7 — Learning from overrides")
    print("Scripted operator history for THERMAL_THROTTLE, oldest first:")

    for i, action in enumerate(["OVERRIDE", "OVERRIDE", "DISMISS", "OVERRIDE"]):
        synthetic = copy.deepcopy(live_prediction)
        synthetic.prediction_id = f"pred-hist-{i:02d}"
        entry = log.record(
            t=t - (4 - i) * 600,
            prediction=synthetic,
            recommendation=live_recommendation,
            operator_action=action,
            operator_alternative=None,
            outcome="UNKNOWN",
            lead_time_seconds=thresholds.thermal_throttle.lead_time_seconds,
            latency_ms=850.0,
        )
        print(f"  - {entry.decision_id}: operator_action={action}")

    before = thresholds.thermal_throttle.lead_time_seconds
    learner = OverrideLearner(log, thresholds)
    adjustments = learner.apply()
    after = thresholds.thermal_throttle.lead_time_seconds
    print(f"\nBefore learning: THERMAL_THROTTLE lead_time_seconds = {before}")
    for adj in adjustments:
        print(f"  Learner adjustment: {adj}")
    print(
        f"After learning:  THERMAL_THROTTLE lead_time_seconds = {after} "
        f"({'tightened' if after < before else 'unchanged'})"
    )

    entry = log.record(
        t=t,
        prediction=live_prediction,
        recommendation=live_recommendation,
        operator_action="APPROVE",
        operator_alternative=None,
        outcome="AVERTED",
        lead_time_seconds=live_prediction.eta_seconds,
        latency_ms=1200.0,
    )
    print(f"  - {entry.decision_id}: operator_action=APPROVE outcome=AVERTED")
    print(f"\nPersisted thresholds -> {config.STATE_PATH}")
    print(f"Decision log ({len(log.read_all())} entries) -> {config.DECISION_LOG_PATH}")


def run_demo(reset_state: bool = True, use_tts: bool = False) -> int:
    logging.basicConfig(level=logging.WARNING)

    if reset_state:
        config.STATE_PATH.unlink(missing_ok=True)
        config.DECISION_LOG_PATH.unlink(missing_ok=True)

    _hr("GPU Cluster Sentinel — M3/M4/M6/M7 demo")
    start, end = config.DEMO_WINDOW
    print(f"Demo window t={start}..{end} (day {start/86400:.2f}..{end/86400:.2f})")

    engine = Engine()
    predictor = PredictionEngine(engine=engine)
    agent = Agent(
        recommender=Recommender(
            topology=engine.topology,
            state=engine.replayer.state,
            placement=engine.replayer.placement,
            pods_by_name=engine.replayer.pods,
            rack_temp_provider=_rack_temp_provider(predictor),
        )
    )

    target = _hottest_g2_rack(engine)
    relief = _cool_migration_target(engine, target)
    print(f"Target rack: {target}  |  Relief rack (T4): {relief}")

    _hr("Live replay → M3 prediction → M4 agent")
    t0 = time.perf_counter()
    prediction, recommendation, t, ticks, alert_frame = run_live_loop(
        engine, predictor, agent, target
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    if prediction is None:
        print("No THERMAL_THROTTLE alert fired within the demo window.", file=sys.stderr)
        return 1

    print_recommendation_card(prediction, recommendation)

    alert_text = build_alert_text(prediction, recommendation)
    print(f"\nTTS script: {alert_text}")
    if use_tts:
        wav = AlertSpeaker().speak_recommendation(prediction, recommendation)
        if wav:
            print(f"Gradium alert written to {wav}")

    log = DecisionLog()
    outcome = "UNKNOWN"
    if recommendation is not None and alert_frame is not None:
        outcome = resolve_outcome(engine, predictor, prediction, recommendation, alert_frame)

    log.record(
        t=t,
        prediction=prediction,
        recommendation=recommendation,
        operator_action="APPROVE" if recommendation else "DISMISS",
        operator_alternative=None,
        outcome=outcome,
        lead_time_seconds=prediction.eta_seconds,
        latency_ms=latency_ms,
    )
    print(f"\nM6 decision logged: outcome={outcome}, lead_time={prediction.eta_seconds:.0f}s")

    demonstrate_learning(log, Thresholds.load(), prediction, recommendation, t)

    _hr("Done")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sentinel M4/M6/M7 demo.")
    parser.add_argument("--keep-state", action="store_true")
    parser.add_argument("--tts", action="store_true")
    args = parser.parse_args()
    sys.exit(run_demo(reset_state=not args.keep_state, use_tts=args.tts))


if __name__ == "__main__":
    main()
