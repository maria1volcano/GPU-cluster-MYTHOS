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
