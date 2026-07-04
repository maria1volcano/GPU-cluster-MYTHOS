"""M3 evaluation harness — prove the upgrade, don't just claim it.

Three honest, grounded measurements on the real demo window + the real thermal
model:

  1. A/B discrimination + calibration: baseline (EWMA level + OLS slope + linear
     eta) vs upgraded (Kalman + ODE-inversion eta) across ALL 42 racks. Reports
     alert volume, false-positive racks (alerted but never actually throttled),
     and the Brier score of the alert confidence vs the realized outcome.

  2. ODE-vs-linear ETA accuracy: generate a ground-truth trajectory from the
     SAME RC thermal recurrence the telemetry model uses, then, from an early
     vantage point, compare each method's predicted time-to-throttle against the
     true crossing. Shows the physics inversion is more accurate AND avoids the
     false alarms linear extrapolation makes when a rack settles below the line.

  3. Counterfactual relief: the digital twin's projected reduction in peak
     throttling GPUs as more running heavy jobs are migrated off the hot rack.

    python3 -m tools.eval_prediction
"""
from __future__ import annotations

import math

from sentinel.config import DEMO_WINDOW, TEMP_TAU_S, TICK_TRACE_S
from sentinel.predict import physics
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import THERMAL_THROTTLE

OUTCOME_HORIZON_TICKS = 20  # "throttles soon" = within the next N ticks


def _load_live_frames(window):
    from sentinel.engine import Engine
    return [f.to_dict() for f in Engine().run(window)]


def evaluate_discrimination(frames):
    """Run baseline vs upgraded over the SAME recorded frames (deterministic,
    fast — no re-simulation). Score each rack-tick: p = alert confidence (0 if
    no alert), y = 1 if that rack throttles within the next horizon."""
    # Ground truth per (rack, tick): does the rack throttle within horizon?
    n = len(frames)
    throttling = [[r["throttling_gpus"] for r in f["racks"]] for f in frames]
    rack_ids = [r["rack_id"] for r in frames[0]["racks"]]
    future_throttle = []  # [tick][rack_idx] -> 0/1
    for i in range(n):
        hi = min(n, i + 1 + OUTCOME_HORIZON_TICKS)
        future_throttle.append([1 if any(throttling[j][k] > 0 for j in range(i, hi)) else 0
                                 for k in range(len(rack_ids))])

    def run(label, estimator, eta_method):
        eng = PredictionEngine(estimator=estimator, eta_method=eta_method)  # no twin (offline)
        alerts = 0
        episodes = set()
        alerted_racks = set()
        sq_err = 0.0
        for i, f in enumerate(frames):
            fired = {p.target["id"]: p for p in eng.on_frame(f) if p.type == THERMAL_THROTTLE}
            for k, rid in enumerate(rack_ids):
                p = fired.get(rid)
                prob = p.confidence if p else 0.0
                y = future_throttle[i][k]
                sq_err += (prob - y) ** 2
                if p:
                    alerts += 1
                    episodes.add(p.prediction_id)
                    alerted_racks.add(rid)
        # A rack is a false positive if it was alerted but never throttled at all.
        ever_throttled = {rack_ids[k] for k in range(len(rack_ids)) if any(throttling[i][k] > 0 for i in range(n))}
        fp_racks = alerted_racks - ever_throttled
        brier = sq_err / (n * len(rack_ids))
        return {
            "label": label, "alerts": alerts, "episodes": len(episodes),
            "racks_alerted": len(alerted_racks), "false_positive_racks": len(fp_racks),
            "brier": brier,
        }

    return [
        run("baseline (EWMA+OLS, linear eta)", "ewma", "linear"),
        run("upgraded (Kalman, ODE eta)", "kalman", "ode"),
    ]


def evaluate_ode_vs_linear():
    """Ground truth = the RC thermal model itself (what generates telemetry).
    A GPU relaxing toward `target` with time-constant tau; we watch the first
    few ticks, estimate the slope, then predict the crossing of the 84 C line."""
    tau = TEMP_TAU_S
    line = 84.0
    scenarios = [
        ("heats to 86C (will throttle)", 78.0, 86.0),
        ("heats to 83C (never throttles)", 78.0, 83.0),
    ]
    rows = []
    for name, t0, target in scenarios:
        # True trajectory via Omar's exact recurrence, dt = one tick.
        alpha = 1.0 - math.exp(-TICK_TRACE_S / tau)
        temps = [t0]
        for _ in range(400):
            temps.append(temps[-1] + (target - temps[-1]) * alpha)
        # True crossing tick (or None).
        true_cross = next((k for k, T in enumerate(temps) if T >= line), None)
        true_eta = true_cross * TICK_TRACE_S if true_cross is not None else None
        # Decision point: 5 ticks in. Estimate slope from the last 3 samples.
        i = 5
        slope_s = (temps[i] - temps[i - 2]) / (2 * TICK_TRACE_S)
        cur = temps[i]
        linear_eta = (line - cur) / slope_s if slope_s > 0 else None
        ode_eta, _tgt = physics.eta_from_trend(cur, slope_s, line, tau)
        # Remaining true eta from the decision point.
        true_remaining = (true_eta - i * TICK_TRACE_S) if true_eta is not None else None
        rows.append((name, true_remaining, linear_eta, ode_eta))
    return rows


