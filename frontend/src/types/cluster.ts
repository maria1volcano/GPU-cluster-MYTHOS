export type RiskLevel = "healthy" | "watch" | "warning" | "critical";
export type WorkloadType = "inference" | "training" | "batch" | "idle";
export type ReplayStatus = "idle" | "running" | "paused" | "stress" | "resolved";
export type RecommendationStatus = "none" | "pending" | "approved" | "overridden" | "averted";
export type AlertStatus = "pending" | "generating" | "ready" | "skipped" | "failed";
export type ActionType =
  "migrate_job" | "increase_cooling" | "reduce_load" | "monitor" | "continue_monitoring";

export interface RackMetric {
  id: string;
  label: string;
  heavyJobsQueued?: number;
  nodeInstabilityScore?: number;
  derivedThermalRiskPct?: number;
  temperatureC: number;
  temperatureTrendCPerMin: number;
  powerDrawKw: number;
  gpuUtilizationPct: number;
  coolingEfficiencyPct: number;
  queuePressurePct: number;
  activeJobId?: string;
  workloadType?: WorkloadType;
  riskScore: number;
  riskLevel: RiskLevel;
  position: { x: number; y: number; z?: number };
  history?: { t: number; temp: number; power: number; util: number }[];
}

export type RackNode = RackMetric;

export interface ClusterState {
  timestamp: string;
  replayStatus?: ReplayStatus;
  totalRacks?: number;
  heavyJobsQueued?: number;
  averageGpuUtilizationPct?: number;
  averageQueuePressurePct?: number;
  projectedRiskCount?: number;
  averageTemperatureC: number;
  totalPowerDrawKw: number;
  averageCoolingEfficiencyPct: number;
  activeJobs: number;
  agentConfidencePct: number;
  racks: RackMetric[];
  mapRacks?: RackMetric[];
  dcgmSampleCount?: number;
  dcgmThrottlingGpus?: number;
}

export interface AgentSignal {
  name: string;
  value: string;
  trend?: "rising" | "falling" | "stable";
  severity: RiskLevel;
  explanation?: string;
}

export type RecommendationSignal = AgentSignal;

export interface AgentRecommendation {
  id: string;
  createdAt: string;
  status?: RecommendationStatus;
  affectedRackId: string;
  destinationRackId?: string;
  affectedJobId?: string;
  riskScore: number;
  riskLevel: RiskLevel;
  predictedIssue:
    | "thermal_throttling"
    | "cooling_degradation"
    | "queue_pressure"
    | "projected_throttling_risk"
    | "node_instability"
    | "scheduling_bottleneck";
  timeToImpactMinutes: number;
  actionType: ActionType;
  title: string;
  summary: string;
  recommendation?: string;
  explanation: string;
  confidencePct: number;
  signals: AgentSignal[];
  alertText?: string;
  alertStatus?: AlertStatus;
  alertAudioUrl?: string;
}

export interface OperatorActionEvent {
  id: string;
  timestamp: string;
  recommendationId: string;
  action: "accepted" | "overridden" | "asked_why";
  reason?: string;
}

export interface TelemetryEvent {
  id: string;
  timestamp: string;
  type: "metric_update" | "job_event" | "agent_event" | "operator_event";
  rackId?: string;
  message: string;
  severity: RiskLevel;
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
    | "job_event"
    | "agent_event"
    | "operator_event"
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
