"""End-to-end walkthrough exercising FR-5, FR-9, and FR-11.

Run with:

    python -m sentinel.demo

This wires together the real M0-M3 pipeline (CSV loading → derived racks →
event-time simulator → synthesized telemetry → prediction engine) and then
focuses on the three requirements this branch delivers:

  FR-5  One-tap recommendation card (plain language + concrete action +
        justifying trend), produced by `sentinel.agent.Agent`.
  FR-11 Explainability: the alert and its card both carry the exact
        numeric evidence that fired the prediction.
  FR-9  Learning from overrides: a short scripted operator-decision
        history is appended to the decision log, then
        `sentinel.learning.OverrideLearner` is run to show thresholds
        actually move as a result.

To keep the demo deterministic and not dependent on where organic
congestion happens to land in the trace, it uses the same "stress
scenario trigger" the frontend README already describes: it raises one
real rack's apparent load and lets the *real* telemetry/prediction model
react to that load exactly as it would to organic congestion.
"""
from __future__ import annotations

import argparse
import copy
import logging
import sys
import time
from typing import List, Optional

from sentinel import config
from sentinel.agent.agent import Agent
from sentinel.agent.recommender import Recommender
from sentinel.config import Thresholds
from sentinel.data.loader import load_nodes, load_pods, stats
from sentinel.decision_log import DecisionLog
from sentinel.learning import OverrideLearner
from sentinel.models import Prediction, Recommendation
from sentinel.predict.engine import PredictionEngine
from sentinel.replay import ClusterSimulator
from sentinel.telemetry import SimTelemetrySource
from sentinel.topology import derive_racks

DEMO_WINDOW_START = 137 * 86400  # near the real peak-concurrency day (PRD §2.2)
TICK_SECONDS = config.TICK_SECONDS
NUM_TICKS = 12


def _hr(title: str = "") -> None:
    print("\n" + "=" * 78)
    if title:
        print(title)
        print("=" * 78)


def build_pipeline():
    nodes = load_nodes(config.NODE_CSV)
    pods = load_pods(config.POD_CSV)
    racks = derive_racks(nodes)
    simulator = ClusterSimulator(nodes, racks, pods)
    pods_by_name = {p.name: p for p in pods}
    return nodes, pods, racks, simulator, pods_by_name


def pick_target_and_relief_racks(racks) -> tuple[str, str]:
    """Deterministically pick a dense (G2/G3, 8-GPU) rack as the "Rack 7"
    hotspot and a low-TDP (T4) rack as the natural "Rack 2" relief target —
    mirrors PRD §5's North-Star scenario and DESIGN §7's model grounding."""
    hot_candidates = sorted(r.rack_id for r in racks.values() if r.gpu_model in ("G2", "G3"))
    cool_candidates = sorted(r.rack_id for r in racks.values() if r.gpu_model == "T4")
    target = hot_candidates[3] if len(hot_candidates) > 3 else hot_candidates[0]
    return target, (cool_candidates[0] if cool_candidates else "")


def run_pipeline_to_first_alert(
    simulator: ClusterSimulator,
    racks,
    engine: PredictionEngine,
    telemetry: SimTelemetrySource,
    target_rack: str,
):
    """Ticks the simulator/telemetry/prediction loop until the target rack
    fires a THERMAL_THROTTLE prediction (or we run out of ticks)."""
    t = DEMO_WINDOW_START
    all_gpu_ids = telemetry.gpu_ids

    for i in range(NUM_TICKS):
        t = DEMO_WINDOW_START + i * TICK_SECONDS
        simulator.run_until(t)
        samples = [telemetry.sample(gpu_id, t) for gpu_id in all_gpu_ids]
        pending_heavy = simulator.pending_heavy_count()
        predictions = engine.ingest_tick(t, samples, pending_heavy)

        for p in predictions:
            if p.type == "THERMAL_THROTTLE" and p.target["id"] == target_rack:
                print(f"[t={t}] tick {i}: rack {target_rack} temp={engine.latest_rack_temp(target_rack):.1f}\u00b0C "
                      f"util={engine.latest_rack_util(target_rack):.2f} -> {p.type} eta={p.eta_seconds:.0f}s")
                return p, t
        if i % 3 == 0:
            temp = engine.latest_rack_temp(target_rack)
            util = engine.latest_rack_util(target_rack)
            if temp is not None:
                print(f"[t={t}] tick {i}: rack {target_rack} heating — temp={temp:.1f}\u00b0C util={util:.2f} (no alert yet)")
    return None, t


