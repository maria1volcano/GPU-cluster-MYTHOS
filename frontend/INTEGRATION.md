# ClusterPulse / Mythos 6 — Frontend ↔ Backend Integration Contract

This is the **canonical API contract**. The frontend (`src/lib/api.ts`) already
targets every endpoint below; the backend (`GPU-cluster-MYTHOS`, the `sentinel/`
pipeline) needs to serve them. All shapes are derived from
[`src/types/cluster.ts`](src/types/cluster.ts) — that file is the source of truth.

## Responsibilities

- **Frontend** — premium operator dashboard, 3D rack map, live telemetry feed,
  agent recommendation card, accept / override / ask-why flows. Talks to the
  backend only through the single adapter in `src/lib/api.ts`.
- **Backend** — telemetry/state, risk scoring, agent reasoning, recommendation
  generation, action execution, operator-feedback storage.

## Running

```bash
# frontend
bun install
bun run dev            # http://localhost:8080
```

`.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCKS=true           # set to false once the backend serves these endpoints
```

- With `VITE_USE_MOCKS=true` the whole demo runs against `src/lib/mockCluster.ts`
  (no backend needed).
- The backend **must send CORS headers** allowing the frontend origin
  (`http://localhost:8080`).

---

## Endpoints

Base URL = `VITE_API_BASE_URL`. All JSON. `{id}` = `AgentRecommendation.id`.

| #   | Method & path                                  | Body                   | Returns                                                                |
| --- | ---------------------------------------------- | ---------------------- | ---------------------------------------------------------------------- |
| 1   | `GET  /api/cluster/state`                      | —                      | `ClusterState` — **polled every 1.5 s**                                |
| 2   | `GET  /api/agent/recommendation`               | —                      | `AgentRecommendation` **or** `null` (use `404`/`204`/`null` when none) |
| 3   | `POST /api/agent/recommendation/{id}/approve`  | —                      | `2xx` — applies the migration; next `/state` must reflect it           |
| 4   | `POST /api/agent/recommendation/{id}/override` | `{ "reason": string }` | `2xx`                                                                  |
| 5   | `POST /api/agent/recommendation/{id}/why`      | —                      | `AgentRecommendation` (or `{ "explanation": string }`)                 |
| 6   | `POST /api/replay/stress`                      | —                      | `2xx` (also optional: `/api/replay/{start,pause,resume}`)              |
| 7   | `GET  /api/telemetry/events`                   | —                      | `TelemetryEvent[]` (or `{ "events": [...] }`)                          |
| 8   | `GET  /api/decision-log`                       | —                      | `DecisionLogEntry[]` (or `{ "events": [...] }`)                        |

Non-2xx on GET #1/#8 surfaces as an error state in the UI; #2 treats non-2xx as
"no recommendation". A future SSE stream (`GET /api/telemetry/stream`) may replace
polling for #7.

### Enums (shared)

- `riskLevel` / `severity`: `"healthy" | "watch" | "warning" | "critical"`
- `riskScore`: number `0–100`
- `predictedIssue`: `thermal_throttling | cooling_degradation | queue_pressure | projected_throttling_risk | node_instability | scheduling_bottleneck`
- `actionType`: `migrate_job | increase_cooling | reduce_load | monitor | continue_monitoring`
- `TelemetryEvent.type`: `metric_update | job_event | agent_event | operator_event`

---

## Shapes

### 1 · `ClusterState`

