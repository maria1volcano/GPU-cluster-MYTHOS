# Mythos 6 Integration Guide

Project: Mythos 6 — GPU Cluster Ops Agent  
Track: Crusoe  
Purpose: explain how to connect the backend/data/agent work to the frontend dashboard.

---

## 1. Product flow the integration must support

The final integrated product should support this flow:

1. Backend replays Alibaba clusterdata as a stream.
2. Frontend displays the live cluster state.
3. Backend/prediction layer detects projected risk.
4. Agent generates a recommendation.
5. Frontend displays the recommendation.
6. Operator clicks Ask Why, Approve, or Override.
7. Backend processes the action.
8. Frontend updates cluster state and decision log.

Core demo flow:

Live cluster state -> predicted risk -> agent recommendation -> operator action -> updated dashboard -> decision log

Example demo:

- Rack 7 becomes risky.
- Agent predicts projected throttling risk in ~8 minutes.
- Agent recommends migrating JOB-X17 from Rack 7 to Rack 2.
- Operator approves.
- Backend applies action or simulates action.
- Frontend shows incident averted.
- Decision log records the decision.

---

## 2. Ownership split

### Frontend owns

- Premium operator dashboard
- Cluster/rack visualization
- Metrics cards
- Agent recommendation UI
- Ask Why drawer/modal
- Approve / Override buttons
- Decision log UI
- Mock mode fallback
- API adapter in `src/lib/api.ts`
- Frontend types in `src/types/cluster.ts`

### Backend owns

- Alibaba CSV parsing
- Stream replay
- Prediction model
- Crusoe Managed Inference calls
- Agent plan/decide/recommend loop
- API endpoints
- Operator action handling
- Decision log persistence or simulation
- Deployment/backend hosting

Important:
The frontend should not parse Alibaba CSV files directly. It should only consume clean API responses from the backend.

---

## 3. Frontend integration rule

Frontend components should not call backend URLs directly.

All backend communication must go through:

`src/lib/api.ts`

The frontend expects these adapter functions:

```ts
getClusterState()
getCurrentRecommendation()
startReplay()
pauseReplay()
resumeReplay()
triggerStressScenario()
approveRecommendation(recommendationId: string)
overrideRecommendation(recommendationId: string, reason: string)
askWhy(recommendationId: string)
getDecisionLog()
resetDemo()
```

Compatibility note:
The current UI also uses older wrapper names, including `fetchRecommendation()` and `acceptRecommendation(rec)`. Keep those wrappers working unless the frontend components are updated in the same PR.

If backend endpoint names change, update both:

1. `src/lib/api.ts`
2. this `integrate.md` file

---

## 4. Environment variables

The frontend should use environment variables, not hardcoded URLs.

For Vite:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCKS=true
VITE_BACKGROUND_IMAGE=/backgrounds/cluster-bg.jpg
```

For Next.js:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_USE_MOCKS=true
NEXT_PUBLIC_BACKGROUND_IMAGE=/backgrounds/cluster-bg.jpg
```

Backend integration steps:

1. Run backend locally.
2. Confirm backend URL, usually `http://localhost:8000`.
3. Set `VITE_API_BASE_URL` or `NEXT_PUBLIC_API_BASE_URL`.
4. Set mock mode to false:

   - `VITE_USE_MOCKS=false`
   - or `NEXT_PUBLIC_USE_MOCKS=false`

5. Restart frontend dev server.
6. Test all API calls.

---

## 5. Expected backend endpoints

Backend should expose these endpoints.

### Cluster state

```http
GET /api/cluster/state
```

Purpose:
Returns the latest cluster state for the dashboard.

### Current recommendation

```http
GET /api/agent/recommendation
```

Purpose:
Returns the current active agent recommendation, or no recommendation if none exists.

### Start replay

```http
POST /api/replay/start
```

Purpose:
Starts Alibaba clusterdata replay.

### Pause replay

```http
POST /api/replay/pause
```

Purpose:
Pauses the replay stream.

### Resume replay

```http
POST /api/replay/resume
```

Purpose:
Resumes the replay stream.

### Trigger stress scenario

```http
POST /api/replay/stress
```

Purpose:
Triggers or accelerates the demo scenario where Rack 7 becomes risky.

### Approve recommendation

```http
POST /api/agent/recommendation/:id/approve
```

Purpose:
Operator approves the recommendation.

### Override recommendation

```http
POST /api/agent/recommendation/:id/override
```

Purpose:
Operator rejects/overrides the recommendation and sends a reason.

### Ask why

```http
POST /api/agent/recommendation/:id/why
```

Purpose:
Returns explanation and ranked signals behind the recommendation.

### Decision log

```http
GET /api/decision-log
```

Purpose:
Returns decision/event log entries.

### Reset demo

```http
POST /api/demo/reset
```

Purpose:
Optional endpoint to reset the backend demo state.

---

## 6. Expected data types

