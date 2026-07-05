import type { ActivePrediction, ClusterState } from "@/types/cluster";
import { formatPredictedIssue } from "@/lib/formatAlertBrief";
import { innerGlassPanel } from "@/lib/glassStyles";
import { riskColorHex } from "@/lib/riskStyles";
import { Activity, AlertTriangle, Cpu, Layers, type LucideIcon } from "lucide-react";

const TYPE_ICONS: Partial<Record<ActivePrediction["type"], LucideIcon>> = {
  thermal_throttling: Activity,
  scheduling_bottleneck: Layers,
  node_instability: Cpu,
};

export function ActivePredictionsPanel({ state }: { state: ClusterState | null }) {
  const preds = state?.activePredictions ?? [];
  if (!preds.length) return null;

  return (
    <section className={`${innerGlassPanel} p-4 sm:p-5`}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-faint">
            Active risk signals
          </p>
          <h3 className="mt-1 text-sm font-semibold text-ink">
            M3 predictors watching thermal, queue, and node health
          </h3>
        </div>
        <span className="rounded-sm border border-line bg-surface/80 px-2 py-1 font-mono text-[10px] uppercase tracking-widest text-ink-dim">
          {preds.length} active
        </span>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        {preds.map((p) => (
          <PredictionCard key={`${p.type}-${p.targetId}`} prediction={p} />
        ))}
      </div>
    </section>
  );
}

function PredictionCard({ prediction: p }: { prediction: ActivePrediction }) {
  const Icon = TYPE_ICONS[p.type] ?? AlertTriangle;
  const color = riskColorHex(p.riskLevel);
  const target =
    p.targetKind === "node"
      ? `Node ${p.targetId}${p.rackId ? ` · ${p.rackId}` : ""}`
      : p.rackId ?? p.targetId;

  return (
    <div
      className="rounded-sm border bg-surface/70 p-4"
      style={{ borderColor: `${color}44` }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span
            className="flex h-8 w-8 items-center justify-center rounded-sm border"
            style={{ borderColor: `${color}55`, color, background: `${color}14` }}
          >
            <Icon className="h-4 w-4" />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">{formatPredictedIssue(p.type)}</p>
            <p className="font-mono text-[10px] uppercase tracking-widest text-ink-dim">
              {target}
            </p>
          </div>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-widest" style={{ color }}>
          {p.severity}
        </span>
      </div>

      <ul className="mt-3 space-y-1.5 text-xs leading-relaxed text-ink-dim">
        {(p.signals ?? []).slice(0, 3).map((s) => (
          <li key={`${s.name}-${s.value}`} className="flex gap-2">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full" style={{ background: color }} />
            <span>
              <span className="text-ink-faint">{s.name}: </span>
              {s.value}
            </span>
          </li>
        ))}
      </ul>

      <p className="mt-3 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        ~{p.etaMinutes} min · {p.confidencePct}% confidence
      </p>
    </div>
  );
}
