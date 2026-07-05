import type {
  AgentRecommendation,
  ClusterState,
  RackMetric,
  TelemetryEvent,
  WorkloadType,
} from "@/types/cluster";
import { calculateRackRisk, riskLevelFromScore, topRiskReasons } from "./riskEngine";

const RACK_LAYOUT: { id: string; x: number; z: number }[] = [
  { id: "R-01", x: -3, z: -1.5 },
  { id: "R-02", x: -1, z: -1.5 },
  { id: "R-03", x: 1, z: -1.5 },
  { id: "R-04", x: 3, z: -1.5 },
  { id: "R-05", x: -3, z: 1.5 },
  { id: "R-06", x: -1, z: 1.5 },
  { id: "R-07", x: 1, z: 1.5 },
  { id: "R-08", x: 3, z: 1.5 },
];

const WORKLOADS: WorkloadType[] = ["inference", "training", "batch", "idle"];

function rand(min: number, max: number) {
  return Math.random() * (max - min) + min;
}

let stressMode = false;
let stressStartedAt = 0;

export function triggerStressScenario() {
  stressMode = true;
  stressStartedAt = Date.now();
}

export function isStressMode() {
  return stressMode;
}

function seedRack(idx: number): RackMetric {
  const { id, x, z } = RACK_LAYOUT[idx];
  const util = rand(45, 88);
  const temp = rand(58, 72);
  const cool = rand(75, 92);
  const power = rand(85, 130);
  const queue = rand(20, 65);
  const demandGpus = Math.round((util / 100) * 256 * 10) / 10;
  const workload = WORKLOADS[Math.floor(Math.random() * WORKLOADS.length)];
  const base: RackMetric = {
    id,
    label: `Rack ${id}`,
    temperatureC: temp,
    temperatureTrendCPerMin: rand(-0.2, 0.4),
    powerDrawKw: power,
    gpuUtilizationPct: util,
    gpuDemandGpus: demandGpus,
    coolingEfficiencyPct: cool,
    queuePressurePct: queue,
    activeJobId:
      workload === "idle" ? undefined : `JOB-${String.fromCharCode(65 + idx)}${10 + idx}`,
    workloadType: workload,
    riskScore: 0,
    riskLevel: "healthy",
    position: { x, y: 0, z },
    history: [],
  };
  base.riskScore = calculateRackRisk(base);
  base.riskLevel = riskLevelFromScore(base.riskScore);
  return base;
}

let racks: RackMetric[] = RACK_LAYOUT.map((_, i) => seedRack(i));
let tick = 0;

function neighborsOf(r: RackMetric) {
  return racks.filter(
    (n) =>
      n.id !== r.id &&
      Math.abs(n.position.x - r.position.x) <= 2 &&
      Math.abs((n.position.z ?? 0) - (r.position.z ?? 0)) <= 2,
  );
}

export function advanceSimulation(): ClusterState {
  tick++;
  const now = Date.now();

  racks = racks.map((r, i) => {
    const stressed = stressMode && i === 6; // R-07 gets stressed
    const nearStress = stressMode && (i === 5 || i === 7);
    const drift = rand(-0.4, 0.4);
    const trend = stressed ? rand(1.2, 2.1) : nearStress ? rand(0.4, 0.9) : drift;
    const newTemp = Math.max(52, Math.min(94, r.temperatureC + trend * 0.5 + rand(-0.3, 0.3)));
    const newCool = Math.max(
      45,
      Math.min(
        96,
        stressed
          ? r.coolingEfficiencyPct - rand(0.8, 1.8)
          : r.coolingEfficiencyPct + rand(-0.6, 0.6),
      ),
    );
    const newUtil = Math.max(20, Math.min(99, r.gpuUtilizationPct + rand(-2, stressed ? 3 : 2)));
    const newPower = Math.max(60, Math.min(180, r.powerDrawKw + rand(-3, stressed ? 5 : 3)));
    const newQueue = Math.max(
      10,
      Math.min(
        98,
        stressed ? r.queuePressurePct + rand(0.5, 2.5) : r.queuePressurePct + rand(-1.5, 1.5),
      ),
    );

    const updated: RackMetric = {
      ...r,
      temperatureC: newTemp,
      temperatureTrendCPerMin: trend,
      coolingEfficiencyPct: newCool,
      gpuUtilizationPct: newUtil,
      gpuDemandGpus: Math.round((newUtil / 100) * 256 * 10) / 10,
      powerDrawKw: newPower,
      queuePressurePct: newQueue,
      history: [
        ...(r.history ?? []).slice(-29),
        { t: now, temp: newTemp, power: newPower, util: newUtil },
      ],
    };
    return updated;
  });

  racks = racks.map((r) => {
    const score = calculateRackRisk(r, neighborsOf(r));
    return { ...r, riskScore: score, riskLevel: riskLevelFromScore(score) };
  });

  const avgTemp = racks.reduce((a, r) => a + r.temperatureC, 0) / racks.length;
  const totalPower = racks.reduce((a, r) => a + r.powerDrawKw, 0);
  const avgCool = racks.reduce((a, r) => a + r.coolingEfficiencyPct, 0) / racks.length;
  const active = racks.filter((r) => r.workloadType && r.workloadType !== "idle").length;

  return {
    timestamp: new Date(now).toISOString(),
    averageTemperatureC: avgTemp,
    totalPowerDrawKw: totalPower,
    averageCoolingEfficiencyPct: avgCool,
    activeJobs: active,
    agentConfidencePct: 78 + Math.round(rand(0, 12)),
    racks,
  };
}

