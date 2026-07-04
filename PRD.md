# GPU Cluster Sentinel — Product Requirements Document (PRD)

> **One-line pitch:** *Sentinel is an AI operations agent that watches a live GPU cluster, predicts thermal throttling, node instability, and scheduling bottlenecks before they hit, and gives a non-technical operator one-tap recommendations to avert incidents — learning from every override.*

**Project name:** GPU Cluster Sentinel (**"Sentinel"** for short)
**Status:** Draft v1 (demo-driven)
**Related docs:** [DESIGN.md](./DESIGN.md) · [TASKS.md](./TASKS.md)

---

## 1. Problem statement

Modern GPU clusters are expensive, dense, and thermally constrained. When something goes wrong, the failure modes are quiet until they are catastrophic:

- **Thermal throttling** silently caps SM clocks when a GPU (or a whole rack) runs too hot, so jobs slow down 15–40% while still reporting "healthy." Nobody notices until a training run misses its deadline.
- **Node instability** (XID errors, ECC error storms, power excursions, falling clocks) precedes hard node failures. A single unstable node can take down long-running distributed jobs and force costly restarts.
- **Scheduling bottlenecks** build up as queues deepen on hot racks while headroom sits idle elsewhere. In a GPU-sharing cluster, a handful of heavy jobs landing on the same rack can stall dozens of queued pods.

Today these problems are handled **reactively** by scarce, highly-technical SREs reading dashboards full of raw DCGM counters. The people who actually operate the floor — shift operators, ops leads — cannot act on `XID 79` or `SM_CLOCK 1230 MHz` without escalating. By the time an expert is paged, the incident has already cost GPU-hours.

**Cost of inaction (why proactive matters):** on this cluster the realistic exposure is large. The reference cluster below has **6,212 GPUs across 1,213 nodes**. Even a 10-minute throttling event across a single 32-node rack (~200 GPUs) at partial slowdown wastes tens of GPU-hours; a single unstable node killing a multi-GPU distributed job can waste thousands. Proactive, explainable intervention — minutes of lead time plus a safe one-tap action — is the difference between "incident averted" and "post-mortem."

## 2. Grounding data (real numbers)

Sentinel is built and demoed on the **Alibaba `openb` GPU-sharing / Kubernetes scheduling trace** already in this repo. All figures below were extracted directly from the CSVs.

### 2.1 Cluster inventory — `openb_node_list_gpu_node.csv`
Columns: `sn, cpu_milli, memory_mib, gpu, model`.

- **1,213 nodes**, **6,212 total physical GPUs**.
- **GPU model distribution:**

| Model | Nodes | Total GPUs | GPUs/node seen |
|---|---:|---:|---|
| G2 | 549 | 4,392 | 8 |
| T4 | 404 | 842 | 2 (387), 4 (17) |
| P100 | 134 | 265 | 1 (3), 2 (131) |
| G3 | 39 | 312 | 8 |
| V100M32 | 30 | 204 | 4 (9), 8 (21) |
| V100M16 | 55 | 195 | 1 (19), 4 (28), 8 (8) |
| A10 | 2 | 2 | 1 |
| **Total** | **1,213** | **6,212** | — |