def print_recommendation_card(prediction: Prediction, recommendation: Optional[Recommendation]) -> None:
    _hr("FR-5 — One-tap recommendation card")
    print(f"Prediction: {prediction.type} on {prediction.target['id']} "
          f"(eta={prediction.eta_seconds:.0f}s, confidence={prediction.confidence}, severity={prediction.severity})")
    if recommendation is None:
        print("No safe migration candidate was available — alert surfaced without a one-tap action.")
        return
    border = "\u2500" * 70
    print(f"\n  \u256d{border}\u256e")
    for line in recommendation.as_card().splitlines():
        print(f"  \u2502 {line}")
    print(f"  \u2570{border}\u256f")
    print(f"\n  [ Approve ]   [ Override ]      (source: {recommendation.source})")

    _hr("FR-11 — Explainability: the raw trend behind this alert")
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
    print(f"\n  Recommendation card evidence field carries the same list "
          f"({len(recommendation.evidence)} item(s)) -> every alert links to the trend that produced it.")


def demonstrate_learning_from_overrides(
    log: DecisionLog,
    thresholds: Thresholds,
    live_prediction: Prediction,
    live_recommendation: Optional[Recommendation],
    t: int,
) -> None:
    _hr("FR-9 — Learning from overrides")
    print("Scripted operator history for this alert class (THERMAL_THROTTLE), oldest first:")

    history_actions = ["OVERRIDE", "OVERRIDE", "DISMISS", "OVERRIDE"]
    for i, action in enumerate(history_actions):
        synthetic_pred = copy.deepcopy(live_prediction)
        synthetic_pred.prediction_id = f"pred-hist-{i:02d}"
        entry = log.record(
            t=t - (len(history_actions) - i) * 600,
            prediction=synthetic_pred,
            recommendation=live_recommendation,
            operator_action=action,
            operator_alternative=None,
            outcome="UNKNOWN",
            lead_time_seconds=thresholds.thermal_throttle.lead_time_seconds,
            latency_ms=850.0,
        )
        print(f"  - {entry.decision_id}: operator_action={action} (treated as a false positive)")

    before = thresholds.thermal_throttle.lead_time_seconds
    learner = OverrideLearner(log, thresholds)
    adjustments = learner.apply()
    after = thresholds.thermal_throttle.lead_time_seconds
    print(f"\nBefore learning: THERMAL_THROTTLE lead_time_seconds = {before}")
    if adjustments:
        for adj in adjustments:
            print(f"  Learner adjustment: {adj}")
    print(f"After learning:  THERMAL_THROTTLE lead_time_seconds = {after}"
          f"  ({'tightened' if after < before else 'unchanged'} — repeated overrides raised the bar to fire)")

    print("\nNow the operator approves the *current*, real alert and the migration averts it:")
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

    # A second approve+averted round shows the class beginning to relax back
    # toward baseline once it's proving itself useful again.
    entry2 = log.record(
        t=t + 900,
        prediction=live_prediction,
        recommendation=live_recommendation,
        operator_action="APPROVE",
        operator_alternative=None,
        outcome="AVERTED",
        lead_time_seconds=live_prediction.eta_seconds,
        latency_ms=1100.0,
    )
    print(f"  - {entry2.decision_id}: operator_action=APPROVE outcome=AVERTED")

    before2 = thresholds.thermal_throttle.lead_time_seconds
    adjustments2 = learner.apply()
    after2 = thresholds.thermal_throttle.lead_time_seconds
    print(f"\nAfter two averted incidents: lead_time_seconds {before2} -> {after2}")
    if adjustments2:
        for adj in adjustments2:
            print(f"  Learner adjustment: {adj}")
    print(f"\nPersisted learned thresholds -> {config.STATE_PATH}")
    print(f"Full decision log ({len(log.read_all())} entries) -> {config.DECISION_LOG_PATH}")