These should match `src/types/cluster.ts`. The frontend currently keeps a few visual-only metric fields for the dashboard, but backend responses should at minimum provide the fields shown here.

```ts
export type RiskLevel = "healthy" | "watch" | "warning" | "critical";

export type WorkloadType = "inference" | "training" | "batch" | "idle";

export type ReplayStatus = "idle" | "running" | "paused" | "stress" | "resolved";

export type RecommendationStatus = "none" | "pending" | "approved" | "overridden" | "averted";

export interface RackNode {
  id: string;
  label: string;
  gpuUtilizationPct: number;
  queuePressurePct: number;
  heavyJobsQueued: number;
  nodeInstabilityScore: number;
  derivedThermalRiskPct: number;
  activeJobId?: string;
  workloadType?: WorkloadType;
  riskScore: number;
  riskLevel: RiskLevel;
  position?: {
    x: number;
    y: number;
    z?: number;
  };
}

export interface ClusterState {
  timestamp: string;
  replayStatus: ReplayStatus;
  totalRacks: number;
  activeJobs: number;
  heavyJobsQueued: number;
  averageGpuUtilizationPct: number;
  averageQueuePressurePct: number;
  projectedRiskCount: number;
  agentConfidencePct: number;
  racks: RackNode[];
}

export interface RecommendationSignal {
  name: string;
  value: string;
  trend?: "rising" | "falling" | "stable";
  severity: RiskLevel;
  explanation?: string;
}

export interface AgentRecommendation {
  id: string;
  createdAt: string;
  status: RecommendationStatus;
  affectedRackId: string;
  destinationRackId?: string;
  affectedJobId?: string;
  predictedIssue: "projected_throttling_risk" | "node_instability" | "scheduling_bottleneck";
  timeToImpactMinutes: number;
  actionType: "migrate_job" | "reduce_load" | "continue_monitoring";
  title: string;
  summary: string;
  recommendation: string;
  explanation: string;
  confidencePct: number;
  riskLevel: RiskLevel;
  signals: RecommendationSignal[];
}

export interface DecisionLogEntry {
  id: string;
  timestamp: string;
  type:
    | "system_ready"
    | "stream_started"
    | "stream_paused"
    | "stream_resumed"
    | "metric_update"
    | "risk_detected"
    | "recommendation_generated"
    | "operator_asked_why"
    | "operator_approved"
    | "operator_overrode"
    | "action_applied"
    | "incident_averted"
    | "demo_reset";
  message: string;
  severity: RiskLevel;
}
```

Important frontend compatibility fields:

- Current visual components also accept `temperatureC`, `temperatureTrendCPerMin`, `powerDrawKw`, `coolingEfficiencyPct`, and `history` on rack objects.
- If backend does not have direct temperature/cooling sensors, either omit those fields after the UI is updated or provide clearly derived/simulated demo values.
- Avoid claiming real temperature/cooling sensor data unless those fields are truly available.

---

## 7. Example API responses

### Example: `GET /api/cluster/state`

```json
{
  "timestamp": "2026-07-04T14:30:00Z",
  "replayStatus": "stress",
  "totalRacks": 8,
  "activeJobs": 24,
  "heavyJobsQueued": 3,
  "averageGpuUtilizationPct": 78,
  "averageQueuePressurePct": 64,
  "projectedRiskCount": 1,
  "agentConfidencePct": 87,
  "racks": [
    {
      "id": "R-07",
      "label": "Rack 7",
      "gpuUtilizationPct": 96,
      "queuePressurePct": 89,
      "heavyJobsQueued": 3,
      "nodeInstabilityScore": 72,
      "derivedThermalRiskPct": 84,
      "activeJobId": "JOB-X17",
      "workloadType": "training",
      "riskScore": 84,
      "riskLevel": "critical",
      "position": {
        "x": 2,
        "y": 1,
        "z": 0
      }
    },
    {
      "id": "R-02",
      "label": "Rack 2",
      "gpuUtilizationPct": 42,
      "queuePressurePct": 31,
      "heavyJobsQueued": 0,
      "nodeInstabilityScore": 18,
      "derivedThermalRiskPct": 24,
      "activeJobId": null,
      "workloadType": "idle",
      "riskScore": 24,
      "riskLevel": "healthy",
      "position": {
        "x": 1,
        "y": 0,
        "z": 0
      }
    }
  ]
}
```

### Example: `GET /api/agent/recommendation`

