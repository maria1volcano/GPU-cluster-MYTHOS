# GPU Cluster Sentinel — Task Breakdown & Execution Plan

**Project:** GPU Cluster Sentinel ("Sentinel")
**Related docs:** [PRD.md](./PRD.md) · [DESIGN.md](./DESIGN.md)

This plan is **demo-first**: milestones **M0–M6** build the [North-Star 60-second demo](./PRD.md#5-north-star-demo-flow-the-60-second-flow) end-to-end. **M7+** layers features. Do the smallest thing that makes the demo real, then iterate.

**Legend:** ✅ required for the first end-to-end demo · ⏭️ later / post-demo.

**Grounding stats to keep visible while building** (from the real CSVs): 1,213 nodes · 6,212 GPUs · 7 GPU models (G2/T4/P100/G3/V100M32/V100M16/A10) · 8,152 pods (7,064 GPU pods) · ~43.6% of GPU pods share a GPU (`gpu_milli < 1000`) · trace span ≈ 149.3 days · peak 56 concurrent pods (~day 137).

---

## Milestone map

| Milestone | Goal | Demo-critical? |
|---|---|---|
| **M0** | Repo scaffold & data loading | ✅ |
| **M1** | Stream replayer (event-time replay over topology) | ✅ |
| **M2** | DCGM telemetry model + rack model | ✅ |
| **M3** | Prediction layer (throttle + bottleneck; instability) | ✅ (throttle+bottleneck) |
| **M4** | Agent via Crusoe Inference (recommend + justify) | ✅ |
| **M5** | Dashboard: live view + one-tap approve/override | ✅ |
| **M6** | Decision log + wire the full 60-sec demo | ✅ |
| **M7** | Learning from overrides | ⏭️ |
| **M8+** | Hardening & stretch (real DCGM, ML, multi-alert) | ⏭️ |

---

## M0 — Repo scaffold & data loading ✅
**Goal:** load both CSVs into clean in-memory models and confirm the real stats.

- [ ] Create project structure (`sentinel/` package: `data/`, `replay/`, `telemetry/`, `predict/`, `agent/`, `server/`, `web/`) and `requirements.txt`.
- [ ] `Node` + `Pod` dataclasses matching [DESIGN §3.1–3.2](./DESIGN.md#31-node).
- [ ] Loader for `openb_node_list_gpu_node.csv` (stdlib `csv`, no pandas required).
- [ ] Loader for `openb_pod_list_default.csv`; parse empty `scheduled_time` as `null` (Pending), handle `deletion_time == 12902960` as censored.
- [ ] `stats` command that prints node count, total GPUs, model breakdown, pod counts, fractional-GPU %, time span — assert they match [PRD §2](./PRD.md#2-grounding-data-real-numbers).
- [ ] Central `config` (RACK_SIZE, SPEEDUP, LEAD_TIME, RNG seed, demo window).

**Definition of done:** `stats` reproduces the real numbers (1,213 nodes / 6,212 GPUs / 8,152 pods / ~149.3 days).

## M1 — Stream replayer ✅
**Goal:** event-time replay of pods over the static topology, with speedup.

- [ ] Build merged, time-sorted event queue: `POD_CREATED` / `POD_SCHEDULED` / `POD_DELETED`.
- [ ] Virtual clock + tick loop; `SPEEDUP` mapping trace-seconds → wall-clock ([DESIGN §2.2](./DESIGN.md#22-stream-replayer)).
- [ ] Maintain live cluster state: placements, per-node/GPU load from `gpu_milli`, queue of pending pods.
- [ ] Play / pause / seek + jump-to-window (default: dense window near day-137 peak).
- [ ] `apply_action(MIGRATE_JOB, job, to_rack)` that overrides placement and recomputes load.
- [ ] Deterministic replay (seeded, reproducible).

**Definition of done:** replaying a chosen window shows pods arriving/scheduling/freeing and queue depth changing over time; peak concurrency is plausible (≤ ~56).

## M2 — DCGM telemetry model + rack model ✅
**Goal:** derive racks and synthesize believable per-GPU telemetry from load.

- [ ] Derived rack model: bucket nodes into racks (default 32/node ⇒ ~38 racks); option for model-homogeneous racks ([DESIGN §2.1](./DESIGN.md#derived-rack--topology-model)).
- [ ] `TelemetrySource` interface + `SimTelemetrySource.sample(gpu_id, t)` ([DESIGN §2.3](./DESIGN.md#dcgm-telemetry-model)).
- [ ] Per-model thermal/power profiles (TDP + idle/throttle temps) for G2/T4/P100/G3/V100M32/V100M16/A10.
- [ ] Temperature with thermal inertia + rack coupling; util from `gpu_milli`; power from util×TDP; clocks drop → throttle_reasons when temp/power exceed caps.
- [ ] Rare synthetic XID/ECC events weighted by instability score.
- [ ] Rack-level aggregates (util, temp) for the dashboard/prediction.
- [ ] Stub `DcgmTelemetrySource` documenting real field mapping (swap-in later).

**Definition of done:** as heavy jobs land on a dense (G2/G3) rack, its temperature visibly climbs toward the throttle threshold and clocks cap — the raw material for the demo alert.

## M3 — Prediction layer ✅ (throttle + bottleneck required; instability P1)
**Goal:** turn telemetry+queue trends into typed, explainable predictions.

- [ ] Rolling-window features: temp slope, power slope, util EWMA, thermal headroom, rack util, queue depth, queued heavy-job count ([DESIGN §2.4](./DESIGN.md#24-prediction-layer)).
- [ ] **Thermal-throttle** predictor: extrapolate temp trend → **time-to-throttle**; fire when `eta < LEAD_TIME`, with confidence + evidence. ✅
- [ ] **Scheduling-bottleneck** predictor: rising queue depth + heavy jobs converging on a hot/full rack. ✅
- [ ] **Node-instability** predictor from XID/ECC/clock signals. ⏭️(P1)
- [ ] Emit `Prediction` objects with `evidence[]` ([DESIGN §3.4](./DESIGN.md#34-prediction)).

**Definition of done:** during the demo window, the layer emits a `THERMAL_THROTTLE` prediction for a rack with `eta ≈ 8 min` and attached trend evidence, plus a matching bottleneck signal (3 heavy jobs queued).

## M4 — Agent via Crusoe Inference ✅
**Goal:** convert a prediction into a one-tap, plain-language recommendation.

- [ ] Deterministic **recommender**: candidate migration jobs + target-rack scoring by headroom (free capacity × thermal headroom × model fit) ([DESIGN §2.5](./DESIGN.md#25-agent-layer--crusoe-inference)).
- [ ] Crusoe Inference client (model/endpoint/auth from config); structured JSON prompt (prediction + validated candidates).
- [ ] Parse/validate LLM output → `Recommendation` (action + plain-language justification).
- [ ] **Guardrail:** LLM may only pick among validated candidates; reject unsafe placements.
- [ ] **Templated fallback** when Crusoe is unavailable/slow (timeout) — mark `source`.

**Definition of done:** given the M3 prediction, the agent returns *"Migrate job X → Rack 2"* with a one-sentence justification in < ~5 s, or a templated fallback.

## M5 — Dashboard: live view + one-tap approve/override ✅
**Goal:** the operator-facing surface.

- [ ] FastAPI backend + `WS /stream` broadcasting telemetry/prediction/KPI frames ([DESIGN §2.6](./DESIGN.md#26-dashboard--one-tap-override)).
- [ ] `POST /decision` endpoint (approve / override / dismiss).
- [ ] Frontend live cluster map (racks→nodes→GPUs colored by util/temp) with pods flowing.
- [ ] Alert card: plain language + **justifying trend chart** + Approve button + Override control.
- [ ] Live update: after a decision, backend applies action and pushes updated frames; alert clears.
- [ ] KPI strip (lead time, incidents averted, approval rate).

**Definition of done:** operator sees the live map, an alert card appears with a trend, one tap approves, and the map + trend update live with no CLI/raw counters.

## M6 — Decision log + wire the full 60-sec demo ✅
**Goal:** connect everything into the North-Star flow and log it.

- [ ] Append-only decision log (JSONL/SQLite) with schema from [DESIGN §3.6](./DESIGN.md#36-decision-log-entry).
- [ ] Log every surfaced prediction + recommendation + operator action + outcome + lead time + latency.
- [ ] Outcome resolution: after an approved migration, mark `AVERTED` when the projected throttle no longer occurs.
- [ ] End-to-end wiring: replayer → telemetry → prediction → agent → dashboard → decision → log, on the chosen demo window.
- [ ] Rehearse & tune the window/speedup so the flow lands in ~60 s.

**Definition of done:** a clean, repeatable ~60-second run: **live view → "Rack 7 throttles in ~8 min, migrate job X → Rack 2" → approve → incident averted → logged.** This is the demo.

---

## M7 — Learning from overrides ⏭️
**Goal:** overrides tune the prediction layer.

- [ ] Aggregate decision-log outcomes by alert type.
- [ ] Raise thresholds / shorten `LEAD_TIME` for repeatedly-overridden alert classes (fewer false positives).
- [ ] Lower thresholds when incidents occur without an alert (fewer misses).
- [ ] Reinforce settings when approvals avert incidents.
- [ ] Show approval-rate / false-positive trends on the KPI strip.

**Definition of done:** demonstrably, repeated overrides of an early throttle alert shift its threshold and reduce that alert's false-positive rate on replay.

## M8+ — Hardening & stretch ⏭️
- [ ] Swap `SimTelemetrySource` → real **NVIDIA DCGM** feed via the same interface.
- [ ] ML time-to-event predictor trained on logged {features → outcome}.
- [ ] Multiple concurrent alerts + prioritization; multi-rack planning.
- [ ] Node-instability model maturation (XID/ECC storms → failure).
- [ ] Historical analytics, replay scrubber, cost/GPU-hour accounting.
- [ ] Reliability: reconnect logic, LLM caching, load/perf tests against [NFRs](./PRD.md#7-non-functional-requirements).

---

## First-demo checklist (must all be true)
- [x] M0 data loads and matches real stats.
- [x] M1 replayer streams the demo window deterministically.
- [x] M2 a dense rack heats toward throttle under load.
- [x] M3 emits a `THERMAL_THROTTLE` (+bottleneck) prediction with ~8-min lead time.
- [x] M4 agent produces the migrate recommendation + justification (or fallback).
- [ ] M5 dashboard shows it and accepts a one-tap approve. *(no UI yet — see below)*
- [x] M6 action averts the incident live and writes a decision-log entry. *(CLI/backend only, no dashboard)*

---

## Implementation status — `sentinel/` backend (this branch)

This branch implements the **backend/agent side** of the loop end-to-end
(no dashboard UI yet) and specifically finishes **FR-5, FR-9, and FR-11**
from the PRD, plus the M0–M4 and M6 groundwork those requirements depend
on:

- `sentinel/data/loader.py` — M0 CSV loaders + `stats()`, verified against
  the exact PRD §2 numbers in `tests/test_loader_and_topology.py`.
- `sentinel/topology.py` — M2 derived, homogeneous rack model.
- `sentinel/replay.py` — M1 event-time `ClusterSimulator` (real
  `creation_time`/`scheduled_time`/`deletion_time` events drive placement
  and load; no wall-clock `SPEEDUP` mapping or play/pause/seek UI yet —
  that's FR-10, still open) plus `apply_action(MIGRATE_JOB, ...)` and a
  documented **stress-scenario trigger** (`set_stress_override` /
  `force_place_on_rack`) so the demo doesn't depend on organic congestion
  landing in a particular window.
- `sentinel/telemetry.py` — M2 synthesized per-GPU DCGM-style telemetry
  (`SimTelemetrySource`) + the `DcgmTelemetrySource` stub seam.
- `sentinel/predict/engine.py` — M3 thermal-throttle + scheduling-bottleneck
  predictors, each emitting `Prediction.evidence` (**FR-11**: every alert
  carries the numeric trend/threshold that fired it). Node-instability
  (FR-4) is still open.
- `sentinel/agent/` — M4 deterministic `Recommender` (capacity-validated
  candidates only) + `CrusoeClient` (Crusoe Managed Inference,
  OpenAI-compatible) + `Agent.recommend()` producing the one-tap
  `Recommendation` card (**FR-5**), with a templated fallback when the LLM
  is unavailable/invalid (NFR-5).
- `sentinel/decision_log.py` — M6 append-only JSONL decision log (FR-8).
- `sentinel/learning.py` — **FR-9**: `OverrideLearner` aggregates recent
  decision-log outcomes per alert class and tightens/relaxes that class's
  thresholds, persisted to `sentinel_state.json`.
- `sentinel/demo.py` (`python -m sentinel.demo`) — wires all of the above
  into one script that prints the FR-5 card, the FR-11 evidence trail, and
  a scripted FR-9 override→learn→relax sequence.
- `tests/` — 22 pytest cases covering loaders/topology, telemetry, both
  predictors' fire/no-fire evidence, the recommender's capacity guardrail,
  agent LLM-choice validation + fallback, the decision log, and the
  learner's tighten/floor/relax behavior.

**Still open (unchanged from before this branch):** M5 dashboard/FR-1,
FR-6/7 (dashboard-side approve/override UI — the backend primitives
`apply_action`/`DecisionLog` they'd call already exist), FR-10 replay
controls, FR-4 node instability *prediction* (telemetry already emits the
XID/ECC signal it would consume).
