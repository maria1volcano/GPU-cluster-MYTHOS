# GPU Cluster Ops Agent

**GPU Cluster Ops Agent** is an AI-powered GPU cluster operations dashboard built for the **Crusoe track**. It monitors a live GPU cluster stream, predicts operational risks before they impact workloads, and gives operators clear recommendations they can approve, override, or question.

The project follows one core operational loop:

```txt
Live cluster state -> predicted risk -> agent recommendation -> operator action -> updated dashboard -> decision log
```

## Overview

GPU clusters are expensive, complex, and sensitive to workload pressure. When heavy jobs accumulate on stressed racks, operators need to act before performance drops, jobs slow down, or throttling risk increases.

Mythos 6 gives operators a live situational model of the cluster and surfaces explainable recommendations, such as:

> Rack 7 is projected to hit throttling risk in about 8 minutes. Three heavy jobs are queued and workload pressure is rising. Recommended action: migrate JOB-X17 to Rack 2.

The operator can then:

- Approve the recommendation
- Override it with context
- Ask why to understand the agent's reasoning

Every decision is logged so the operator stays in control.

## Hackathon Track

This project was built for **Statement Three - Crusoe**.

The track asks for an agent that builds a live situational model from streaming inputs and uses that model to drive proactive, context-sensitive actions a non-technical operator can trust, question, and override.

Mythos 6 fits this by creating an operator-facing GPU cluster control room where the agent watches changing infrastructure signals, predicts risk, recommends actions, and adapts to operator feedback.

## Demo Flow

The main demo is intentionally focused:

1. The operator opens the live cluster dashboard.
2. The dashboard replays GPU cluster telemetry.
3. Rack 7 begins showing elevated workload pressure.
4. The agent flags a projected throttling risk.
5. The agent recommends migrating `JOB-X17` from Rack 7 to Rack 2.
6. The operator clicks **Approve**.
7. The dashboard updates: the job moves to Rack 2, Rack 7 risk decreases, and the incident is marked as averted.
8. The decision is added to the log.

This creates a complete 60-second story for judges.

## Key Features

- Premium dark operator dashboard
- Live GPU cluster visualization
- Rack health and risk states
- Workload replay mode
- Stress scenario trigger
- AI recommendation card
- Explainable telemetry signals
- One-tap approve and override flow
- Ask Why reasoning drawer
- Decision and event log
- Mock mode for frontend-only demo
- Backend integration contract

## Architecture

```txt
Alibaba clusterdata
        |
Stream replayer
        |
Prediction layer
        |
Agent loop
        |
Crusoe Managed Inference
        |
Operator dashboard
        |
Approve / Override / Ask Why
        |
Decision log
```

## What Is Real vs Inferred?

The project is designed to use **Alibaba clusterdata** as a realistic workload replay source.

Real or replayed signals may include:

- Workload traces
- Job pressure
- Scheduling events
- Utilization-style telemetry
- Machine and job behavior

Derived or inferred signals may include:

- Projected throttling risk
- Node instability score
- Operational risk level
- Recommended migration action

The frontend can run in mock mode so the full demo works before backend integration is complete.

## Tech Stack

Frontend:

- React
- TypeScript
- Tailwind CSS
- shadcn/ui
- lucide-react
- React Three Fiber / Three.js
- Mock API layer

Backend target:

- Stream replay service
- Prediction layer
- Agent orchestration
- Crusoe Managed Inference
- REST API endpoints

## Getting Started

Clone the repository:

```bash
git clone https://github.com/maria1volcano/GPU-cluster.git
cd GPU-cluster
```

Install dependencies:

```bash
npm install
```

Create an environment file.

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

Run the frontend:

```bash
npm run dev
```

## Mock Mode

Mock mode allows the frontend demo to run without a backend.

When mock mode is enabled, the app simulates:

- Live cluster replay
- Rack 7 becoming risky
- An agent recommendation
- Approve and override actions
- Ask Why explanations
- Decision log updates

Enable mock mode with:

```env
VITE_USE_MOCKS=true
```

or:

```env
NEXT_PUBLIC_USE_MOCKS=true
```

## Backend Integration

The frontend should call backend services through a single API adapter rather than raw `fetch` calls inside UI components.

Expected API endpoints:

```txt
GET  /api/cluster/state
GET  /api/agent/recommendation
POST /api/replay/start
POST /api/replay/stress
POST /api/agent/recommendation/:id/approve
POST /api/agent/recommendation/:id/override
POST /api/agent/recommendation/:id/why
GET  /api/decision-log
```

If present, `INTEGRATION.md` should define the exact request and response shapes, frontend/backend responsibilities, mock behavior, and demo fallback plan.

## Core UI States

- **Idle**: The cluster replay has not started yet.
- **Running**: The cluster replay is active and the dashboard is receiving live updates.
- **Stress**: Rack 7 becomes risky and the agent generates a recommendation.
- **Resolved**: The operator approves the recommendation and the dashboard shows the incident as averted.

## Suggested Project Structure

```txt
src/
  components/
    ClusterHeader.tsx
    RackMap.tsx
    AgentRecommendationPanel.tsx
    TelemetryFeed.tsx
    DecisionLog.tsx
    OverrideModal.tsx
    AskWhyDrawer.tsx
  lib/
    api.ts
    mockCluster.ts
    riskEngine.ts
  types/
    cluster.ts
  App.tsx

INTEGRATION.md
README.md
```

Actual structure may vary as the project evolves.

## Demo Script

1. "This is Mythos 6, a GPU cluster operations agent for live infrastructure monitoring."
2. "We replay cluster workload data and show the operator a live situational model."
3. Click **Start Replay**.
4. Click **Trigger Stress Scenario**.
5. "Rack 7 is now showing projected throttling risk."
6. Open **Ask Why**.
7. "The agent explains the recommendation using workload pressure, queue depth, utilization, and derived risk."
8. Click **Approve**.
9. "The job is migrated to Rack 2, Rack 7 risk drops, and the decision is logged."
10. "The operator stays in control and can override the agent at any moment."

## Team

- **Marcy** - Frontend UX/UI, operator dashboard, integration documentation
- **Omar** - Data ingestion, stream replay, prediction model, deployment
- **Parv** - Agent loop, tool calls, Crusoe Managed Inference integration
- **Anish** - Agent loop, tool calls, Crusoe Managed Inference integration

## Status

- Frontend dashboard: in progress
- Mock mode: supported
- Backend integration: pending
- Alibaba clusterdata replay: backend-owned
- Crusoe Managed Inference integration: backend/agent-owned

## License

Hackathon project. License can be added later.
