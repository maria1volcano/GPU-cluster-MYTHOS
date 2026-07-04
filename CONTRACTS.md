# Sentinel data & telemetry contracts (M0–M2 → M3/M4/M5)

**Owner:** Omar (data ingestion, replay, telemetry).
**Status: FROZEN at schema v1.** Key names and semantics won't change without a version bump and a heads-up. New *optional* keys may be added — consumers must tolerate unknown keys.

Canonical definitions in code: `sentinel/telemetry/sample.py`.
Real sample data: [`fixtures/`](./fixtures/) (see [Fixtures](#fixtures) below).

---

## 1. The TelemetryFrame — what you consume every tick

One frame = one tick of cluster truth. **Transport-agnostic:** the identical JSON object is
- pushed over `WS /stream` (DESIGN §4), interleaved with `"prediction"` (M3) and `"kpi"` (M6) frames — demux on `type`;
- returned by `GET /api/cluster/state` (latest frame) for the REST/mock-mode path the frontend README describes.

```jsonc
{
  "type": "telemetry",        // frame demux key: "telemetry" | "prediction" | "kpi"
  "v": 1,                     // schema version
  "tick": 137,                // tick counter since window start
  "t": 12824110,              // trace-seconds (event time, NOT wall clock)
  "trace_day": 148.427,
  "samples": [ GpuTelemetrySample, ... ],   // sorted by gpu_id — ONLY interesting GPUs, see §2
  "racks":   [ RackAggregate, ... ],        // ALWAYS all 42 racks, rack index order
  "queue":   [ QueuedPodInfo, ... ],        // pending pods, creation order
  "cluster": {
    "active_pods": 51,        // scheduled & not yet deleted (GPU + CPU pods)
    "pending_pods": 7,        // created, not scheduled, not deleted
    "pending_heavy": 6,       // pending pods with gpu_demand >= 1.0
    "gpu_demand": 114.6,      // GPU-equivalents currently placed cluster-wide
    "capacity_gpus": 6212,
    "active_gpus": 121,       // GPUs with any load
    "throttling_gpus": 3,     // GPUs with capped clocks right now
    "events_applied": 16204   // cumulative replay events since window start
  }
}
```

Cadence: 1 tick = `TICK_TRACE_S` (30) trace-seconds. Wall pacing = `TICK_TRACE_S / SPEEDUP` (config; M5 may expose a live speed control).

## 2. GpuTelemetrySample (DESIGN §3.3, verbatim keys)

```json
{
  "gpu_id": "openb-node-0233/gpu-3",
  "node_sn": "openb-node-0233",
  "rack_id": "rack-07",
  "model": "G2",
  "t": 12824110,
  "util": 0.94,
  "temp_c": 81.5,
  "power_w": 292.0,
  "sm_clock_mhz": 1230,
  "mem_clock_mhz": 877,
  "mem_used_mib": 27853,
  "throttle_reasons": ["SW_THERMAL"],
  "xid_errors": 0,
  "ecc_errors": {"volatile": 0, "aggregate": 3}
}
```

Semantics:
- `util` ∈ [0,1] is **driven by real trace load** (sum of `gpu_milli` placed on that GPU / 1000, plus tiny seeded noise). Temperature, power, and clocks are synthesized *from* that real load (DESIGN §2.3).
- **Throttling signal:** `sm_clock_mhz` below the model's base clock ⇔ `throttle_reasons` non-empty. `SW_THERMAL` at the throttle temperature, `HW_THERMAL` when well past it, `SW_POWER_CAP` on power-cap hits.
- `xid_errors` is a **count for this tick** (rare Poisson events); `ecc_errors.volatile` likewise, while `ecc_errors.aggregate` accumulates over the run. These feed the P1 node-instability predictor.
- **Sparse inclusion rule:** `samples` contains only GPUs that are interesting — any load, temperature meaningfully above the model's idle, throttling, or errors. **A GPU absent from `samples` is idle at ambient temperature** — treat missing as idle, never as an error. (`racks` is always complete, so the map never has holes.)

Per-model constants (idle/throttle temps, TDP, base clocks): `sentinel/telemetry/profiles.py`.

## 3. RackAggregate — what prediction should trend on

```json
{
  "rack_id": "rack-07",
  "gpu_model": "G2",
  "num_nodes": 32,
  "capacity_gpus": 256,
  "gpu_demand": 98.47,
  "util": 0.3846,
  "active_gpus": 104,
  "temp_c_mean_active": 79.8,
  "temp_c_max": 85.2,
  "power_w_total": 31240.5,
  "throttling_gpus": 2,
  "active_pods": 38,
  "queued_pods": 6,
  "queued_heavy": 6
}
```

- Bin-packing concentrates load on a few nodes, so a mean over all 256 GPUs would dilute the thermal signal. **Trend on `temp_c_mean_active` (recommended) or `temp_c_max`**; `util` = `gpu_demand / capacity_gpus` is the occupancy signal.
- `queued_pods` / `queued_heavy`: pending pods whose **placement preview** targets this rack (the trace has no per-pod rack affinity; the deterministic bin-packing policy defines "would land here"). This is the "3 heavy jobs queued on Rack 7" signal for the bottleneck predictor.
- Throttle threshold per rack = its model's `throttle_temp` (homogeneous racks, so one line per rack chart).

## 4. QueuedPodInfo

```json
{
  "name": "openb-pod-7712",
  "num_gpu": 8,
  "gpu_milli": 1000,
  "gpu_demand": 8.0,
  "qos": "BE",
  "heavy": true,
  "waiting_s": 312,
  "target_rack": "rack-07"
}
```

`heavy` ⇔ `gpu_demand >= 1.0`. `target_rack` may be `null` if nothing currently fits.

## 5. Interfaces (function-level, DESIGN §4)

| Interface | Signature | Notes |
|---|---|---|
| Tick hook (→ M3) | `Engine.run(window, on_tick=cb)` calls `cb(t, state, frame)` | `frame` is the `TelemetryFrame`; `state` is the live `ClusterState` (placements, queue) for anything not in the frame |
| Telemetry seam | `TelemetrySource.sample(gpu_id, t) -> GpuTelemetrySample` | `SimTelemetrySource` now; `DcgmTelemetrySource` (stub, field mapping documented) swaps in later with zero prediction changes |
| Operator action (M5/M6 → replayer) | `Replayer.apply_action("MIGRATE_JOB", job_id, to_rack)` | Frees the job's current placement (or dequeues it if pending) and places it on `to_rack`; both racks' load and telemetry shift from the next tick |
| Replay controls | `Engine.seek(t)`, `Engine.tick()`, `DEMO_WINDOW` in `sentinel/config.py` | `seek` warm-starts: state is fast-forwarded and temperatures initialize at steady-state for the load at `t` |

## 6. Determinism guarantee

Same seed (`SEED=42`) + same window + same tick ⇒ **bit-identical frame sequence**, across processes and machines. Noise and rare events are keyed on `(seed, gpu_id, t)`, not on call order. Rehearse the demo once, it replays forever.

## 7. Fixtures

- `fixtures/telemetry_frame.golden.json` — one full frame from the demo window (day 148.4x), at a moment with heavy jobs queued and a G2 rack heating. Regenerate: `python3 -m sentinel.telemetry.fixture`.
- `fixtures/telemetry_frames.sample.jsonl` — a thinned frame series across the demo window (1 frame per 10 ticks) so charts/trend logic can be built against a real trajectory.

Build against the fixtures now; the live stream will match them shape-for-shape.

## 8. What is real vs. synthesized (say this right in the demo)

Real from the trace: node inventory & GPU models, every pod's arrival/schedule/free timing, `gpu_milli`/`num_gpu` demand, QoS, queue pressure. Synthesized deterministically **from that real load**: placement (the trace has no node-assignment column — seeded bin-packing), rack topology (no rack column — model-homogeneous racks of 32), and DCGM telemetry (temp/power/clocks as a function of real load; throttling *emerges* when real heavy jobs pile onto a dense rack — nothing is scripted).
