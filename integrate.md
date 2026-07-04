# Mythos 6 Integration Guide

Project: Mythos 6 - GPU Cluster Ops Agent
Track: Crusoe
Purpose: explain how the frontend and backend connect after merging into one repo.

---

## Repo structure

```txt
/backend-repo
  /frontend
    package.json
    src/
    public/
    .env.example
  sentinel/
  fixtures/
  tests/
  tools/
  integrate.md
  README.md
```

The backend Python files currently live at the repository root in the `sentinel/` package.

---

## How to run backend

Install backend dependencies:

```bash
pip install -r requirements.txt
```

Run the current end-to-end backend demo:

```bash
python -m sentinel.demo
```

Useful backend verification commands:

```bash
python -m sentinel.data.stats
python -m sentinel.data.racks
python -m sentinel.replay.replayer
python -m sentinel.engine
python -m sentinel.telemetry.fixture
pytest
```

There is not yet a FastAPI/Flask HTTP server entrypoint in this repo. The stream and REST routes below are the integration contract for the API layer that should wrap the existing `sentinel` engine, prediction, agent, and decision-log modules.

---

## How to run frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Environment variables

Frontend must use the backend API base URL.

For this Vite frontend:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_USE_MOCKS=false
VITE_BACKGROUND_IMAGE=/backgrounds/cluster-bg.jpg
```

For demo fallback before backend HTTP integration is ready:

```env
VITE_USE_MOCKS=true
```

---

## Frontend responsibilities

* Operator dashboard
* Landing/hero UI
* Rack visualization
* Agent recommendation UI
* Ask Why / Approve / Override controls
* Decision log UI
* API adapter in frontend code
* Mock mode fallback

## Backend responsibilities

* Alibaba clusterdata ingestion/replay
* Stream endpoint
* Prediction model
* Agent loop
* Crusoe Managed Inference
* Decision endpoint
* Decision log
* Deployment

---

## Required backend endpoints

The frontend expects these endpoints or equivalent adapter mappings. Existing backend contracts in `CONTRACTS.md` define the telemetry frame shape and note that the stream should carry telemetry, prediction, and KPI frames.

### WebSocket stream

```txt
WS /stream
```

Purpose:
Streams cluster frames to the frontend.

Expected frame shape should match `sentinel.telemetry.sample.TelemetryFrame.to_dict()` or be mapped in the frontend API adapter.

### Latest cluster state

```http
GET /api/cluster/state
```

Purpose:
Returns the latest telemetry frame for REST/mock-mode compatible UI paths.

### Decision endpoint

```http
POST /decision
```

Purpose:
Receives operator actions such as approve/override.

Suggested request:

```json
{
  "recommendationId": "REC-001",
  "action": "approve",
  "reason": null
}
```

For override:

```json
{
  "recommendationId": "REC-001",
  "action": "override",
  "reason": "Priority workload cannot move"
}
```

The backend should map approved migration actions to `Engine.apply_action("MIGRATE_JOB", job_id, to_rack)` and record outcomes through `sentinel.decision_log.DecisionLog`.

---

## Frontend API adapter rule

Frontend components should not call raw backend URLs directly.

All backend communication should go through frontend API/stream adapter files such as:

```txt
frontend/src/lib/api.ts
frontend/src/lib/stream.ts
```

The current frontend has `frontend/src/lib/api.ts`; add `stream.ts` or extend the adapter when the backend stream server lands. If backend endpoint names differ, update those adapter files instead of changing every UI component.

---

## Merge checklist

### Backend checklist

* [ ] Backend runs locally.
* [ ] WebSocket `/stream` works.
* [ ] `POST /decision` works.
* [ ] CORS allows the frontend dev server.
* [ ] Backend URL is documented.
* [ ] Stream frame shape is documented.

### Frontend checklist

* [ ] Frontend runs from `/frontend`.
* [ ] `.env.example` exists.
* [ ] Frontend points to backend URL.
* [ ] Mock mode can be disabled.
* [ ] API/stream adapter is ready.
* [ ] UI does not call backend directly outside adapter.

---

## Demo fallback

If backend integration is not ready, run frontend in mock mode:

```env
VITE_USE_MOCKS=true
```
