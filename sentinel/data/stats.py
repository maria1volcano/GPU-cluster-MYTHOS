"""M0 stats gate: recompute PRD §2's grounding numbers from the CSVs and assert.

Run:  python3 -m sentinel.data.stats
Exits non-zero on any mismatch. This is the gate everything else builds on.
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter

from sentinel.config import TRACE_END_S
from sentinel.data.loader import load_nodes, load_pods

CHECKS: list[tuple[str, object, object]] = []


def check(label: str, actual: object, expected: object) -> None:
    CHECKS.append((label, actual, expected))
    mark = "PASS" if actual == expected else "FAIL"
    print(f"[{mark}] {label}: {actual}" + ("" if actual == expected else f" (expected {expected})"))


def main() -> int:
    nodes = load_nodes()
    pods = load_pods()

    # --- PRD §2.1 node inventory ---
    check("nodes", len(nodes), 1213)
    check("total GPUs", sum(n.gpu for n in nodes), 6212)
    model_nodes = Counter(n.model for n in nodes)
    model_gpus = Counter()
    for n in nodes:
        model_gpus[n.model] += n.gpu
    check("G2 nodes/gpus", (model_nodes["G2"], model_gpus["G2"]), (549, 4392))
    check("T4 nodes/gpus", (model_nodes["T4"], model_gpus["T4"]), (404, 842))
    check("P100 nodes/gpus", (model_nodes["P100"], model_gpus["P100"]), (134, 265))
    check("G3 nodes/gpus", (model_nodes["G3"], model_gpus["G3"]), (39, 312))
    check("V100M32 nodes/gpus", (model_nodes["V100M32"], model_gpus["V100M32"]), (30, 204))
    check("V100M16 nodes/gpus", (model_nodes["V100M16"], model_gpus["V100M16"]), (55, 195))
    check("A10 nodes/gpus", (model_nodes["A10"], model_gpus["A10"]), (2, 2))
    gpn = Counter(n.gpu for n in nodes)
    check("GPUs-per-node dist {1,2,4,8}", (gpn[1], gpn[2], gpn[4], gpn[8]), (24, 518, 54, 617))
    check("unique node sns", len({n.sn for n in nodes}), 1213)

    # --- PRD §2.2 pod stream ---
    check("pods", len(pods), 8152)
    gpu_pods = [p for p in pods if p.is_gpu_pod]
    check("GPU pods / CPU-only", (len(gpu_pods), len(pods) - len(gpu_pods)), (7064, 1088))
    ng = Counter(p.num_gpu for p in gpu_pods)
    check("num_gpu dist {1,2,4,8}", (ng[1], ng[2], ng[4], ng[8]), (6989, 16, 15, 44))
    frac = sum(1 for p in pods if 0 < p.gpu_milli < 1000)
    whole = sum(1 for p in pods if p.gpu_milli == 1000)
    check("gpu_milli fractional/whole", (frac, whole), (3078, 3986))
    check("multi-GPU pods all whole-GPU", all(p.gpu_milli == 1000 for p in pods if p.num_gpu > 1), True)
    qos = Counter(p.qos for p in pods)
    check("QoS LS/BE/Burstable/Guaranteed",
          (qos["LS"], qos["BE"], qos["Burstable"], qos["Guaranteed"]), (4647, 3398, 100, 7))
    ph = Counter(p.pod_phase for p in pods)
    check("phases Running/Failed/Pending/Succeeded",
          (ph["Running"], ph["Failed"], ph["Pending"], ph["Succeeded"]), (5193, 1870, 897, 192))
    check("gpu_spec empty everywhere", all(p.gpu_spec == "" for p in pods), True)
    check("pending (no scheduled_time)", sum(1 for p in pods if p.is_pending), 897)
    check("pending pods all phase=Pending", all(p.pod_phase == "Pending" for p in pods if p.is_pending), True)
    check("censored pods", sum(1 for p in pods if p.censored), 34)
    check("trace end", max(p.deletion_time for p in pods), TRACE_END_S)
    check("trace span days", round(TRACE_END_S / 86400, 1), 149.3)

    waits = [p.scheduled_time - p.creation_time for p in pods if not p.is_pending]
    check("sched wait median/max/instant",
          (statistics.median(waits), max(waits), sum(1 for w in waits if w == 0)), (2, 14330, 2046))
    check("sched wait mean ~61s", round(statistics.mean(waits)), 61)
    lifetimes = [p.deletion_time - p.scheduled_time for p in pods if not p.is_pending]
    check("job lifetime median s", statistics.median(lifetimes), 616)
    check("no negative intervals",
          all(w >= 0 for w in waits) and all(lt >= 0 for lt in lifetimes), True)

    # Peak concurrency (scheduled and not yet deleted), event sweep.
    events = []
    for p in pods:
        if not p.is_pending:
            events.append((p.scheduled_time, 1))
            events.append((p.deletion_time, -1))
    events.sort()
    cur = peak = peak_t = 0
    for t, d in events:
        cur += d
        if cur > peak:
            peak, peak_t = cur, t
    check("peak concurrent pods", peak, 56)
    check("peak near day 137", round(peak_t / 86400), 137)
    busiest = Counter(p.creation_time // 86400 for p in pods).most_common(1)[0]
    check("busiest day arrivals", busiest[1], 678)

    failures = [(label, a, e) for label, a, e in CHECKS if a != e]
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed.")
    if failures:
        print("STATS GATE FAILED", file=sys.stderr)
        return 1
    print("STATS GATE PASSED — data matches PRD §2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
