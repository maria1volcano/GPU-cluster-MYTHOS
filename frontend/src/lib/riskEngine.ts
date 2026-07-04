import type { RackMetric, RiskLevel } from "@/types/cluster";

export function riskLevelFromScore(score: number): RiskLevel {
  if (score >= 75) return "critical";
  if (score >= 55) return "warning";
  if (score >= 35) return "watch";
  return "healthy";
}

export function calculateRackRisk(rack: RackMetric, neighbors: RackMetric[] = []): number {
  const tempScore = Math.max(0, (rack.temperatureC - 60) * 2.2);
  const trendScore = Math.max(0, rack.temperatureTrendCPerMin * 12);
  const coolingScore = Math.max(0, (85 - rack.coolingEfficiencyPct) * 0.9);
  const utilScore = rack.gpuUtilizationPct * 0.25;
  const queueScore = rack.queuePressurePct * 0.2;
  const powerScore = Math.max(0, (rack.powerDrawKw - 100) * 0.25);
  const neighborScore =
    neighbors.length === 0
      ? 0
      : (neighbors.reduce((a, n) => a + Math.max(0, n.temperatureC - 70), 0) / neighbors.length) *
        1.2;

  const raw =
    tempScore + trendScore + coolingScore + utilScore + queueScore + powerScore + neighborScore;
  return Math.min(100, Math.round(raw));
}

export function topRiskReasons(
  rack: RackMetric,
): { name: string; value: string; severity: RiskLevel }[] {
  const reasons: { name: string; value: string; severity: RiskLevel; weight: number }[] = [
    {
      name: "Temperature trend",
      value: `${rack.temperatureTrendCPerMin >= 0 ? "+" : ""}${rack.temperatureTrendCPerMin.toFixed(1)}°C/min`,
      severity:
        rack.temperatureTrendCPerMin > 1.2
          ? "critical"
          : rack.temperatureTrendCPerMin > 0.5
            ? "warning"
            : "watch",
      weight: rack.temperatureTrendCPerMin * 12,
    },
    {
      name: "Core temperature",
      value: `${rack.temperatureC.toFixed(1)}°C`,
      severity: rack.temperatureC > 82 ? "critical" : rack.temperatureC > 74 ? "warning" : "watch",
      weight: Math.max(0, (rack.temperatureC - 60) * 2.2),
    },
    {
      name: "Cooling efficiency",
      value: `${rack.coolingEfficiencyPct.toFixed(0)}%`,
      severity:
        rack.coolingEfficiencyPct < 60
          ? "critical"
          : rack.coolingEfficiencyPct < 75
            ? "warning"
            : "healthy",
      weight: Math.max(0, (85 - rack.coolingEfficiencyPct) * 0.9),
    },
    {
      name: "GPU utilization",
      value: `${rack.gpuUtilizationPct.toFixed(0)}%`,
      severity: rack.gpuUtilizationPct > 92 ? "warning" : "watch",
      weight: rack.gpuUtilizationPct * 0.25,
    },
    {
      name: "Queue pressure",
      value: `${rack.queuePressurePct.toFixed(0)}%`,
      severity: rack.queuePressurePct > 80 ? "warning" : "watch",
      weight: rack.queuePressurePct * 0.2,
    },
  ];
  return reasons
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 4)
    .map(({ weight, ...r }) => r);
}
