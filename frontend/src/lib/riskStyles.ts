import type { RiskLevel } from "@/types/cluster";

// Hex values mirror the brand tokens in styles.css (used for inline styles,
// Three.js materials, and Recharts strokes that can't take utility classes).
export const riskColorHex = (l: RiskLevel) =>
  l === "critical"
    ? "#ff3b3b"
    : l === "warning"
      ? "#ffb200"
      : l === "watch"
        ? "#7f8794"
        : "#34d0a8";

export const riskGlow = (l: RiskLevel) =>
  l === "critical"
    ? "shadow-[0_0_36px_-8px_rgba(255,59,59,0.65)] border-crit/45"
    : l === "warning"
      ? "shadow-[0_0_28px_-14px_rgba(255,178,0,0.4)] border-warn/35"
      : l === "watch"
        ? "border-watch/30"
        : "border-good/25";

export const riskTextClass = (l: RiskLevel) =>
  l === "critical"
    ? "text-crit"
    : l === "warning"
      ? "text-warn"
      : l === "watch"
        ? "text-watch"
        : "text-good";

export const riskLabel = (l: RiskLevel) =>
  ({ critical: "Critical", warning: "Warning", watch: "Watch", healthy: "Healthy" })[l];
