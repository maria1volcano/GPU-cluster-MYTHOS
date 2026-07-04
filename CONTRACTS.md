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

All example values below are copied verbatim from `fixtures/telemetry_frame.golden.json` (tick 137 of the demo window — the queue-peak moment).

```jsonc
{
  "type": "telemetry",        // frame demux key: "telemetry" | "prediction" | "kpi"
  "v": 1,                     // schema version
  "tick": 137,                // tick counter since window start
  "t": 12824140,              // trace-seconds (event time, NOT wall clock)
  "trace_day": 148.428,
  "samples": [ GpuTelemetrySample, ... ],   // sorted by gpu_id — ONLY interesting GPUs, see §2
  "racks":   [ RackAggregate, ... ],        // ALWAYS all 42 racks, rack index order
  "queue":   [ QueuedPodInfo, ... ],        // pending pods, creation order
  "cluster": {
    "active_pods": 44,        // scheduled & not yet deleted (GPU + CPU pods)
    "pending_pods": 8,        // created, not scheduled, not deleted
    "pending_heavy": 8,       // pending pods with gpu_demand >= 1.0
    "gpu_demand": 48.68,      // GPU-equivalents currently placed cluster-wide
    "capacity_gpus": 6212,
    "active_gpus": 49,        // GPUs with any load
    "throttling_gpus": 45,    // GPUs with capped clocks right now
    "events_applied": 108     // replay events applied since window start
  }
}
```

Cadence: 1 tick = `TICK_TRACE_S` (30) trace-seconds. Wall pacing = `TICK_TRACE_S / SPEEDUP` (config; M5 may expose a live speed control).

## 2. GpuTelemetrySample (DESIGN §3.3, verbatim keys)

```json
{
  "gpu_id": "openb-node-0026/gpu-0",
  "node_sn": "openb-node-0026",
  "rack_id": "rack-00",
  "model": "G2",
  "t": 12824140,
  "util": 1.0,
  "temp_c": 85.7,
  "power_w": 300.0,
  "sm_clock_mhz": 1364,
  "mem_clock_mhz": 877,
  "mem_used_mib": 13926,
  "throttle_reasons": ["SW_THERMAL", "SW_POWER_CAP"],
  "xid_errors": 0,
  "ecc_errors": {"volatile": 0, "aggregate": 0}
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
  "rack_id": "rack-00",
  "gpu_model": "G2",
  "num_nodes": 32,
  "capacity_gpus": 256,
  "gpu_demand": 48.68,
  "util": 0.1902,
  "active_gpus": 49,
  "temp_c_mean_active": 84.0,
  "temp_c_max": 86.0,
  "power_w_total": 23273.2,
  "throttling_gpus": 45,
  "active_pods": 42,
  "queued_pods": 8,
  "queued_heavy": 8
}
```

- Bin-packing concentrates load on a few nodes, so a mean over all 256 GPUs would dilute the thermal signal. **Trend on `temp_c_mean_active` (recommended) or `temp_c_max`**; `util` = `gpu_demand / capacity_gpus` is the occupancy signal.
- **Caveat for trend logic:** `temp_c_mean_active` is composition-sensitive — when a burst of new jobs lands, the fresh (still-cool, climbing) GPUs briefly *dilute* the mean before driving it up. That dip-then-climb is the alert signature, not noise. `throttling_gpus` and `queued_heavy` are the complementary evidence; smooth the mean (EWMA) before extrapolating.
- `queued_pods` / `queued_heavy`: pending pods whose **placement preview** targets this rack (the trace has no per-pod rack affinity; the deterministic bin-packing policy defines "would land here"). This is the "3 heavy jobs queued on Rack 7" signal for the bottleneck predictor.
- Throttle threshold per rack = its model's `throttle_temp` (homogeneous racks, so one line per rack chart).

## 4. QueuedPodInfo

```json
{
  "name": "openb-pod-7671",
  "num_gpu": 1,
  "gpu_milli": 1000,
  "gpu_demand": 1.0,
  "qos": "LS",
  "heavy": true,
  "waiting_s": 132,
  "target_rack": "rack-00"
}
```

`heavy` ⇔ `gpu_demand >= 1.0`. `target_rack` may be `null` if nothing currently fits.

## 5. Interfaces (function-level, DESIGN §4)

| Interface | Signature | Notes |
|---|---|---|
| Tick loop (→ M3/M5) | `for frame in engine.run(window):` — yields one `TelemetryFrame` per tick. Callback form: `engine.run(window, on_tick=cb)` calls `cb(t, state, frame)` eagerly | `frame` is the `TelemetryFrame`; `state` is the live `ClusterState` (placements, queue) for anything not in the frame |
| Telemetry seam | `TelemetrySource.sample(gpu_id, t) -> GpuTelemetrySample` | Returns the sample as of the last completed tick (its `.t` says when). `SimTelemetrySource` now; `DcgmTelemetrySource` (stub, field mapping documented) swaps in later with zero prediction changes |
| Operator action (M5/M6 → replayer) | `Engine.apply_action("MIGRATE_JOB", job_id, to_rack) -> bool` | Guardrail seam: ANY invalid input (unknown job/rack, finished job, target without capacity) returns `False` — it never raises. On success the job (queued or running) is placed on `to_rack` and both racks shift from the next tick |
| Replay controls | `Engine.seek(t)`, `Engine.tick()`, `DEMO_WINDOW` in `sentinel/config.py` | `seek` warm-starts deterministically: state fast-forwards to `t − 30min`, temperatures initialize by actual load age, then real events replay to `t` so thermal state is fully evolved |

## 6. Determinism guarantee

Same seed (`SEED=42`) + same window + same tick ⇒ **bit-identical frame sequence**, across processes and machines. Noise and rare events are keyed on `(seed, gpu_id, t)`, not on call order. Rehearse the demo once, it replays forever.

## 7. Fixtures

- `fixtures/telemetry_frame.golden.json` — one full frame from the demo window (day 148.4x), at a moment with heavy jobs queued and a G2 rack heating. Regenerate: `python3 -m sentinel.telemetry.fixture`.
- `fixtures/telemetry_frames.sample.jsonl` — a thinned frame series across the demo window (1 frame per 10 ticks) so charts/trend logic can be built against a real trajectory.

Build against the fixtures now; the live stream will match them shape-for-shape.

## 8. What is real vs. synthesized (say this right in the demo)

Real from the trace: node inventory & GPU models, every pod's arrival/schedule/free timing, `gpu_milli`/`num_gpu` demand, QoS, queue pressure. Synthesized deterministically **from that real load**: placement (the trace has no node-assignment column — seeded bin-packing), rack topology (no rack column — model-homogeneous racks of 32), and DCGM telemetry (temp/power/clocks as a function of real load; throttling *emerges* when real heavy jobs pile onto a dense rack — nothing is scripted).