def run_demo(reset_state: bool = True) -> int:
    logging.basicConfig(level=logging.WARNING)

    if reset_state:
        config.STATE_PATH.unlink(missing_ok=True)
        config.DECISION_LOG_PATH.unlink(missing_ok=True)

    _hr("GPU Cluster Sentinel — FR-5 / FR-9 / FR-11 demo")
    nodes, pods, racks, simulator, pods_by_name = build_pipeline()
    s = stats(nodes, pods)
    print(f"Loaded {s['node_count']} nodes / {s['total_gpus']} GPUs / {s['pod_count']} pods "
          f"({s['fractional_gpu_pods']} fractional-GPU pods) across {len(racks)} derived racks.")

    target_rack, relief_rack = pick_target_and_relief_racks(racks)
    print(f"Target ('Rack 7'-style) rack: {target_rack}  |  Expected relief ('Rack 2'-style) rack: {relief_rack}")

    gpu_models = dict(simulator.gpu_models)
    telemetry = SimTelemetrySource(
        gpu_models=gpu_models,
        gpu_node=simulator.gpu_node,
        gpu_rack=simulator.gpu_rack,
        util_provider=simulator.gpu_util,
        rack_util_provider=simulator.rack_util,
        seed=config.RNG_SEED,
    )

    simulator.run_until(DEMO_WINDOW_START)
    # Stress-scenario trigger (README): deterministically raise the target
    # rack's apparent load so the demo doesn't depend on where organic
    # congestion happens to land in the trace window, AND force-place a
    # handful of *real* heavy pods from the trace onto it so the agent has
    # genuine, capacity-checked jobs to recommend migrating (PRD §5: "3
    # heavy jobs queued").
    nodes_by_sn = {n.sn: n for n in nodes}
    rack_node_gpu_count = max((nodes_by_sn[nid].gpu for nid in racks[target_rack].node_ids), default=1)
    forced_jobs = [p for p in pods if p.is_heavy and p.num_gpu <= rack_node_gpu_count and p.name not in simulator.placement]
    placed = 0
    for pod in forced_jobs:
        if placed >= config.DEFAULT_BOTTLENECK_QUEUED_HEAVY:
            break
        if simulator.force_place_on_rack(pod, target_rack):
            placed += 1
    print(f"Stress scenario: force-placed {placed} real heavy job(s) onto {target_rack} "
          f"(e.g. {', '.join(simulator.jobs_on_rack(target_rack)[:3])}).")
    simulator.set_stress_override(target_rack, util=0.97, queued_heavy=config.DEFAULT_BOTTLENECK_QUEUED_HEAVY)

    thresholds = Thresholds.load()
    engine = PredictionEngine(racks, thresholds)

    _hr("Live replay -> telemetry -> prediction")
    prediction, t = run_pipeline_to_first_alert(simulator, racks, engine, telemetry, target_rack)
    if prediction is None:
        print("No THERMAL_THROTTLE alert fired within the demo tick budget.", file=sys.stderr)
        return 1

    recommender = Recommender(
        racks=racks,
        simulator=simulator,
        pods_by_name=pods_by_name,
        rack_temp_provider=engine.latest_rack_temp,
    )
    agent = Agent(recommender=recommender)
    recommendation = agent.recommend(prediction)
    print_recommendation_card(prediction, recommendation)

    if recommendation is not None:
        applied = simulator.apply_action_migrate_job(recommendation.job_id, recommendation.to_rack)
        print(f"\nOperator approves -> apply_action(MIGRATE_JOB, {recommendation.job_id}, {recommendation.to_rack}) -> applied={applied}")

    log = DecisionLog()
    demonstrate_learning_from_overrides(log, thresholds, prediction, recommendation, t)

    _hr("Done")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sentinel FR-5/FR-9/FR-11 demo.")
    parser.add_argument("--keep-state", action="store_true", help="Don't reset decision_log.jsonl / sentinel_state.json before running.")
    args = parser.parse_args()
    sys.exit(run_demo(reset_state=not args.keep_state))


if __name__ == "__main__":
    main()