- **GPUs per node:** 1 GPU (24 nodes), 2 GPU (518), 4 GPU (54), 8 GPU (617).
- **CPU per node (`cpu_milli`):** ranges 8,000–128,000; most common are 96,000 (587 nodes) and 104,000 (387 nodes).
- **Memory per node (`memory_mib`):** ranges 32,768–1,048,576 MiB; most common are 393,216 MiB / 384 GiB (566 nodes) and 524,288 MiB / 512 GiB (387 nodes).
- **No rack/topology column** exists in the trace — rack topology is **derived** by Sentinel (see [DESIGN.md](./DESIGN.md#derived-rack--topology-model); default grouping ≈ 32 nodes/rack ⇒ ~38 racks).

### 2.2 Job / pod stream — `openb_pod_list_default.csv`
Columns: `name, cpu_milli, memory_mib, num_gpu, gpu_milli, gpu_spec, qos, pod_phase, creation_time, deletion_time, scheduled_time`. Times are relative seconds.

- **8,152 pods** total; **7,064 request GPUs** (`num_gpu > 0`), 1,088 are CPU-only.
- **GPU count per pod:** 1 GPU (6,989), 2 GPU (16), 4 GPU (15), 8 GPU (44) → **75 multi-GPU jobs**.
- **GPU sharing is the headline signal:** `gpu_milli` (thousandths of one GPU) shows **3,078 pods request a *fraction* of a GPU** (`0 < gpu_milli < 1000`, e.g. 810, 470, 320, 650…), **3,986 request whole GPUs** (`gpu_milli = 1000`), and 1,088 request none. **~43.6% of GPU pods share a physical GPU** — this is precisely why the openb "fragmentation" trace is a strong stressor for scheduling-bottleneck prediction.
- **QoS mix:** LS 4,647 · BE 3,398 · Burstable 100 · Guaranteed 7.
- **Pod phases:** Running 5,193 · Failed 1,870 · Pending 897 · Succeeded 192. (897 Pending pods never received a `scheduled_time` — real queue pressure.)
- **`gpu_spec` is empty for all rows** (no per-pod model affinity in this trace).
- **Time span:** creation `0 … 12,901,761 s`, deletion up to `12,902,960 s` → the trace spans **≈ 12,902,960 s ≈ 149.3 days**.
- **Scheduling wait (`scheduled − creation`):** median **2 s**, mean **61 s**, max **14,330 s (~4 h)**; 2,046 pods scheduled instantly.
- **Job lifetime (`deletion − scheduled`):** median **616 s (~10 min)**, mean **~8 h**, max **~145 days** (34 long-lived pods are still running at trace end).
- **Concurrency:** peak **56 concurrently-scheduled pods**, near day 137; arrivals are bursty and back-loaded (busiest single day has 678 arrivals).

> These numbers size the replay window, speedup factor, and the plausible magnitude of "3 heavy jobs queued on Rack 7." See [DESIGN.md](./DESIGN.md#22-stream-replayer).

### 2.3 Signals that must be synthesized
The openb trace has **no telemetry** — no temperature, power, SM clocks, XID/ECC errors, or throttle reasons, and no rack topology. Sentinel therefore **synthesizes DCGM-style per-GPU telemetry** as a function of scheduled load (with a clean seam so a real NVIDIA DCGM feed can replace it later), and **derives rack topology** from node grouping. What is real vs. synthesized is spelled out in [DESIGN.md](./DESIGN.md#telemetry-real-vs-synthesized).

## 3. Target users & personas

**Primary user — the non-technical cluster operator.** Runs the operations floor on shift. Comfortable with dashboards and clear yes/no decisions; *not* comfortable interpreting raw GPU counters or writing `kubectl`. Needs to keep the cluster healthy and escalate only when necessary.

| Persona | Role | Goal | Pain today | What Sentinel gives them |
|---|---|---|---|---|
| **Riya — Shift Operator** | Watches the floor 24/7 | Keep utilization high, avoid incidents | Can't read DCGM counters; pages an SRE for everything | Plain-language alerts + one-tap safe actions |
| **Marco — Ops Lead** | Owns SLAs & cost | Fewer incidents, provable lead time | No early warning; only post-mortems | KPIs: lead time, incidents averted, approval rate |
| **Dana — Platform SRE** *(secondary)* | Owns the cluster infra | Trust automation, keep guardrails | Alert fatigue, manual migrations | Auditable decision log, override-driven tuning |

## 4. Goals & non-goals

### Goals
- **G1.** Predict thermal throttling, node instability, and scheduling bottlenecks with **actionable lead time** (minutes ahead), before impact.
- **G2.** Turn each prediction into **one plain-language recommendation** a non-technical operator can approve or override in one tap, with the **telemetry trend that justifies it** shown inline.
- **G3.** **Learn from overrides** — every approve/override is logged and feeds back to tune thresholds/models.
- **G4.** Deliver a crisp **60-second North-Star demo** end-to-end on the real openb trace.

### Non-goals (v1)
- **NG1.** Not a general cluster manager or a replacement for Kubernetes/the real scheduler — Sentinel *recommends*, it does not silently re-schedule production.
- **NG2.** No control of real hardware in v1 (actions apply to the simulated/replayed cluster state).
- **NG3.** No multi-cluster / multi-tenant billing.
- **NG4.** No heavyweight/opaque deep-learning models in v1 — explainability first (thresholds + trend extrapolation), ML later.
- **NG5.** Not building a full DCGM collector in v1 — telemetry is synthesized behind a swappable interface.

## 5. North-Star demo flow (the 60-second flow)

> This single flow drives the whole build. Everything in v1 exists to make this work end-to-end.

1. **Live view (0:00).** Operator watches the Sentinel dashboard: a live cluster map (racks, nodes, GPUs) replaying the real openb stream — pods arriving, being scheduled, and freeing over derived rack topology; per-rack utilization and temperature trending in real time.
2. **Prediction (0:10).** The prediction layer detects a rising thermal + queue trend on **Rack 7**: temperature climbing, thermal headroom shrinking, and **3 heavy GPU jobs queued** for that rack. It estimates **time-to-throttle ≈ 8 minutes**.
3. **Recommendation (0:20).** The agent (Crusoe Inference LLM) converts the prediction into a plain-language, one-tap card:
   > **"Rack 7 will likely throttle in ~8 min — 3 heavy jobs queued. Suggest migrating job `openb-pod-XXXX` to Rack 2 (has headroom, cooler). Approve?"**
   The card shows the **justifying trend** (Rack 7 temp/util rising toward the throttle line; Rack 2 flat with headroom).
4. **One-tap decision (0:35).** Operator taps **Approve** (or **Override** with an alternative). No CLI, no counters.
5. **Incident averted (0:45).** Dashboard updates live: the job moves to Rack 2, Rack 7's projected temperature curve bends back below the throttle threshold, the alert clears.
6. **Logged & learns (0:55).** Sentinel writes a **decision-log entry** (prediction, recommendation, action = approve, outcome = averted). Overrides feed back to tune thresholds.

**Definition of success for the demo:** a clean, believable, ~60-second loop of *predict → explain → one-tap → avert → log*, driven by real openb data.

## 6. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | **Live dashboard**: real-time cluster map (racks/nodes/GPUs) + per-rack util/temp trends, driven by the replayed openb stream | P0 |
| FR-2 | **Thermal-throttle prediction**: per-GPU/per-rack time-to-throttle estimate with a confidence + the trend evidence | P0 |
| FR-3 | **Scheduling-bottleneck prediction**: detect deepening queues / heavy jobs converging on a hot rack | P0 |
| FR-4 | **Node-instability prediction**: flag nodes trending toward failure (synthetic XID/ECC/power/clock signals) | P1 |
| FR-5 | **One-tap recommendation**: plain-language card with a concrete action (e.g., migrate job X → Rack Y) + justifying trend | P0 |
| FR-6 | **Approve / Override**: one-tap approve, or override with an alternative target; both captured | P0 |
| FR-7 | **Live update after action**: dashboard + projected trend reflect the action immediately; alert clears if resolved | P0 |
| FR-8 | **Decision log**: append-only log of {prediction, recommendation, action, outcome, timestamps} | P0 |
| FR-9 | **Learning from overrides**: overrides adjust thresholds/model weights over time | P1 |
| FR-10 | **Replay controls**: play/pause, speedup factor, jump-to-window (pick a dense segment of the trace) | P1 |
| FR-11 | **Explainability**: every alert links to the raw trend that produced it | P0 |

## 7. Non-functional requirements

- **NFR-1 (Prediction latency):** end-to-end tick → prediction → recommendation card in **< 2 s** at demo speedup; agent (LLM) response target **< 5 s**.
- **NFR-2 (Lead time):** predictions must fire with enough headroom to act — target **≥ 5 min** simulated lead time before the modeled throttle/failure.
- **NFR-3 (Replay fidelity):** event-time replay must preserve the real ordering and relative timing of `creation_time → scheduled_time → deletion_time`; speedup must be uniform and deterministic (reproducible demo).
- **NFR-4 (Explainability):** no black-box alerts — each recommendation carries the numeric trend + threshold that triggered it, understandable by a non-technical operator.
- **NFR-5 (Reliability):** the pipeline must degrade gracefully — if the LLM is unavailable, fall back to a templated recommendation from the prediction layer.
- **NFR-6 (Determinism/repeatability):** fixed random seed for synthesized telemetry so the demo is reproducible.
- **NFR-7 (Auditability):** the decision log is append-only and complete (every prediction that surfaced a recommendation is logged with its outcome).

## 8. Success metrics / KPIs

| KPI | Definition | v1 target |
|---|---|---|
| **Prediction lead time** | Median minutes between alert and modeled incident | ≥ 5 min |
| **Incidents averted** | Predicted incidents resolved by an approved action | ≥ 80% of surfaced incidents in demo |
| **Operator approval rate** | Approvals ÷ total recommendations | Track (baseline for learning) |
| **False-positive rate** | Alerts with no would-be incident ÷ total alerts | < 25% in v1, decreasing |
| **Time-to-decision** | Alert surfaced → operator tap | < 15 s (one-tap UX) |
| **Explainability coverage** | Recommendations with a visible justifying trend | 100% |

## 9. Scope

### v1 (the single North-Star demo)
- Data loading of both CSVs; event-time **stream replayer** with speedup.
- **Synthesized DCGM telemetry** + **derived rack topology**.
- **Thermal-throttle** time-to-event prediction + **scheduling-bottleneck** detection (P0). Node instability included if time permits (P1).
- **Agent recommendation** via Crusoe Inference (plain-language migrate-job suggestion + justification), with a templated fallback.
- **Dashboard** with live view + **one-tap approve/override** + live post-action update.
- **Decision log** written for the demo flow.

### Later (post-demo)
- Full node-instability model; richer failure signals.
- **Learning from overrides** loop maturing into online threshold/model tuning.
- Real **DCGM** feed swapped in behind the telemetry interface.
- Multiple concurrent recommendations, alert prioritization, multi-rack planning.
- Historical analytics, replay scrubber, cost accounting.

## 10. Assumptions & open questions

**Assumptions**
- The openb trace is a faithful stand-in for a live cluster's job/scheduling dynamics.
- Synthesized telemetry is "good enough" to demonstrate believable throttle/instability trends; realism is a modeling choice, not ground truth.
- Rack topology can be reasonably derived by grouping nodes (default ~32/rack).
- Crusoe Inference provides a chat/completions-style LLM endpoint for the agent layer.

**Open questions (for the user to resolve)**
1. **Replay window & speed:** replay the full 149.3-day trace at a large speedup, or pre-select a dense segment (e.g., near the day-137 concurrency peak / the 678-arrival busiest day) for the demo? Suggested default: pick a dense window and replay at a configurable factor.
2. **Rack grouping rule:** group by contiguous `sn` index (default 32/rack) or group *within* GPU model (so racks are homogeneous)? Homogeneous racks make thermal modeling cleaner.
3. **Telemetry realism bar:** how physically detailed should the thermal model be (simple load→temp curve vs. thermal mass + neighbor coupling)? Simple is fine for the demo.
4. **Crusoe Inference specifics:** which model/endpoint/auth, and expected latency/cost budget per recommendation?
5. **Action semantics:** does "migrate job X" simply update simulated placement, or should it emit a mock `kubectl`/scheduler action for realism?
6. **What counts as a true incident** for KPI scoring (throttle onset? sustained N seconds of capped clocks?).

## 11. Glossary

- **DCGM** — NVIDIA Data Center GPU Manager; source of per-GPU telemetry (temp, power, util, SM clocks, memory, XID, ECC, throttle reasons).
- **XID error** — NVIDIA driver error code indicating a GPU fault; a leading indicator of instability.
- **ECC error** — memory error-correction event; storms indicate failing memory/instability.
- **Throttle reason** — DCGM flag explaining why clocks are capped (thermal, power, etc.).
- **`gpu_milli`** — GPU request in thousandths of a GPU (1000 = one whole GPU); `< 1000` = GPU sharing.
- **QoS** — quality-of-service class (LS = latency-sensitive, BE = best-effort, Burstable, Guaranteed).
- **openb** — the Alibaba clusterdata GPU-sharing / fragmentation benchmark trace used here.
- **Rack (derived)** — a logical group of nodes Sentinel infers for topology/thermal modeling (trace has no rack field).
- **Time-to-throttle** — predicted seconds/minutes until a GPU/rack crosses its throttle threshold.
- **North-Star demo** — the 60-second predict→explain→one-tap→avert→log flow this product is built around.