def evaluate_duration_prior():
    """Recompute the learned per-class runtime medians that weight queue
    pressure (config.DURATION_PRIOR_MIN). Keyed on frozen-contract fields only
    (whole-vs-fractional GPU + qos); duration = deletion - scheduled."""
    import statistics as st

    from sentinel.data.loader import load_pods
    buckets = {}
    for p in load_pods():
        if not p.is_gpu_pod:
            continue
        base = p.scheduled_time if p.scheduled_time is not None else p.creation_time
        cls = ("whole" if p.gpu_milli == 1000 else "frac", p.qos)
        buckets.setdefault(cls, []).append(max(0, p.deletion_time - base))
    return {cls: (len(ds), st.median(ds) / 60.0) for cls, ds in buckets.items()}


def evaluate_counterfactual(window):
    from sentinel.engine import Engine
    from sentinel.predict.twin import DigitalTwin, _summarize
    eng = Engine()
    pe = PredictionEngine(engine=eng)
    start = window[0]
    for frame, _preds in pe.attach(eng, (start, start + 4200)):
        if frame["t"] >= start + 4050:
            break
    state = eng.replayer.state
    heavy_running = [name for name, rk in state.pod_rack.items()
                     if rk == "rack-00" and eng.replayer.pods[name].heavy]
    twin = DigitalTwin(eng)
    base = twin.project("rack-00")
    rows = []
    for count in (0, 1, 3, 6, 10):
        fork = eng.fork()
        moved = sum(1 for nme in heavy_running[:count]
                    if fork.apply_action("MIGRATE_JOB", nme, "rack-18"))
        traj = twin._roll(fork, "rack-00")
        proj = _summarize("rack-00", eng.t, traj, twin.horizon_ticks * TICK_TRACE_S, twin.sustained_ticks)
        rows.append((moved, proj.peak_throttling, proj.end_throttling, proj.peak_temp_mean))
    return base, rows


def main() -> int:
    window = DEMO_WINDOW
    print("Loading live demo-window frames (one full replay)...")
    frames = _load_live_frames(window)
    print(f"  {len(frames)} frames, {len(frames[0]['racks'])} racks\n")

    print("=== 1. A/B discrimination + calibration (all racks, full window) ===")
    for m in evaluate_discrimination(frames):
        print(f"  {m['label']:34}  alerts={m['alerts']:5d}  episodes={m['episodes']:3d}  "
              f"racks_alerted={m['racks_alerted']}  false_pos_racks={m['false_positive_racks']}  "
              f"Brier={m['brier']:.4f}")

    print("\n=== 2. ODE-inversion vs naive linear ETA (ground truth = RC model) ===")
    for name, true_eta, lin, ode in evaluate_ode_vs_linear():
        def fmt(x):
            return "never" if x is None else f"{x/60:5.1f}min"
        lin_err = "n/a" if (lin is None or true_eta is None) else f"{abs(lin-true_eta)/60:.1f}min"
        ode_err = "n/a" if (ode is None or true_eta is None) else f"{abs(ode-true_eta)/60:.1f}min"
        print(f"  {name:32} true={fmt(true_eta)}  linear={fmt(lin)} (err {lin_err})  "
              f"ODE={fmt(ode)} (err {ode_err})")

    print("\n=== 2b. Learned duration prior by job class (weights FR-3 queue pressure) ===")
    for cls, (n, med) in sorted(evaluate_duration_prior().items(), key=lambda kv: -kv[1][0]):
        print(f"  {str(cls):24} n={n:5d}  median_runtime={med:6.1f} min")

    print("\n=== 3. Digital-twin counterfactual relief (migrate running heavy off rack-00) ===")
    base, rows = evaluate_counterfactual(window)
    print(f"  no-action baseline: peak_throttling={base.peak_throttling}  "
          f"end_throttling={base.end_throttling}  peak_temp={base.peak_temp_mean}C  "
          f"(horizon {base.horizon_s//60} min)")
    for moved, peak, end, ptemp in rows:
        print(f"  migrate {moved:2d} heavy -> rack-18 (T4): projected peak_throttling={peak:2d}  "
              f"end_throttling={end:2d}  peak_temp={ptemp}C")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