```jsonc
{
  "timestamp": "2026-07-04T18:20:00Z", // required (ISO 8601)
  "replayStatus": "running", // optional: idle|running|paused|stress|resolved
  "averageTemperatureC": 66.4, // required
  "totalPowerDrawKw": 880, // required
  "averageCoolingEfficiencyPct": 82, // required
  "activeJobs": 6, // required
  "agentConfidencePct": 87, // required
  "racks": [
    // required
    {
      "id": "R-07", // required
      "label": "Rack R-07", // required
      "temperatureC": 71.9, // required
      "temperatureTrendCPerMin": 2.1, // required
      "powerDrawKw": 120, // required
      "gpuUtilizationPct": 74, // required
      "coolingEfficiencyPct": 78, // required
      "queuePressurePct": 60, // required
      "riskScore": 61, // required (0–100)
      "riskLevel": "warning", // required
      "position": { "x": -3, "y": 0, "z": 0 }, // required — 3D floor layout
      "activeJobId": "JOB-X17", // optional
      "workloadType": "training", // optional: inference|training|batch|idle
      "heavyJobsQueued": 3, // optional
      "history": [
        // optional — ~30 pts, drives sparklines
        { "t": 0, "temp": 68.1, "power": 118, "util": 70 },
      ],
    },
  ],
}
```

### 2 / 5 · `AgentRecommendation`

```jsonc
{
  "id": "rec-123", // required — used in /{id}/approve etc.
  "createdAt": "2026-07-04T18:20:01Z", // required
  "status": "pending", // optional: none|pending|approved|overridden|averted
  "affectedRackId": "R-07", // required
  "destinationRackId": "R-02", // optional
  "affectedJobId": "JOB-X17", // optional
  "riskScore": 61, // required
  "riskLevel": "warning", // required
  "predictedIssue": "thermal_throttling", // required (see enums)
  "timeToImpactMinutes": 8, // required
  "actionType": "migrate_job", // required (see enums)
  "title": "Migrate JOB-X17 from R-07 to R-02", // required
  "summary": "Rack R-07 is likely to thermal throttle in the next 8 minutes.", // required
  "explanation": "Temperature trend +2.1°C/min with cooling at 78% …", // required
  "confidencePct": 80, // required
  "signals": [
    // required
    { "name": "Temperature trend", "value": "+2.1°C/min", "severity": "critical" },
    { "name": "Queue pressure", "value": "60%", "severity": "warning" },
  ],
}
```

### 7 / 8 · `TelemetryEvent` / `DecisionLogEntry`

```jsonc
{
  "id": "ev-1",
  "timestamp": "2026-07-04T18:20:02Z",
  "type": "metric_update", // TelemetryEvent enum above; DecisionLogEntry
  // also allows risk_detected, recommendation_generated,
  // operator_approved, incident_averted, demo_reset, …
  "rackId": "R-07", // optional
  "message": "R-07 temperature increased to 84°C",
  "severity": "warning",
}
```

---

## Mapping onto `sentinel/` (backend implementation notes)

- **`racks[]`** ← aggregate `GpuTelemetrySample` by `rack_id`:
  `temperatureC` = avg `temp_c`, `gpuUtilizationPct` = avg `util`,
  `powerDrawKw` = Σ`power_w` / 1000, `coolingEfficiencyPct` / `queuePressurePct`
  from your derived signals; `riskScore` / `riskLevel` from `Prediction`.
- **`AgentRecommendation`** ← `Recommendation` + its `Prediction`:
  `id` = `recommendation_id`, `affectedRackId` = `prediction.target.id`,
  `destinationRackId` = `to_rack`, `affectedJobId` = `job_id`,
  `timeToImpactMinutes` = `eta_seconds / 60`, `summary`/`explanation` =
  `expected_effect`/`justification`, `confidencePct` = `confidence * 100`,
  `signals[]` ← `prediction.evidence[]` (`name` = `metric`,
  `value` = formatted `current`/`slope_per_min`, `severity` by magnitude).
- After `POST /{id}/approve`, the next `GET /api/cluster/state` should show the
  migration applied (affected rack's `riskScore` drops) and
  `GET /api/agent/recommendation` should return the next rec or `null`.

## Demo fallback

If the backend isn't ready, keep `VITE_USE_MOCKS=true` — the full
predict → recommend → accept/override/ask-why → migrate → updated-state story
runs end-to-end against `src/lib/mockCluster.ts` + `src/lib/riskEngine.ts`.
