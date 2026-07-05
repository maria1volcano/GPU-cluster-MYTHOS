import { Canvas } from "@react-three/fiber";
import { memo, Suspense, useMemo, useState } from "react";

import { RackMapScene } from "@/components/RackMapScene";
import type { RackMetric } from "@/types/cluster";
import { formatPercent, formatPowerKw, formatScheduledLoad, formatTemperatureC } from "@/lib/formatMetric";
import { riskColorHex } from "@/lib/riskStyles";
import { innerGlassPanel } from "@/lib/glassStyles";

export const RackMap3D = memo(function RackMap3D({
  racks,
  selectedId,
  onSelect,
}: {
  racks: RackMetric[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const stableRacks = useMemo(() => racks, [racks]);

  const hovered = useMemo(
    () => stableRacks.find((r) => r.id === hoveredId) ?? null,
    [hoveredId, stableRacks],
  );

  return (
    <div className="relative w-full overflow-hidden rounded-sm border border-line bg-surface/85 backdrop-blur-sm">
      <div className="relative h-[410px] w-full">
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.03] via-transparent to-transparent" />
        <Canvas
          shadows
          camera={{ position: [0, 7, 11], fov: 42 }}
          dpr={[1, 1.5]}
          gl={{ antialias: true, powerPreference: "high-performance" }}
        >
          <Suspense fallback={null}>
            <RackMapScene
              racks={stableRacks}
              selectedId={selectedId}
              onSelect={onSelect}
              onHover={(r) => setHoveredId(r?.id ?? null)}
            />
          </Suspense>
        </Canvas>

        <div className="pointer-events-none absolute left-4 right-4 top-4 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-widest text-ink-dim">
          {(["healthy", "watch", "warning", "critical"] as const).map((l) => (
            <span
              key={l}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 ${innerGlassPanel}`}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: riskColorHex(l) }} />
              {l}
            </span>
          ))}
        </div>

        {hovered && (
          <div
            className={`pointer-events-none absolute right-4 top-4 min-w-[220px] rounded-sm p-3 text-xs text-ink-dim ${innerGlassPanel}`}
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="font-semibold text-ink">{hovered.label ?? hovered.id}</span>
              <span className="font-mono" style={{ color: riskColorHex(hovered.riskLevel) }}>
                risk {hovered.riskScore ?? "—"}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1 font-mono text-ink-dim">
              <span>Temp</span>
              <span className="text-right text-ink">{formatTemperatureC(hovered.temperatureC)}</span>
              <span>Cooling</span>
              <span className="text-right text-ink">
                {formatPercent(hovered.coolingEfficiencyPct)}
              </span>
              <span>Util</span>
              <span className="text-right text-ink">
                {formatScheduledLoad(hovered.gpuDemandGpus, hovered.gpuUtilizationPct)}
              </span>
              <span>Power</span>
              <span className="text-right text-ink">{formatPowerKw(hovered.powerDrawKw)}</span>
            </div>
          </div>
        )}

        <div className="pointer-events-none absolute bottom-3 left-4 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          Drag to orbit · scroll to zoom · click a rack to inspect
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 border-t border-line bg-surface/90 p-3 sm:grid-cols-4">
        {stableRacks.map((rack) => {
          const active = rack.id === selectedId || rack.id === hoveredId;
          const color = riskColorHex(rack.riskLevel ?? "healthy");
          return (
            <button
              key={rack.id}
              type="button"
              onClick={() => onSelect(rack.id)}
              onMouseEnter={() => setHoveredId(rack.id)}
              onMouseLeave={() => setHoveredId((id) => (id === rack.id ? null : id))}
              className={`rounded-sm border px-2.5 py-2 text-left transition ${innerGlassPanel} ${
                active ? "border-heat/50 bg-heat/10" : "border-line hover:border-heat/30"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] font-semibold uppercase text-ink">
                  {rack.id.replace("rack-", "R")}
                </span>
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
              </div>
              <div className="mt-1.5 space-y-0.5 font-mono text-[10px] leading-tight text-ink-dim">
                <div className="flex justify-between gap-2">
                  <span className="text-ink-faint">Temp</span>
                  <span className="text-ink">{formatTemperatureC(rack.temperatureC)}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-ink-faint">Load</span>
                  <span className="text-ink">
                    {formatScheduledLoad(rack.gpuDemandGpus, rack.gpuUtilizationPct)}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-ink-faint">Power</span>
                  <span className="text-ink">{formatPowerKw(rack.powerDrawKw)}</span>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
});
