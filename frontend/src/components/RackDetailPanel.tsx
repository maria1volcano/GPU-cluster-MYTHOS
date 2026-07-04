import type { RackMetric } from "@/types/cluster";
import { riskColorHex, riskLabel, riskTextClass } from "@/lib/riskStyles";
import { innerGlassPanel, glassPanel } from "@/lib/glassStyles";
import { LineChart, Line, ResponsiveContainer, YAxis, Tooltip } from "recharts";
import { Cpu, Thermometer, Wind, Zap, Activity, X } from "lucide-react";

export function RackDetailPanel({
  rack,
  destinationHint,
  onClose,
}: {
  rack: RackMetric | null;
  destinationHint?: string;
  onClose: () => void;
}) {
  if (!rack) {
    return (
      <div className={`h-full p-6 text-sm text-ink-dim ${glassPanel}`}>
        <p className="font-mono text-xs uppercase tracking-[0.18em] text-ink-faint">Rack detail</p>
        <p className="mt-3">
          Click a rack in the cluster map to inspect live metrics and agent notes.
        </p>
      </div>
    );
  }
  const history = rack.history ?? [];
  const color = riskColorHex(rack.riskLevel);

  return (
    <div className={`relative h-full overflow-hidden p-6 ${glassPanel}`}>
      <button
        onClick={onClose}
        aria-label="Close rack detail"
        className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded-sm border border-line bg-surface-2 text-ink-dim transition hover:text-ink"
      >
        <X className="h-3.5 w-3.5" />
      </button>
      <div className="flex items-center gap-3">
        <div
          className="h-9 w-9 rounded-sm border border-line"
          style={{ background: `radial-gradient(circle at 30% 30%, ${color}, transparent 70%)` }}
        />
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-ink-faint">Rack</p>
          <p className="text-lg font-semibold text-ink">{rack.label}</p>
        </div>
        <div className="ml-auto text-right">
          <p className="font-mono text-xs uppercase tracking-[0.18em] text-ink-faint">Risk</p>
          <p className={`font-mono text-lg font-semibold ${riskTextClass(rack.riskLevel)}`}>
            {rack.riskScore} · {riskLabel(rack.riskLevel)}
          </p>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3">
        <MiniStat
          icon={<Thermometer className="h-3.5 w-3.5" />}
          label="Temperature"
          value={`${rack.temperatureC.toFixed(1)}°C`}
        />
        <MiniStat
          icon={<Activity className="h-3.5 w-3.5" />}
          label="Trend"
          value={`${rack.temperatureTrendCPerMin >= 0 ? "+" : ""}${rack.temperatureTrendCPerMin.toFixed(1)}°C/min`}
        />
        <MiniStat
          icon={<Zap className="h-3.5 w-3.5" />}
          label="Power"
          value={`${rack.powerDrawKw.toFixed(0)} kW`}
        />
        <MiniStat
          icon={<Cpu className="h-3.5 w-3.5" />}
          label="GPU util"
          value={`${rack.gpuUtilizationPct.toFixed(0)}%`}
        />
        <MiniStat
          icon={<Wind className="h-3.5 w-3.5" />}
          label="Cooling"
          value={`${rack.coolingEfficiencyPct.toFixed(0)}%`}
        />
        <MiniStat
          icon={<Activity className="h-3.5 w-3.5" />}
          label="Queue"
          value={`${rack.queuePressurePct.toFixed(0)}%`}
        />
      </div>

      <div className="mt-5">
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-faint">
          Temperature (30 ticks)
        </p>
        <div className="mt-2 h-24">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={history}>
              <YAxis hide domain={["dataMin-2", "dataMax+2"]} />
              <Tooltip
                contentStyle={{
                  background: "#141416",
                  border: "1px solid #2a2a2e",
                  borderRadius: 2,
                  fontSize: 11,
                }}
                labelStyle={{ display: "none" }}
                formatter={(v: number) => [`${v.toFixed(1)}°C`, "temp"]}
              />
              <Line type="monotone" dataKey="temp" stroke={color} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <ChartBlock label="Power (kW)" data={history} dataKey="power" color="#7f8794" />
        <ChartBlock label="GPU util (%)" data={history} dataKey="util" color="#34d0a8" />
      </div>

      <div className={`mt-5 rounded-sm p-4 text-sm ${innerGlassPanel}`}>
        <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-faint">
          Active workload
        </p>
        <p className="mt-1 text-ink">
          {rack.activeJobId ? (
            <>
              <span className="font-mono">{rack.activeJobId}</span> ·{" "}
              <span className="text-ink-dim">{rack.workloadType}</span>
            </>
          ) : (
            <span className="text-ink-faint">Idle</span>
          )}
        </p>
        {destinationHint && (
          <p className="mt-2 text-xs text-ink-dim">
            Agent notes: safer destination is <span className="text-ink">{destinationHint}</span>.
          </p>
        )}
      </div>
    </div>
  );
}

function MiniStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className={`rounded-sm p-3 ${innerGlassPanel}`}>
      <div className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        {icon}
        {label}
      </div>
      <p className="mt-1.5 font-mono text-sm font-semibold text-ink tabular-nums">{value}</p>
    </div>
  );
}

function ChartBlock({
  label,
  data,
  dataKey,
  color,
}: {
  label: string;
  data: { t: number; temp: number; power: number; util: number }[];
  dataKey: "power" | "util";
  color: string;
}) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">{label}</p>
      <div className="mt-1 h-16">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <YAxis hide domain={["auto", "auto"]} />
            <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.5} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
