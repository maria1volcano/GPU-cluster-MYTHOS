import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { useMemo, useRef, useState, Suspense } from "react";
import type { Mesh } from "three";
import * as THREE from "three";
import type { RackMetric } from "@/types/cluster";
import { riskColorHex } from "@/lib/riskStyles";
import { innerGlassPanel } from "@/lib/glassStyles";

function Rack({
  rack,
  selected,
  onSelect,
  onHover,
}: {
  rack: RackMetric;
  selected: boolean;
  onSelect: () => void;
  onHover: (r: RackMetric | null) => void;
}) {
  const meshRef = useRef<Mesh>(null);
  const glowRef = useRef<Mesh>(null);
  const color = useMemo(() => new THREE.Color(riskColorHex(rack.riskLevel)), [rack.riskLevel]);
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    if (glowRef.current) {
      const pulse =
        rack.riskLevel === "critical"
          ? 0.6 + Math.sin(t * 4) * 0.25
          : 0.35 + Math.sin(t * 1.5) * 0.08;
      (glowRef.current.material as THREE.MeshBasicMaterial).opacity = pulse;
    }
    if (meshRef.current) {
      meshRef.current.position.y = selected ? 0.15 + Math.sin(t * 2) * 0.05 : 0;
    }
  });

  return (
    <group position={[rack.position.x, 0, rack.position.z ?? 0]}>
      {/* Glow disc */}
      <mesh ref={glowRef} rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.5, 0]}>
        <circleGeometry args={[1.1, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.35} />
      </mesh>
      {/* Rack body */}
      <mesh
        ref={meshRef}
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover(rack);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          onHover(null);
          document.body.style.cursor = "default";
        }}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
      >
        <boxGeometry args={[1.2, 2, 1.2]} />
        <meshStandardMaterial
          color="#141416"
          metalness={0.85}
          roughness={0.25}
          emissive={color}
          emissiveIntensity={selected ? 0.55 : 0.28}
        />
      </mesh>
      {/* GPU slot lights */}
      {[0.6, 0.2, -0.2, -0.6].map((y, i) => (
        <mesh key={i} position={[0, y, 0.61]}>
          <boxGeometry args={[0.9, 0.15, 0.04]} />
          <meshBasicMaterial color={color} />
        </mesh>
      ))}
    </group>
  );
}

function Floor() {
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.51, 0]} receiveShadow>
      <planeGeometry args={[24, 16]} />
      <meshStandardMaterial
        color="#141416"
        transparent
        opacity={0.4}
        metalness={0.5}
        roughness={0.9}
      />
    </mesh>
  );
}

export function RackMap3D({
  racks,
  selectedId,
  onSelect,
}: {
  racks: RackMetric[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  const [hovered, setHovered] = useState<RackMetric | null>(null);
  return (
    <div className="relative h-[410px] w-full overflow-hidden rounded-sm border border-line bg-surface/85 backdrop-blur-sm">
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/[0.03] via-transparent to-transparent" />
      <Canvas camera={{ position: [0, 5, 8], fov: 45 }} dpr={[1, 2]}>
        <Suspense fallback={null}>
          <ambientLight intensity={0.3} />
          <directionalLight position={[5, 8, 5]} intensity={0.8} />
          <pointLight position={[-6, 4, -6]} intensity={0.6} color="#ff6b1a" />
          <pointLight position={[6, 4, 6]} intensity={0.5} color="#34d0a8" />
          <Floor />
          {racks.map((r) => (
            <Rack
              key={r.id}
              rack={r}
              selected={selectedId === r.id}
              onSelect={() => onSelect(r.id)}
              onHover={setHovered}
            />
          ))}
          <OrbitControls
            enablePan={false}
            minPolarAngle={Math.PI / 6}
            maxPolarAngle={Math.PI / 2.2}
            minDistance={6}
            maxDistance={16}
          />
        </Suspense>
      </Canvas>

      {/* Overlay legend */}
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
            <span className="font-semibold text-ink">{hovered.label}</span>
            <span className="font-mono" style={{ color: riskColorHex(hovered.riskLevel) }}>
              risk {hovered.riskScore}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-1 font-mono text-ink-dim">
            <span>Temp</span>
            <span className="text-right text-ink">{hovered.temperatureC.toFixed(1)}°C</span>
            <span>Cooling</span>
            <span className="text-right text-ink">{hovered.coolingEfficiencyPct.toFixed(0)}%</span>
            <span>Util</span>
            <span className="text-right text-ink">{hovered.gpuUtilizationPct.toFixed(0)}%</span>
            <span>Power</span>
            <span className="text-right text-ink">{hovered.powerDrawKw.toFixed(0)} kW</span>
          </div>
        </div>
      )}

      <div className="pointer-events-none absolute bottom-3 left-4 font-mono text-[10px] uppercase tracking-widest text-ink-faint">
        Drag to orbit · scroll to zoom · click a rack to inspect
      </div>
    </div>
  );
}
