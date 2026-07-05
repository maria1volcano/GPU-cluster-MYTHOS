import type { ClusterState, RackMetric, RiskLevel, WorkloadType } from "@/types/cluster";

/** Deterministic demo floor — live trace only loads rack-00 at the stress peak. */
const SLOT_UTIL = [58, 64, 71, 48, 55, 61, 68, 74];
const SLOT_TEMP = [62, 65, 68, 60, 63, 66, 69, 72];
const SLOT_POWER = [92, 98, 105, 88, 94, 100, 108, 112];
const WORKLOADS: WorkloadType[] = ["inference", "training", "batch"];

function slotIndex(rackId: string): number {
  const n = Number.parseInt(rackId.replace("rack-", ""), 10);
  return Number.isFinite(n) ? n : -1;
}

function riskLevelFromScore(score: number): RiskLevel {
  if (score >= 78) return "critical";
  if (score >= 58) return "warning";
  if (score >= 38) return "watch";
  return "healthy";
}

/** Keep the 8-rack floor visually active during live stress demo. */
export function applyLiveStressDemoFloor(
  liveMap: RackMetric[],
  hotRackId: string,
  preserveRackIds: string[] = [],
): RackMetric[] {
  if (!liveMap.length) return liveMap;

  const preserve = new Set([hotRackId, ...preserveRackIds.filter(Boolean)]);

  return liveMap.map((rack) => {
    if (preserve.has(rack.id)) return rack;
    const slot = slotIndex(rack.id);
    if (slot < 0) return rack;

    const util = SLOT_UTIL[slot] ?? 55;
    const riskScore = Math.min(72, 28 + slot * 4);

    return {
      ...rack,
      gpuUtilizationPct: util,
      gpuDemandGpus: Math.round((util / 100) * 256 * 10) / 10,
      temperatureC: SLOT_TEMP[slot] ?? 63,
      temperatureTrendCPerMin: 0.3 + slot * 0.05,
      powerDrawKw: SLOT_POWER[slot] ?? 95,
      queuePressurePct: 32 + slot * 3,
      coolingEfficiencyPct: 76 + slot,
      activeJobId: `JOB-DEMO-${String(slot).padStart(2, "0")}`,
      workloadType: WORKLOADS[slot % WORKLOADS.length],
      riskScore,
      riskLevel: riskLevelFromScore(riskScore),
    };
  });
}

export function activeMapRackCount(mapRacks: RackMetric[]): number {
  return mapRacks.filter((r) => (r.gpuUtilizationPct ?? 0) > 0).length;
}

export function decorateLiveStressState(
  state: ClusterState,
  rec?: { affectedRackId?: string; destinationRackId?: string } | null,
): ClusterState {
  if (state.replayStatus !== "stress") return state;
  const map = state.mapRacks ?? [];
  if (!map.length || activeMapRackCount(map) > 1) return state;
  const hotId = rec?.affectedRackId ?? "rack-00";
  const preserve = [rec?.destinationRackId].filter(Boolean) as string[];
  return { ...state, mapRacks: applyLiveStressDemoFloor(map, hotId, preserve) };
}
