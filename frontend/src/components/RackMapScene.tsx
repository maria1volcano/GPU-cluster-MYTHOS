import { OrbitControls, Text } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { memo, useRef } from "react";
import type { Mesh, MeshStandardMaterial } from "three";
import * as THREE from "three";

import { R3F } from "@/components/r3fElements";
import type { RackMetric, RiskLevel } from "@/types/cluster";
import { riskColorHex } from "@/lib/riskStyles";

function riskLevelFromScore(score: number, prev: RiskLevel): RiskLevel {
  if (score >= 78 || (prev === "critical" && score >= 70)) return "critical";
  if (score >= 58 || (prev === "warning" && score >= 50)) return "warning";
  if (score >= 38 || (prev === "watch" && score >= 30)) return "watch";
  return "healthy";
}

type RackVisual = {
  riskScore: number;
  riskLevel: RiskLevel;
  temp: number;
  util: number;
  power: number;
};

const Rack = memo(function Rack({
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
  const matRef = useRef<MeshStandardMaterial>(null);
  const visual = useRef<RackVisual>({
    riskScore: rack.riskScore ?? 0,
    riskLevel: rack.riskLevel ?? "healthy",
    temp: rack.temperatureC ?? 0,
    util: rack.gpuUtilizationPct ?? 0,
    power: rack.powerDrawKw ?? 0,
  });
  const color = useRef(new THREE.Color(riskColorHex(rack.riskLevel ?? "healthy")));
  const targetColor = useRef(new THREE.Color(riskColorHex(rack.riskLevel ?? "healthy")));

  useFrame(({ clock }, delta) => {
    const v = visual.current;
    const blend = 1 - Math.pow(0.02, delta * 60);
    const temp = rack.temperatureC ?? 0;
    const util = rack.gpuUtilizationPct ?? 0;
    const power = rack.powerDrawKw ?? 0;
    const risk = rack.riskScore ?? 0;

    v.temp += (temp - v.temp) * blend;
    v.util += (util - v.util) * blend;
    v.power += (power - v.power) * blend;
    v.riskScore += (risk - v.riskScore) * blend;
    v.riskLevel = riskLevelFromScore(v.riskScore, v.riskLevel);

    targetColor.current.set(riskColorHex(v.riskLevel));
    color.current.lerp(targetColor.current, blend * 0.35);

    const t = clock.getElapsedTime();
    if (glowRef.current) {
      const pulse =
        v.riskLevel === "critical"
          ? 0.55 + Math.sin(t * 2.2) * 0.15
          : v.riskLevel === "warning"
            ? 0.4 + Math.sin(t * 1.2) * 0.08
            : 0.28 + Math.sin(t * 0.8) * 0.04;
      (glowRef.current.material as THREE.MeshBasicMaterial).opacity = pulse;
      (glowRef.current.material as THREE.MeshBasicMaterial).color.copy(color.current);
    }
    if (matRef.current) {
      matRef.current.emissive.copy(color.current);
      matRef.current.emissiveIntensity = selected ? 0.5 : v.riskLevel === "critical" ? 0.38 : 0.22;
    }
    if (meshRef.current) {
      const lift = selected ? 0.12 + Math.sin(t * 1.6) * 0.03 : 0;
      meshRef.current.position.y = THREE.MathUtils.lerp(meshRef.current.position.y, lift, blend);
      const targetScale = 1 + (Math.max(v.util, v.power / 40, (v.temp - 32) * 0.35) / 100) * 0.04;
      meshRef.current.scale.setScalar(
        THREE.MathUtils.lerp(meshRef.current.scale.x, targetScale, blend * 0.5),
      );
    }
  });

  const rackSpacing = 1.55;
  const px = (rack.position?.x ?? 0) * rackSpacing;
  const pz = (rack.position?.z ?? 0) * rackSpacing;
  const slotYs = [0.55, 0.15, -0.25, -0.65];

  return R3F(
    "group",
    { position: [px, 0, pz] },
    R3F(
      "mesh",
      {
        ref: glowRef,
        rotation: [-Math.PI / 2, 0, 0],
        position: [0, -0.48, 0],
      },
      R3F("circleGeometry", { args: [1.05, 32] }),
      R3F("meshBasicMaterial", { color: color.current, transparent: true, opacity: 0.28 }),
    ),
    R3F(
      "mesh",
      {
        ref: meshRef,
        castShadow: true,
        onPointerOver: (e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          onHover(rack);
          document.body.style.cursor = "pointer";
        },
        onPointerOut: () => {
          onHover(null);
          document.body.style.cursor = "default";
        },
        onClick: (e: ThreeEvent<PointerEvent>) => {
          e.stopPropagation();
          onSelect();
        },
      },
      R3F("boxGeometry", { args: [1.05, 1.85, 1.05] }),
      R3F("meshStandardMaterial", {
        ref: matRef,
        color: "#161618",
        metalness: 0.92,
        roughness: 0.18,
        emissive: color.current,
        emissiveIntensity: 0.22,
      }),
    ),
    ...slotYs.map((y, i) =>
      R3F(
        "mesh",
        { key: i, position: [0, y, 0.53] },
        R3F("boxGeometry", { args: [0.82, 0.12, 0.03] }),
        R3F("meshBasicMaterial", { color: color.current, transparent: true, opacity: 0.85 }),
      ),
    ),
    R3F(
      Text,
      {
        position: [0, 1.15, 0],
        fontSize: 0.18,
        color: "#c8c8cc",
        anchorX: "center",
        anchorY: "middle",
        outlineWidth: 0.01,
        outlineColor: "#000000",
      },
      rack.id.replace("rack-", "R"),
    ),
  );
});

function Floor() {
  return (
    <>
      {R3F(
        "mesh",
        {
          rotation: [-Math.PI / 2, 0, 0],
          position: [0, -0.51, 0],
          receiveShadow: true,
        },
        R3F("planeGeometry", { args: [14, 8] }),
        R3F("meshStandardMaterial", { color: "#0e0e10", metalness: 0.6, roughness: 0.85 }),
      )}
      {R3F("gridHelper", { args: [14, 14, "#2a2a30", "#1a1a1f"], position: [0, -0.505, 0] })}
    </>
  );
}

export function RackMapScene({
  racks,
  selectedId,
  onSelect,
  onHover,
}: {
  racks: RackMetric[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onHover: (r: RackMetric | null) => void;
}) {
  const safeRacks = racks.filter((r) => r?.position && Number.isFinite(r.position.x));

  return (
    <>
      {R3F("ambientLight", { intensity: 0.35 })}
      {R3F("directionalLight", { position: [4, 10, 6], intensity: 0.85, castShadow: true })}
      {R3F("pointLight", { position: [-5, 4, -4], intensity: 0.45, color: "#ff6b1a" })}
      {R3F("pointLight", { position: [5, 4, 4], intensity: 0.35, color: "#34d0a8" })}
      <Floor />
      {safeRacks.map((r) => (
        <Rack
          key={r.id}
          rack={r}
          selected={selectedId === r.id}
          onSelect={() => onSelect(r.id)}
          onHover={onHover}
        />
      ))}
      <OrbitControls
        enablePan={false}
        minPolarAngle={Math.PI / 5}
        maxPolarAngle={Math.PI / 2.15}
        minDistance={8}
        maxDistance={18}
        enableDamping
        dampingFactor={0.08}
      />
    </>
  );
}
