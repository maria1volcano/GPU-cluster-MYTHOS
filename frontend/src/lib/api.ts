import type {
  AgentRecommendation,
  ClusterState,
  DecisionLogEntry,
  TelemetryEvent,
} from "@/types/cluster";
import {
  advanceSimulation,
  generateTelemetryFromState,
  getRecommendation,
  getTelemetry,
  migrateJob,
  pushTelemetry,
  triggerStressScenario as triggerMockStressScenario,
} from "./mockCluster";

const USE_MOCKS = (import.meta.env.VITE_USE_MOCKS ?? "true") !== "false";
const API_BASE =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.DEV ? "" : "http://localhost:8000");
const FETCH_TIMEOUT_MS = 15_000;

export const apiConfig = {
  mode: USE_MOCKS ? ("mock" as const) : ("live" as const),
  baseUrl: API_BASE || (typeof window !== "undefined" ? window.location.origin : "http://localhost:8000"),
  lastFetch: null as string | null,
  backendReachable: null as boolean | null,
};

async function fetchWithTimeout(path: string, init?: RequestInit): Promise<Response> {
  const url = `${API_BASE}${path}`;
  if (typeof window === "undefined") {
    return fetch(url, init);
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

// --- live HTTP helpers -----------------------------------------------------
// These throw on non-2xx so TanStack Query surfaces the error (retry / error
// state) instead of us silently parsing an error body as data.

async function getJson<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(path);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  apiConfig.backendReachable = true;
  return (await res.json()) as T;
}

async function post(path: string, body?: unknown): Promise<Response> {
  const res = await fetchWithTimeout(path, {
    method: "POST",
    ...(body !== undefined
      ? { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }
      : {}),
  });
  if (!res.ok) throw new Error(`POST ${path} failed: ${res.status} ${res.statusText}`);
  apiConfig.backendReachable = true;
  return res;
}

export async function pingBackend(): Promise<boolean> {
  if (USE_MOCKS) {
    apiConfig.backendReachable = true;
    return true;
  }
  try {
    const res = await fetchWithTimeout("/health");
    apiConfig.backendReachable = res.ok;
    return res.ok;
  } catch {
    apiConfig.backendReachable = false;
    return false;
  }
}

// --- cluster state ---------------------------------------------------------

export async function getClusterState(): Promise<ClusterState> {
  if (USE_MOCKS) {
    const state = advanceSimulation();
    generateTelemetryFromState(state);
    apiConfig.lastFetch = new Date().toISOString();
    return state;
  }
  const state = await getJson<ClusterState>("/api/cluster/state");
  apiConfig.lastFetch = new Date().toISOString();
  return state;
}

// --- recommendation --------------------------------------------------------

export async function fetchRecommendation(): Promise<AgentRecommendation | null> {
  if (USE_MOCKS) return getRecommendation();
  const res = await fetchWithTimeout("/api/agent/recommendation");
  // A missing recommendation is a normal state, not an error: the backend may
  // answer 404 / 204 / 200-with-null when nothing needs attention.
  if (res.status === 204) return null;
  if (!res.ok) return null;
  apiConfig.backendReachable = true;
  const data = await res.json();
  return (data ?? null) as AgentRecommendation | null;
}

export async function getCurrentRecommendation(): Promise<AgentRecommendation | null> {
  return fetchRecommendation();
}

export async function acceptRecommendation(rec: AgentRecommendation) {
  return approveRecommendation(rec.id, rec);
}

export async function approveRecommendation(recommendationId: string, rec?: AgentRecommendation) {
  if (USE_MOCKS) {
    const recommendation = rec ?? getRecommendation();
    if (recommendation?.destinationRackId)
      migrateJob(recommendation.affectedRackId, recommendation.destinationRackId);
    pushTelemetry({
      type: "operator_event",
      rackId: recommendation?.affectedRackId,
      message: `Operator approved: ${recommendation?.title ?? recommendationId}`,
      severity: "healthy",
    });
    return { success: true };
  }
  await post(`/api/agent/recommendation/${recommendationId}/approve`);
  return { success: true };
}

export async function overrideRecommendation(
  recOrId: AgentRecommendation | string,
  reason: string,
) {
  const recommendationId = typeof recOrId === "string" ? recOrId : recOrId.id;
  const rec = typeof recOrId === "string" ? getRecommendation() : recOrId;
  if (USE_MOCKS) {
    pushTelemetry({
      type: "operator_event",
      rackId: rec?.affectedRackId,
      message: `Operator overrode recommendation — ${reason}`,
      severity: "warning",
    });
    pushTelemetry({
      type: "agent_event",
      message: "Agent updated future recommendations based on operator feedback",
      severity: "watch",
    });
    return { success: true };
  }
  await post(`/api/agent/recommendation/${recommendationId}/override`, { reason });
  return { success: true };
}

export async function askWhy(recOrId: AgentRecommendation | string) {
  const recommendationId = typeof recOrId === "string" ? recOrId : recOrId.id;
  const rec = typeof recOrId === "string" ? getRecommendation() : recOrId;
  if (USE_MOCKS) {
    pushTelemetry({
      type: "operator_event",
      rackId: rec?.affectedRackId,
      message: `Operator asked agent to explain ${rec?.affectedRackId ?? recommendationId} risk`,
      severity: "watch",
    });
    return rec;
  }
  const res = await post(`/api/agent/recommendation/${recommendationId}/why`);
  return res.json();
}

// --- telemetry -------------------------------------------------------------

export async function fetchTelemetry(): Promise<TelemetryEvent[]> {
  if (USE_MOCKS) return getTelemetry();
  const res = await fetchWithTimeout("/api/telemetry/events");
  if (!res.ok) return [];
  apiConfig.backendReachable = true;
  const data = await res.json();
  return Array.isArray(data) ? data : (data.events ?? []);
}

export type DashboardData = {
  state: ClusterState;
  rec: AgentRecommendation | null;
  events: TelemetryEvent[];
};

export async function fetchDashboard(): Promise<DashboardData> {
  if (USE_MOCKS) {
    const state = await getClusterState();
    generateTelemetryFromState(state);
    const rec = await fetchRecommendation();
    const events = [...(await fetchTelemetry())];
    return { state, rec, events };
  }
  const [state, rec, events] = await Promise.all([
    getClusterState(),
    fetchRecommendation(),
    fetchTelemetry(),
  ]);
  return { state, rec, events: [...events] };
}

/** @deprecated mock-only synchronous read — use {@link fetchTelemetry} for live mode. */
export function getTelemetryEvents(): TelemetryEvent[] {
  return getTelemetry();
}

// --- replay controls -------------------------------------------------------

export async function startReplay() {
  if (USE_MOCKS) {
    pushTelemetry({ type: "agent_event", message: "Mock replay started", severity: "healthy" });
    return { success: true };
  }
  await post("/api/replay/start");
  return { success: true };
}

export async function pauseReplay() {
  if (USE_MOCKS) {
    pushTelemetry({ type: "agent_event", message: "Mock replay paused", severity: "watch" });
    return { success: true };
  }
  await post("/api/replay/pause");
  return { success: true };
}

export async function resumeReplay() {
  if (USE_MOCKS) {
    pushTelemetry({ type: "agent_event", message: "Mock replay resumed", severity: "healthy" });
    return { success: true };
  }
  await post("/api/replay/resume");
  return { success: true };
}

export async function triggerStressScenario() {
  if (USE_MOCKS) {
    triggerMockStressScenario();
    pushTelemetry({
      type: "agent_event",
      message: "Stress scenario injected — cluster load rising on R-07",
      severity: "warning",
    });
    return { success: true };
  }
  await post("/api/replay/stress");
  return { success: true };
}

// --- decision log ----------------------------------------------------------

export async function getDecisionLog(): Promise<DecisionLogEntry[]> {
  if (USE_MOCKS) {
    return getTelemetry().map((event) => ({
      id: event.id,
      timestamp: event.timestamp,
      type: event.type,
      message: event.message,
      severity: event.severity,
    }));
  }
  const data = await getJson<DecisionLogEntry[] | { events: DecisionLogEntry[] }>(
    "/api/decision-log",
  );
  return Array.isArray(data) ? data : (data.events ?? []);
}

export async function resetDemo() {
  if (USE_MOCKS) {
    pushTelemetry({ type: "agent_event", message: "Mock demo reset requested", severity: "watch" });
    return { success: true };
  }
  const { clearPlayedAlerts } = await import("./alertAudio");
  clearPlayedAlerts();
  await post("/api/demo/reset");
  return { success: true };
}