export function currentRacks() {
  return racks;
}

export function migrateJob(fromId: string, toId: string) {
  const from = racks.find((r) => r.id === fromId);
  const to = racks.find((r) => r.id === toId);
  if (!from || !to) return;
  const jobId = from.activeJobId;
  const wl = from.workloadType;
  racks = racks.map((r) => {
    if (r.id === fromId) {
      return {
        ...r,
        activeJobId: undefined,
        workloadType: "idle",
        temperatureTrendCPerMin: -0.8,
        gpuUtilizationPct: Math.max(20, r.gpuUtilizationPct - 30),
        gpuDemandGpus: Math.round((Math.max(20, r.gpuUtilizationPct - 30) / 100) * 256 * 10) / 10,
        powerDrawKw: Math.max(60, r.powerDrawKw - 25),
        queuePressurePct: Math.max(10, r.queuePressurePct - 30),
      };
    }
    if (r.id === toId) {
      return {
        ...r,
        activeJobId: jobId,
        workloadType: wl,
        gpuUtilizationPct: Math.min(96, r.gpuUtilizationPct + 15),
        gpuDemandGpus:
          Math.round((Math.min(96, r.gpuUtilizationPct + 15) / 100) * 256 * 10) / 10,
      };
    }
    return r;
  });
  stressMode = false;
}

export function getRecommendation(): AgentRecommendation | null {
  const worst = [...racks].sort((a, b) => b.riskScore - a.riskScore)[0];
  if (!worst || worst.riskScore < 45) return null;
  const safest = [...racks]
    .filter((r) => r.id !== worst.id && r.riskScore < 40)
    .sort((a, b) => a.riskScore - b.riskScore)[0];
  const signals = topRiskReasons(worst);
  const timeToImpact = Math.max(2, Math.round(20 - worst.riskScore / 6));
  return {
    id: `REC-${worst.id}-${tick}`,
    createdAt: new Date().toISOString(),
    affectedRackId: worst.id,
    destinationRackId: safest?.id,
    riskScore: worst.riskScore,
    riskLevel: worst.riskLevel,
    predictedIssue: "thermal_throttling",
    timeToImpactMinutes: timeToImpact,
    actionType: safest ? "migrate_job" : "increase_cooling",
    title: safest
      ? `Migrate ${worst.activeJobId ?? "workload"} from ${worst.id} to ${safest.id}`
      : `Increase cooling on ${worst.id}`,
    summary: `Rack ${worst.id} is likely to thermal throttle in the next ${timeToImpact} minutes.`,
    explanation: `Temperature is ${worst.temperatureTrendCPerMin >= 0 ? "rising" : "falling"} ${Math.abs(
      worst.temperatureTrendCPerMin,
    ).toFixed(1)}°C/min, cooling efficiency is ${worst.coolingEfficiencyPct.toFixed(
      0,
    )}%, GPU utilization at ${worst.gpuUtilizationPct.toFixed(0)}%, and queue pressure ${worst.queuePressurePct.toFixed(
      0,
    )}%.${safest ? ` Rack ${safest.id} has lower thermal load and enough capacity to absorb the workload.` : ""}`,
    confidencePct: Math.min(96, 65 + Math.round(worst.riskScore / 4)),
    signals,
  };
}

// Telemetry feed
const events: TelemetryEvent[] = [];
let evtId = 0;
export function pushTelemetry(
  e: Omit<TelemetryEvent, "id" | "timestamp"> & { timestamp?: string },
) {
  const ev: TelemetryEvent = {
    id: `EVT-${++evtId}`,
    timestamp: e.timestamp ?? new Date().toISOString(),
    type: e.type,
    rackId: e.rackId,
    message: e.message,
    severity: e.severity,
  };
  events.unshift(ev);
  if (events.length > 40) events.pop();
  return ev;
}
export function getTelemetry() {
  return events;
}

export function generateTelemetryFromState(state: ClusterState) {
  const worst = [...state.racks].sort((a, b) => b.riskScore - a.riskScore)[0];
  if (worst && worst.riskScore > 60 && Math.random() > 0.4) {
    pushTelemetry({
      type: "metric_update",
      rackId: worst.id,
      message: `${worst.id} temperature ${worst.temperatureTrendCPerMin >= 0 ? "rising" : "falling"} — now ${worst.temperatureC.toFixed(1)}°C`,
      severity: worst.riskLevel,
    });
  }
  if (Math.random() > 0.7) {
    const r = state.racks[Math.floor(Math.random() * state.racks.length)];
    pushTelemetry({
      type: "metric_update",
      rackId: r.id,
      message: `${r.id} cooling efficiency at ${r.coolingEfficiencyPct.toFixed(0)}%`,
      severity: r.riskLevel,
    });
  }
  if (Math.random() > 0.85) {
    pushTelemetry({
      type: "agent_event",
      message: `Agent recalculated risk scores across ${state.racks.length} racks`,
      severity: "watch",
    });
  }
}
