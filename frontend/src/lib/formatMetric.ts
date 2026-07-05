/** Safe numeric formatting for dashboard metrics — never throws on missing data. */
export function formatMetric(
  value: number | null | undefined,
  format: (n: number) => string,
  fallback = "—",
): string {
  if (value == null || Number.isNaN(value)) return fallback;
  return format(value);
}

export function formatTemperatureC(value: number | null | undefined): string {
  const core = formatMetric(value, (n) => n.toFixed(1));
  return core === "—" ? core : `${core}°C`;
}

export function formatPercent(value: number | null | undefined, digits = 0): string {
  const core = formatMetric(value, (n) => n.toFixed(digits));
  return core === "—" ? core : `${core}%`;
}

export function formatPowerKw(value: number | null | undefined): string {
  const core = formatMetric(value, (n) => n.toFixed(1));
  return core === "—" ? core : `${core} kW`;
}

function formatLoadPct(value: number | null | undefined): string {
  return formatMetric(value, (n) => (n > 0 && n < 10 ? n.toFixed(1) : n.toFixed(0)));
}

/** Scheduled GPU load: demand in GPU-equivalents plus capacity %. */
export function formatScheduledLoad(
  demandGpus: number | null | undefined,
  utilPct: number | null | undefined,
): string {
  const demandNum = demandGpus ?? 0;
  const utilNum = utilPct ?? 0;
  if (demandNum <= 0 && utilNum <= 0) return "idle";

  const demand = formatMetric(demandGpus, (n) => n.toFixed(1));
  const pct = formatLoadPct(utilPct);
  if (demand === "—" && pct === "—") return "—";
  if (demand === "—") return `${pct}%`;
  if (pct === "—") return `${demand} GPU`;
  return `${pct}% · ${demand} GPU`;
}
