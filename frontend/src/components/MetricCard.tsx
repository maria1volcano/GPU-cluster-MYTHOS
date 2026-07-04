import { AnimatedNumber } from "./AnimatedNumber";
import type { LucideIcon } from "lucide-react";
import { innerGlassPanel } from "@/lib/glassStyles";

export function MetricCard({
  label,
  value,
  suffix = "",
  decimals = 0,
  icon: Icon,
  tone = "default",
  hint,
  loading = false,
}: {
  label: string;
  value: number;
  suffix?: string;
  decimals?: number;
  icon?: LucideIcon;
  tone?: "default" | "warning" | "critical" | "good";
  hint?: string;
  loading?: boolean;
}) {
  const accent =
    tone === "critical"
      ? "border-l-crit"
      : tone === "warning"
        ? "border-l-warn"
        : tone === "good"
          ? "border-l-good"
          : "border-l-line";

  return (
    <div
      className={`group relative overflow-hidden rounded-sm border border-line border-l-2 ${accent} bg-surface p-4 transition-colors hover:bg-surface-2`}
    >
      <div className="relative flex items-start justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-dim">{label}</p>
          {loading ? (
            <span className="mt-3 block h-6 w-16 animate-pulse rounded-sm bg-surface-2" />
          ) : (
            <p className="mt-2.5 font-mono text-2xl font-semibold text-ink">
              <AnimatedNumber value={value} decimals={decimals} suffix={suffix} />
            </p>
          )}
          {hint && <p className="mt-1 text-xs text-ink-faint">{hint}</p>}
        </div>
        {Icon && (
          <div className={`rounded-sm p-2.5 text-ink-dim ${innerGlassPanel}`}>
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>
    </div>
  );
}
