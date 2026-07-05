import type { AgentRecommendation } from "@/types/cluster";

const ISSUE_LABELS: Record<AgentRecommendation["predictedIssue"], string> = {
  thermal_throttling: "Thermal throttling",
  cooling_degradation: "Cooling degradation",
  queue_pressure: "Queue pressure",
  projected_throttling_risk: "Projected throttling risk",
  node_instability: "Node instability",
  scheduling_bottleneck: "Scheduling bottleneck",
};

export function formatPredictedIssue(issue: AgentRecommendation["predictedIssue"]): string {
  return ISSUE_LABELS[issue] ?? "Infrastructure risk";
}

export type AlertBriefSection = {
  title: string;
  lines: string[];
};

/** Operator-readable briefing — brief recommendation + chosen action only. */
export function buildAlertBriefSections(rec: AgentRecommendation): AlertBriefSection[] {
  const issue = `${formatPredictedIssue(rec.predictedIssue)} on ${rec.affectedRackId}`;

  const actionLines: string[] = [];
  if (rec.affectedJobId && rec.destinationRackId) {
    actionLines.push(`Migrate ${rec.affectedJobId} from ${rec.affectedRackId} to ${rec.destinationRackId}`);
  } else if (rec.destinationRackId) {
    actionLines.push(`Move workload to ${rec.destinationRackId}`);
  } else {
    actionLines.push(rec.title);
  }

  return [
    { title: "Recommendation", lines: [issue, rec.title].filter(Boolean) },
    { title: "Action", lines: actionLines },
  ];
}
