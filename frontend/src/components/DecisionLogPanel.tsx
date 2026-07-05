import { useMemo, type ReactNode } from "react";
import { ClipboardList, ShieldCheck, Timer, TrendingUp } from "lucide-react";

import type { DecisionLogEntry } from "@/types/cluster";
import { riskColorHex } from "@/lib/riskStyles";
import { glassPanel, innerGlassPanel } from "@/lib/glassStyles";

export type DecisionKpis = {
  leadTimeMinutes: number;
  recommendationsSurfaced: number;
  incidentsAverted: number;
  approvalRatePct: number | null;
};

const RECORDED_ACTIONS = new Set(["SURFACED", "APPROVE", "OVERRIDE", "DISMISS"]);

export function isRecordedDecisionEntry(entry: DecisionLogEntry): boolean {
  return Boolean(entry.operatorAction && RECORDED_ACTIONS.has(entry.operatorAction));
}

export function computeDecisionKpis(entries: DecisionLogEntry[]): DecisionKpis {
  const structured = entries.filter(isRecordedDecisionEntry);
  const surfaced = structured.filter(
    (e) => e.operatorAction === "SURFACED" || e.type === "recommendation_generated",
  );
  const approved = structured.filter((e) => e.operatorAction === "APPROVE");
  const averted = structured.filter(
    (e) => e.outcome === "AVERTED" || e.type === "incident_averted",
  );
  const latestLead =
    surfaced.find((e) => e.leadTimeMinutes != null)?.leadTimeMinutes ??
    approved.find((e) => e.leadTimeMinutes != null)?.leadTimeMinutes ??
    8;
  const approvalRatePct =
    surfaced.length > 0 ? Math.round((approved.length / surfaced.length) * 100) : null;

  return {
    leadTimeMinutes: latestLead,
    recommendationsSurfaced: surfaced.length,
    incidentsAverted: averted.length,
    approvalRatePct,
  };
}

function KpiTile({
  label,
  value,
  suffix,
  icon,
  tone = "default",
}: {
  label: string;
  value: string | number;
  suffix?: string;
  icon: ReactNode;
  tone?: "default" | "good" | "heat";
}) {
  const accent =
    tone === "good" ? "#34d0a8" : tone === "heat" ? "#ff6b1a" : "#9ca3af";
  return (
    <div className={`rounded-sm border border-line px-3 py-3 ${innerGlassPanel}`}>
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        <span style={{ color: accent }}>{icon}</span>
        {label}
      </div>
      <p className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink">
        {value}
        {suffix && (
          <span className="ml-1 text-sm font-normal text-ink-dim">{suffix}</span>
        )}
      </p>
    </div>
  );
}

export function DecisionLogKpiStrip({ kpis }: { kpis: DecisionKpis }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <KpiTile
        label="Lead time"
        value={kpis.leadTimeMinutes}
        suffix="min"
        icon={<Timer className="h-3.5 w-3.5" />}
        tone="heat"
      />
      <KpiTile
        label="Surfaced"
        value={kpis.recommendationsSurfaced}
        icon={<TrendingUp className="h-3.5 w-3.5" />}
      />
      <KpiTile
        label="Averted"
        value={kpis.incidentsAverted}
        icon={<ShieldCheck className="h-3.5 w-3.5" />}
        tone="good"
      />
      <KpiTile
        label="Approval rate"
        value={kpis.approvalRatePct ?? "—"}
        suffix={kpis.approvalRatePct != null ? "%" : undefined}
        icon={<ClipboardList className="h-3.5 w-3.5" />}
      />
    </div>
  );
}

const TYPE_LABEL: Record<string, string> = {
  recommendation_generated: "Surfaced",
  operator_approved: "Approved",
  operator_overrode: "Override",
  incident_averted: "Averted",
  risk_detected: "Risk",
  agent_event: "Agent",
  operator_event: "Operator",
};

export function DecisionLogPanel({ entries }: { entries: DecisionLogEntry[] }) {
  const logOnly = useMemo(() => entries.filter(isRecordedDecisionEntry), [entries]);
  const kpis = useMemo(() => computeDecisionKpis(logOnly), [logOnly]);

  return (
    <div className={`flex h-full flex-col ${glassPanel}`}>
      <div className="border-b border-line px-5 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="font-mono text-[11px] uppercase tracking-[0.2em] text-ink-dim">
            Decision log & KPIs
          </div>
          <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            append-only
          </span>
        </div>
        <div className="mt-3">
          <DecisionLogKpiStrip kpis={kpis} />
        </div>
      </div>
      <div
        aria-live="polite"
        className="max-h-[280px] flex-1 space-y-1.5 overflow-y-auto p-4"
      >
        {logOnly.length === 0 && (
          <p className="pt-4 text-center text-xs text-ink-faint">
            Recommendations and operator actions appear here after stress or approve.
          </p>
        )}
        {[...logOnly].reverse().map((e) => (
          <div
            key={e.id}
            className={`flex items-start gap-3 rounded-sm px-3 py-2 text-xs ${innerGlassPanel}`}
          >
            <span
              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: riskColorHex(e.severity) }}
            />
            <div className="min-w-0 flex-1">
              <p className="text-ink">{e.message}</p>
              <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                {new Date(e.timestamp).toLocaleTimeString()} ·{" "}
                {TYPE_LABEL[e.type] ?? e.type.replace(/_/g, " ")}
                {e.leadTimeMinutes != null && e.type === "recommendation_generated"
                  ? ` · ~${e.leadTimeMinutes} min to impact`
                  : ""}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
