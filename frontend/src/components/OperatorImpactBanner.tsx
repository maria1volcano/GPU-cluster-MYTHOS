import { CheckCircle2, ShieldOff, X } from "lucide-react";

import type { OperatorActionResult } from "@/types/cluster";
import { riskColorHex } from "@/lib/riskStyles";

export function OperatorImpactBanner({
  impact,
  onDismiss,
}: {
  impact: OperatorActionResult;
  onDismiss: () => void;
}) {
  const isApproved = impact.action === "approved";
  const isAverted = impact.outcome === "averted";
  const accent = isApproved ? (isAverted ? "#34d0a8" : "#ffb200") : "#ff6b1a";
  const Icon = isApproved ? CheckCircle2 : ShieldOff;

  return (
    <div
      className="mb-4 animate-in fade-in slide-in-from-top-2 rounded-sm border px-4 py-4 shadow-lg duration-300"
      style={{
        borderColor: `${accent}66`,
        background: `linear-gradient(90deg, ${accent}18, transparent)`,
      }}
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-5 w-5 shrink-0" style={{ color: accent }} />
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[10px] uppercase tracking-[0.24em]" style={{ color: accent }}>
            {isApproved ? "Operator approved" : "Operator override"}
          </p>
          <p className="mt-1 text-base font-semibold text-ink">{impact.title}</p>
          <p className="mt-1 text-sm leading-relaxed text-ink-dim">{impact.detail}</p>
          {(impact.fromRack || impact.toRack || impact.jobId) && (
            <div className="mt-3 flex flex-wrap gap-2 font-mono text-[11px] uppercase tracking-wider">
              {impact.jobId && (
                <span className="rounded-sm border border-line bg-surface px-2 py-1 text-ink">
                  Job {impact.jobId}
                </span>
              )}
              {impact.fromRack && (
                <span
                  className="rounded-sm border px-2 py-1"
                  style={{ borderColor: `${riskColorHex("critical")}55`, color: riskColorHex("critical") }}
                >
                  From {impact.fromRack}
                </span>
              )}
              {impact.toRack && isApproved && (
                <span
                  className="rounded-sm border px-2 py-1"
                  style={{ borderColor: `${riskColorHex("healthy")}55`, color: riskColorHex("healthy") }}
                >
                  To {impact.toRack}
                </span>
              )}
            </div>
          )}
          {impact.expectedEffect && isApproved && (
            <p className="mt-2 text-xs text-ink-faint">Expected: {impact.expectedEffect}</p>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded-sm p-1 text-ink-faint transition hover:bg-surface-2 hover:text-ink"
          aria-label="Dismiss impact summary"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
