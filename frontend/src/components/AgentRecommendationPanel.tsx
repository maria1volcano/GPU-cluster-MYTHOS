import type { AgentRecommendation } from "@/types/cluster";
import { riskColorHex, riskLabel, riskTextClass } from "@/lib/riskStyles";
import { innerGlassPanel, strongGlassPanel } from "@/lib/glassStyles";
import {
  ArrowRight,
  Check,
  HelpCircle,
  ShieldOff,
  Sparkles,
  TrendingUp,
  Volume2,
} from "lucide-react";

export function AgentRecommendationPanel({
  rec,
  onAccept,
  onOverride,
  onAskWhy,
  showExplanation,
}: {
  rec: AgentRecommendation | null;
  onAccept: () => void;
  onOverride: () => void;
  onAskWhy: () => void;
  showExplanation: boolean;
}) {
  if (!rec) {
    return (
      <div className={`${strongGlassPanel} p-6`}>
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink-dim">
          <Sparkles className="h-3 w-3" /> AI Operator Recommendation
        </div>
        <p className="mt-4 text-sm text-ink-dim">
          No action required. All racks operating within safe thermal envelope.
        </p>
      </div>
    );
  }
  const color = riskColorHex(rec.riskLevel);
  return (
    <div
      className={`${strongGlassPanel} relative overflow-hidden p-6`}
      style={{ borderLeft: `2px solid ${color}` }}
    >
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink-dim">
            <Sparkles className="h-3 w-3" /> AI Operator Recommendation
          </div>
          <h3 className="mt-3 text-2xl font-semibold text-ink">{rec.title}</h3>
          <p className="mt-1 text-sm text-ink-dim">{rec.summary}</p>
        </div>
        <div className="text-right">
          <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">Risk</p>
          <p className={`font-mono text-3xl font-semibold ${riskTextClass(rec.riskLevel)}`}>
            {rec.riskScore}
          </p>
          <p className="text-xs text-ink-dim">
            {riskLabel(rec.riskLevel)} · confidence {rec.confidencePct}%
          </p>
          {rec.alertStatus === "generating" && (
            <p className="mt-1 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              Generating voice alert…
            </p>
          )}
          {rec.alertStatus === "ready" && rec.alertAudioUrl && (
            <p className="mt-1 inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-widest text-heat">
              <Volume2 className="h-3 w-3" /> Voice alert ready
            </p>
          )}
        </div>
      </div>

      <div className="relative mt-5 flex flex-wrap items-center gap-3 text-sm">
        <Pill label={rec.affectedRackId} tone="critical" />
        {rec.destinationRackId && (
          <>
            <ArrowRight className="h-4 w-4 text-ink-faint" />
            <Pill label={rec.destinationRackId} tone="good" />
          </>
        )}
        <span
          className={`ml-2 flex items-center gap-1.5 rounded-sm px-3 py-1 font-mono text-xs text-ink-dim ${innerGlassPanel}`}
        >
          <TrendingUp className="h-3 w-3" />
          impact in ~{rec.timeToImpactMinutes} min
        </span>
      </div>

      <div className="relative mt-5 grid gap-2 sm:grid-cols-2">
        {rec.signals.map((s) => (
          <div
            key={s.name}
            className={`flex items-center justify-between rounded-sm px-3.5 py-2.5 text-sm ${innerGlassPanel}`}
          >
            <div>
              <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                {s.name}
              </p>
              <p className="font-mono text-ink">{s.value}</p>
            </div>
            <span
              className="h-2 w-2 rounded-full"
              style={{ background: riskColorHex(s.severity) }}
            />
          </div>
        ))}
      </div>

      {showExplanation && (
        <div className={`relative mt-5 rounded-sm p-4 text-sm text-ink-dim ${innerGlassPanel}`}>
          <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
            Why the agent recommends this
          </p>
          <p className="mt-2 leading-relaxed">{rec.explanation}</p>
          <ul className="mt-3 space-y-1 text-xs text-ink-dim">
            <li>· Ranked signals reflect the dominant contributors to the risk score.</li>
            {rec.destinationRackId && (
              <li>
                · {rec.destinationRackId} was selected as the safest destination with headroom.
              </li>
            )}
            <li>· The agent re-evaluates every telemetry tick and revises if signals change.</li>
          </ul>
        </div>
      )}

      <div className="relative mt-6 flex flex-wrap gap-2">
        <button
          onClick={onAccept}
          className="inline-flex items-center gap-2 rounded-sm bg-heat min-h-11 px-4 py-2.5 font-mono text-xs font-semibold uppercase tracking-wider text-[#0a0a0b] transition hover:bg-heat/90"
        >
          <Check className="h-4 w-4" /> Accept
        </button>
        <button
          onClick={onOverride}
          className="inline-flex items-center gap-2 rounded-sm border border-line bg-surface-2 min-h-11 px-4 py-2.5 font-mono text-xs font-semibold uppercase tracking-wider text-ink transition hover:border-heat/40"
        >
          <ShieldOff className="h-4 w-4" /> Override
        </button>
        <button
          onClick={onAskWhy}
          className="inline-flex items-center gap-2 rounded-sm border border-line bg-transparent min-h-11 px-4 py-2.5 font-mono text-xs font-semibold uppercase tracking-wider text-ink-dim transition hover:text-ink"
        >
          <HelpCircle className="h-4 w-4" /> Ask why
        </button>
      </div>
    </div>
  );
}

function Pill({ label, tone }: { label: string; tone: "critical" | "good" }) {
  const color = tone === "critical" ? "#ff3b3b" : "#34d0a8";
  return (
    <span
      className="rounded-sm border px-2.5 py-1 font-mono text-xs font-semibold"
      style={{ borderColor: `${color}55`, color, background: `${color}18` }}
    >
      {label}
    </span>
  );
}