```json
{
  "id": "REC-001",
  "createdAt": "2026-07-04T14:30:10Z",
  "status": "pending",
  "affectedRackId": "R-07",
  "destinationRackId": "R-02",
  "affectedJobId": "JOB-X17",
  "predictedIssue": "projected_throttling_risk",
  "timeToImpactMinutes": 8,
  "actionType": "migrate_job",
  "title": "Rack 7 projected throttling risk",
  "summary": "Rack 7 is projected to hit throttling risk in ~8 minutes.",
  "recommendation": "Migrate JOB-X17 from Rack 7 to Rack 2.",
  "explanation": "Rack 7 combines high GPU utilization, rising queue pressure, three heavy jobs queued, and elevated node instability. Rack 2 has lower current load and enough capacity.",
  "confidencePct": 87,
  "riskLevel": "critical",
  "signals": [
    {
      "name": "GPU utilization",
      "value": "96%",
      "trend": "rising",
      "severity": "critical",
      "explanation": "Sustained high utilization increases operational risk."
    },
    {
      "name": "Queue pressure",
      "value": "89%",
      "trend": "rising",
      "severity": "warning",
      "explanation": "Three heavy jobs are queued for the same rack."
    },
    {
      "name": "Derived thermal risk",
      "value": "84%",
      "trend": "rising",
      "severity": "critical",
      "explanation": "Risk inferred from workload pressure and node behavior."
    }
  ]
}
```

### Example: `POST /api/agent/recommendation/:id/approve`

Request:

```json
{
  "operatorId": "demo-operator",
  "timestamp": "2026-07-04T14:31:00Z"
}
```

Response:

```json
{
  "success": true,
  "message": "Migration approved. JOB-X17 moved from Rack 7 to Rack 2.",
  "updatedRecommendationStatus": "approved"
}
```

### Example: `POST /api/agent/recommendation/:id/override`

Request:

```json
{
  "operatorId": "demo-operator",
  "reason": "Priority workload cannot move",
  "notes": "Operator has external context.",
  "timestamp": "2026-07-04T14:31:00Z"
}
```

Response:

```json
{
  "success": true,
  "message": "Override recorded. Agent feedback log updated.",
  "updatedRecommendationStatus": "overridden"
}
```

### Example: `POST /api/agent/recommendation/:id/why`

Response:

```json
{
  "recommendationId": "REC-001",
  "reasoningSummary": "The agent identified projected throttling risk because Rack 7 combines high GPU utilization, queue pressure, heavy jobs, and elevated node instability.",
  "rankedSignals": [
    {
      "name": "GPU utilization",
      "value": "96%",
      "impact": "high"
    },
    {
      "name": "Queue pressure",
      "value": "89%",
      "impact": "high"
    },
    {
      "name": "Derived thermal risk",
      "value": "84%",
      "impact": "high"
    }
  ],
  "alternativeActions": [
    "Migrate JOB-X17 to Rack 2",
    "Reduce workload on Rack 7",
    "Continue monitoring"
  ]
}
```

### Example: `GET /api/decision-log`

```json
{
  "events": [
    {
      "id": "LOG-001",
      "timestamp": "2026-07-04T14:31:00Z",
      "type": "operator_approved",
      "message": "Operator approved migration of JOB-X17 from Rack 7 to Rack 2.",
      "severity": "healthy"
    }
  ]
}
```

---

## 8. Mock mode behavior

The frontend must remain usable without backend.

When mock mode is true:

- API adapter returns mock cluster state.
- API adapter returns mock recommendation.
- Approve/Override/Ask Why return mock responses.
- UI should not crash.

When backend is ready:

- Set mock mode to false.
- Frontend calls real backend endpoints.
- Response shapes must match this guide.

---

## 9. CORS requirement

Backend must allow requests from the frontend dev server.

Common frontend URLs:

- `http://localhost:5173`
- `http://localhost:3000`

Backend should enable CORS for whichever URL the frontend uses.

---

## 10. Merge checklist

Before merging frontend and backend:

### Backend checklist

- [ ] Backend server runs locally.
- [ ] CORS is enabled.
- [ ] API base URL is known.
- [ ] `/api/cluster/state` works.
- [ ] `/api/agent/recommendation` works.
- [ ] `/api/replay/start` works.
- [ ] `/api/replay/stress` works.
- [ ] `/api/agent/recommendation/:id/approve` works.
- [ ] `/api/agent/recommendation/:id/override` works.
- [ ] `/api/agent/recommendation/:id/why` works.
- [ ] `/api/decision-log` works.
- [ ] Response shapes match this file.

### Frontend checklist

- [ ] `.env` points to backend URL.
- [ ] Mock mode is set to false.
- [ ] Frontend app runs.
- [ ] No components call raw backend URLs directly.
- [ ] API adapter handles backend responses.
- [ ] UI loads cluster state.
- [ ] UI loads recommendation.
- [ ] Approve button calls backend.
- [ ] Override button calls backend.
- [ ] Ask Why calls backend.
- [ ] Decision log loads.

---

## 11. Demo fallback plan

If backend is not ready, run frontend in mock mode:

```env
VITE_USE_MOCKS=true
```

or:

```env
NEXT_PUBLIC_USE_MOCKS=true
```

This allows the frontend to show the product story without backend.

---

## 12. Important wording for judges

Use:

- "real Alibaba workload replay"
- "derived operational risk"
- "projected throttling risk"
- "operator-in-the-loop recommendation"
- "Crusoe Managed Inference agent"

Avoid claiming direct temperature/cooling sensor data unless backend confirms those fields exist.
