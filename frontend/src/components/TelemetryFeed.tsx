import type { TelemetryEvent } from "@/types/cluster";
import { riskColorHex } from "@/lib/riskStyles";
import { innerGlassPanel, glassPanel } from "@/lib/glassStyles";
import { Radio } from "lucide-react";

export function TelemetryFeed({ events }: { events: TelemetryEvent[] }) {
  return (
    <div className={`flex h-full flex-col ${glassPanel}`}>
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink-dim">
          <Radio className="h-3 w-3 animate-pulse text-good" />
          Live telemetry
        </div>
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {events.length} events
        </span>
      </div>
      <div
        aria-live="polite"
        aria-label="Live telemetry feed"
        className="max-h-[440px] flex-1 space-y-1.5 overflow-y-auto p-4"
      >
        {events.length === 0 && (
          <p className="pt-6 text-center text-xs text-ink-faint">Waiting for telemetry…</p>
        )}
        {events.map((e) => (
          <div
            key={e.id}
            className={`flex items-start gap-3 rounded-sm px-3 py-2 text-xs animate-in fade-in slide-in-from-top-1 ${innerGlassPanel}`}
          >
            <span
              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: riskColorHex(e.severity) }}
            />
            <div className="flex-1">
              <p className="text-ink">{e.message}</p>
              <p className="mt-0.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                {new Date(e.timestamp).toLocaleTimeString()} · {e.type.replace("_", " ")}
                {e.rackId && ` · ${e.rackId}`}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
