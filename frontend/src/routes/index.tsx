import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  BrainCircuit,
  Cpu,
  Flame,
  Gauge,
  Play,
  Radar,
  ShieldAlert,
  Sparkles,
  Thermometer,
  Wind,
  Zap,
} from "lucide-react";

import { BackgroundLayer } from "@/components/BackgroundLayer";
import { LiquidLogo } from "@/components/LiquidLogo";
import { MetricCard } from "@/components/MetricCard";
import { RackMap3D } from "@/components/RackMap3D";
import { RackDetailPanel } from "@/components/RackDetailPanel";
import { AgentRecommendationPanel } from "@/components/AgentRecommendationPanel";
import { TelemetryFeed } from "@/components/TelemetryFeed";
import { OverrideModal } from "@/components/OverrideModal";
import { IntegrationStatus } from "@/components/IntegrationStatus";
import { ExplanationDrawer } from "@/components/ExplanationDrawer";
import { glassPanel, innerGlassPanel } from "@/lib/glassStyles";
import { riskColorHex, riskLabel } from "@/lib/riskStyles";

import {
  acceptRecommendation,
  apiConfig,
  askWhy,
  fetchDashboard as loadDashboard,
  overrideRecommendation,
  pauseReplay,
  startReplay,
  triggerStressScenario,
} from "@/lib/api";
import { useRecommendationAlert } from "@/lib/alertAudio";
import type {
  AgentRecommendation,
  ClusterState,
  RackMetric,
  TelemetryEvent,
} from "@/types/cluster";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Mythos 6 — Predict. Explain. Rebalance." },
      {
        name: "description",
        content:
          "Mythos 6 is a GPU cluster ops agent that monitors live infrastructure, predicts rack-level thermal throttling before it happens, and gives operators one-tap workload migration recommendations.",
      },
      { property: "og:title", content: "Mythos 6 — GPU Cluster Ops Agent" },
      {
        property: "og:description",
        content: "Predict thermal throttling. Explain the cause. Rebalance in one tap.",
      },
    ],
  }),
  component: Dashboard,
});

function Dashboard() {
  const queryClient = useQueryClient();
  const [selectedRackId, setSelectedRackId] = useState<string | undefined>();
  const [running, setRunning] = useState(true);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [showExplanation, setShowExplanation] = useState(false);
  const replayBooted = useRef(false);

  const { data, isPending, isFetching, isError, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: loadDashboard,
    enabled: typeof window !== "undefined",
    refetchInterval: running ? 1500 : false,
    refetchOnWindowFocus: false,
    retry: 2,
    staleTime: 500,
  });
  const state = data?.state ?? null;
  const rec = data?.rec ?? null;
  const events = data?.events ?? [];
  const loading = !state && (isPending || isFetching);

  useRecommendationAlert(rec);

  useEffect(() => {
    if (apiConfig.mode !== "live" || replayBooted.current) return;
    replayBooted.current = true;
    startReplay().catch((err) => console.warn("Failed to start backend replay", err));
  }, []);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["dashboard"] });

  const handleToggleRunning = async () => {
    const next = !running;
    if (apiConfig.mode === "live") {
      try {
        if (next) await startReplay();
        else await pauseReplay();
      } catch (err) {
        console.warn("Replay control failed", err);
        return;
      }
    }
    setRunning(next);
  };

  const acceptMutation = useMutation({
    mutationFn: (r: AgentRecommendation) => acceptRecommendation(r),
    onSuccess: () => {
      setShowExplanation(false);
      refresh();
    },
  });
  const overrideMutation = useMutation({
    mutationFn: ({ r, reason }: { r: AgentRecommendation; reason: string }) =>
      overrideRecommendation(r, reason),
    onSuccess: refresh,
  });
  const askWhyMutation = useMutation({
    mutationFn: (r: AgentRecommendation) => askWhy(r),
    onSuccess: refresh,
  });

  const selectedRack = useMemo(
    () => state?.racks.find((r) => r.id === selectedRackId) ?? null,
    [state, selectedRackId],
  );

  const topRack = useMemo(() => {
    if (!state?.racks?.length) return null;
    return [...state.racks].sort((a, b) => b.riskScore - a.riskScore)[0];
  }, [state]);

  const handleAccept = () => {
    if (rec) acceptMutation.mutate(rec);
  };
  const handleOverride = (reason: string) => {
    if (rec) overrideMutation.mutate({ r: rec, reason });
  };
  const handleAskWhy = () => {
    if (!rec) return;
    setShowExplanation((v) => !v);
    askWhyMutation.mutate(rec);
  };
  const handleViewPredictionLogic = () => {
    setShowExplanation(true);
    document
      .getElementById("agent-recommendation")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (rec) askWhyMutation.mutate(rec);
  };

  const startStress = () => {
    void triggerStressScenario().then(() => refresh());
  };

  return (
    <main className="min-h-screen text-ink">
      <BackgroundLayer />

      <div className="mx-auto max-w-[1440px] px-6 py-7 lg:px-10 lg:py-8">
        {isError && (
          <div className="mb-4 rounded-sm border border-crit/40 bg-crit/10 px-4 py-3 text-sm text-crit">
            Live backend unreachable — start it with{" "}
            <code className="font-mono text-xs">python -m sentinel.server</code>
            {error instanceof Error ? `: ${error.message}` : ""}
          </div>
        )}
        <HeroSection
          running={running}
          onToggleRunning={() => void handleToggleRunning()}
          onStress={startStress}
          onViewLogic={handleViewPredictionLogic}
          rec={rec}
          topRack={topRack}
          loading={loading}
        />

        <WhyItMatters />

        <section className="mt-14 flex flex-wrap items-end justify-between gap-5">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-sm border border-heat/25 bg-heat/10 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.24em] text-heat">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-heat" />
              Live
            </div>
            <h2 className="text-3xl font-semibold tracking-tight text-ink md:text-4xl">
              Live Operator Console
            </h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-ink-dim">
              Replay cluster conditions, inspect predicted risk, and approve or override agent
              recommendations.
            </p>
          </div>
          <ExplanationDrawer />
        </section>

        {/* Metrics grid */}
        <section className="stagger mt-6 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          <MetricCard
            label="Total racks"
            value={state?.racks.length ?? 0}
            icon={Cpu}
            loading={loading}
          />
          <MetricCard
            label="Active jobs"
            value={state?.activeJobs ?? 0}
            icon={Activity}
            loading={loading}
          />
          <MetricCard
            label="Avg temperature"
            value={state?.averageTemperatureC ?? 0}
            decimals={1}
            suffix="°C"
            icon={Thermometer}
            tone={state && state.averageTemperatureC > 75 ? "warning" : "default"}
            loading={loading}
          />
          <MetricCard
            label="Power draw"
            value={state?.totalPowerDrawKw ?? 0}
            suffix=" kW"
            icon={Zap}
            loading={loading}
          />
          <MetricCard
            label="Cooling avg"
            value={state?.averageCoolingEfficiencyPct ?? 0}
            suffix="%"
            icon={Wind}
            tone={state && state.averageCoolingEfficiencyPct < 70 ? "warning" : "good"}
            loading={loading}
          />
          <MetricCard
            label="Throttling risks"
            value={
              state?.racks.filter((r) => r.riskLevel === "critical" || r.riskLevel === "warning")
                .length ?? 0
            }
            icon={ShieldAlert}
            tone={rec?.riskLevel === "critical" ? "critical" : "default"}
            hint="racks needing attention"
            loading={loading}
          />
          <MetricCard
            label="Agent confidence"
            value={state?.agentConfidencePct ?? 0}
            suffix="%"
            icon={Sparkles}
            tone="good"
            loading={loading}
          />
        </section>

        {/* Main grid */}
        <section className="mt-5 grid gap-5 xl:grid-cols-[1.5fr_1fr]">
          <div className="min-w-0 space-y-6">
            <div className="relative">
              <SectionHeader
                icon={<Gauge className="h-3.5 w-3.5" />}
                title="Cluster map"
                hint="8-rack GPU floor"
              />
              <RackMap3D
                racks={state?.mapRacks ?? state?.racks.slice(0, 8) ?? []}
                selectedId={selectedRackId}
                onSelect={setSelectedRackId}
              />
            </div>

            <div id="agent-recommendation">
              <SectionHeader
                icon={<Sparkles className="h-3.5 w-3.5" />}
                title="Agent recommendation"
                hint="Rebalance in one tap"
              />
              <AgentRecommendationPanel
                rec={rec}
                onAccept={handleAccept}
                onOverride={() => setOverrideOpen(true)}
                onAskWhy={handleAskWhy}
                showExplanation={showExplanation}
              />
            </div>

            <IntegrationStatus />
          </div>

          <div className="min-w-0 space-y-6">
            <div>
              <SectionHeader icon={<Cpu className="h-3.5 w-3.5" />} title="Rack detail" />
              <RackDetailPanel
                rack={selectedRack}
                destinationHint={
                  rec?.affectedRackId === selectedRack?.id ? rec?.destinationRackId : undefined
                }
                onClose={() => setSelectedRackId(undefined)}
              />
            </div>
            <div>
              <SectionHeader icon={<Activity className="h-3.5 w-3.5" />} title="Operations feed" />
              <TelemetryFeed events={events} />
            </div>
          </div>
        </section>

        <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-6 font-mono text-[11px] uppercase tracking-widest text-ink-faint">
          <span>Mythos 6 · GPU Cluster Ops Agent</span>
          <span>Built for the RAISE AI Hackathon · Paris</span>
        </footer>
      </div>

      <OverrideModal
        open={overrideOpen}
        onClose={() => setOverrideOpen(false)}
        onSubmit={handleOverride}
      />
    </main>
  );
}

function HeroSection({
  running,
  onToggleRunning,
  onStress,
  onViewLogic,
  rec,
  topRack,
  loading,
}: {
  running: boolean;
  onToggleRunning: () => void;
  onStress: () => void;
  onViewLogic: () => void;
  rec: AgentRecommendation | null;
  topRack: RackMetric | null;
  loading: boolean;
}) {
  return (
    <section className="relative overflow-hidden py-10 md:py-16">
      <div className="absolute left-0 top-10 h-px w-48 bg-gradient-to-r from-heat via-heat/30 to-transparent" />
      <div className="grid items-center gap-10 lg:grid-cols-[1.08fr_0.92fr]">
        <div className="stagger min-w-0 max-w-4xl">
          <LiquidLogo />
          <div className="mt-10 inline-flex items-center gap-2 rounded-sm border border-heat/25 bg-heat/10 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.22em] text-heat">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-heat shadow-[0_0_14px_rgba(255,107,26,0.9)]" />
            GPU Cluster Ops Agent
          </div>
          <h1 className="mt-5 font-display text-4xl font-bold uppercase tracking-tight text-ink sm:text-5xl md:text-7xl">
            Mythos 6
          </h1>
          <p className="mt-4 font-display text-2xl font-semibold tracking-tight text-heat sm:text-3xl md:text-5xl">
            Predict · Explain · Rebalance
          </p>
          <p className="mt-6 max-w-2xl text-base leading-7 text-ink-dim md:text-lg">
            An AI operations agent that watches live GPU cluster telemetry, predicts infrastructure
            risk before it hits, and recommends one-tap workload actions operators can trust or
            override.
          </p>

          <div className="mt-9 flex flex-wrap gap-3">
            <button
              onClick={onToggleRunning}
              className="inline-flex items-center gap-2 rounded-sm bg-heat min-h-11 px-5 py-3 font-mono text-xs font-semibold uppercase tracking-wider text-[#0a0a0b] shadow-[0_16px_40px_-18px_rgba(255,107,26,0.7)] transition hover:bg-heat/90"
            >
              <Play className="h-4 w-4" />
              {running ? "Pause simulation" : "Start simulation"}
            </button>
            <button
              onClick={onStress}
              className="inline-flex items-center gap-2 rounded-sm border border-crit/40 bg-crit/10 min-h-11 px-5 py-3 font-mono text-xs font-semibold uppercase tracking-wider text-crit transition hover:bg-crit/20"
            >
              <Flame className="h-4 w-4" />
              Trigger stress scenario
            </button>
            <button
              onClick={onViewLogic}
              className="inline-flex items-center gap-2 rounded-sm border border-line bg-surface min-h-11 px-5 py-3 font-mono text-xs font-semibold uppercase tracking-wider text-ink transition hover:border-heat/40 hover:bg-surface-2"
            >
              <BrainCircuit className="h-4 w-4" />
              View prediction logic
            </button>
          </div>
        </div>

        <LiveRiskPreview rec={rec} topRack={topRack} loading={loading} />
      </div>
    </section>
  );
}

function LiveRiskPreview({
  rec,
  topRack,
  loading,
}: {
  rec: AgentRecommendation | null;
  topRack: RackMetric | null;
  loading: boolean;
}) {
  // Bind to the live cluster: the loudest thing on screen is the real highest-risk
  // rack, escalating calm → watch → warning → critical. This card IS the alarm.
  const level = rec?.riskLevel ?? topRack?.riskLevel ?? "healthy";
  const color = riskColorHex(level);
  const isCritical = level === "critical";
  const hasRisk = level !== "healthy";
  const signals = rec?.signals?.slice(0, 3) ?? [];

  return (
    <div
      className={`relative min-w-0 rounded-sm bg-surface/85 p-6 backdrop-blur-sm transition-all duration-500 ${isCritical ? "alarm-critical border-2" : "border"}`}
      style={{
        borderColor: isCritical ? color : `${color}55`,
        // critical box-shadow is driven by the .alarm-critical breathe keyframe
        boxShadow: isCritical ? undefined : `0 30px 90px -50px rgba(0,0,0,0.9)`,
      }}
    >
      <div
        className="absolute inset-x-6 top-0 h-px"
        style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }}
      />

      <div className="relative flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div
            className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.24em]"
            style={{ color }}
          >
            <Radar className="h-3.5 w-3.5" />
            Live Risk Preview
          </div>
          <h2 className="mt-4 text-2xl font-semibold tracking-tight text-ink md:text-3xl">
            {loading && !topRack
              ? "Initializing telemetry…"
              : topRack
                ? `${topRack.label} projected throttling risk`
                : "Cluster nominal"}
          </h2>
        </div>
        <div
          className={`flex shrink-0 items-center gap-2 rounded-sm px-3 py-1.5 font-mono text-[11px] font-semibold uppercase tracking-widest ${isCritical ? "border-2" : "border"}`}
          style={{
            borderColor: `${color}${isCritical ? "" : "55"}`,
            background: `${color}1a`,
            color,
          }}
        >
          <span
            className={`h-2 w-2 rounded-full ${isCritical ? "animate-pulse" : ""}`}
            style={{ background: color, boxShadow: isCritical ? `0 0 12px ${color}` : "none" }}
          />
          {riskLabel(level)}
        </div>
      </div>

      <div className="relative mt-7 grid grid-cols-3 gap-3">
        <RiskStat
          label="Risk score"
          value={topRack ? String(topRack.riskScore) : "—"}
          tone={hasRisk ? color : undefined}
        />
        <RiskStat label="Impact" value={rec ? `${rec.timeToImpactMinutes} min` : "—"} />
        <RiskStat label="Rack" value={topRack?.id ?? "—"} />
      </div>

      <div className={`relative mt-5 rounded-sm p-4 ${innerGlassPanel}`}>
        <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-ink-faint">
          Recommended action
        </p>
        {rec ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-lg font-semibold text-ink">
            <span>{rec.title}</span>
            {rec.destinationRackId && (
              <>
                <ArrowRight className="h-4 w-4 text-heat" />
                <span className="text-good">{rec.destinationRackId}</span>
              </>
            )}
          </div>
        ) : (
          <p className="mt-3 text-lg font-semibold text-ink">
            No action required — thermal envelope nominal.
          </p>
        )}
      </div>

      {signals.length > 0 && (
        <div className="relative mt-5 space-y-2">
          {signals.map((s) => (
            <div
              key={s.name}
              className={`flex items-center justify-between rounded-sm px-3.5 py-3 text-sm ${innerGlassPanel}`}
            >
              <div className="flex items-center gap-2 text-ink-dim">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ background: riskColorHex(s.severity) }}
                />
                {s.name}
              </div>
              <span className="font-mono text-[11px] text-ink">{s.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RiskStat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`min-w-0 rounded-sm p-3 sm:p-4 ${innerGlassPanel}`}>
      <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">{label}</p>
      <p
        className="mt-2 whitespace-nowrap font-mono text-xl font-semibold text-ink sm:text-2xl"
        style={tone ? { color: tone } : undefined}
      >
        {value}
      </p>
    </div>
  );
}

function WhyItMatters() {
  const items = [
    {
      title: "Predict before failure",
      text: "Detect projected throttling and node instability before workloads degrade.",
      icon: <Radar className="h-4 w-4" />,
    },
    {
      title: "Explain every action",
      text: "Show the telemetry signals behind each recommendation.",
      icon: <BrainCircuit className="h-4 w-4" />,
    },
    {
      title: "Keep humans in control",
      text: "Operators can approve, override, or inspect the agent's reasoning.",
      icon: <ShieldAlert className="h-4 w-4" />,
    },
  ];

  return (
    <section className="stagger grid gap-3 md:grid-cols-3">
      {items.map((item) => (
        <div key={item.title} className={`${glassPanel} p-5`}>
          <div className="flex h-9 w-9 items-center justify-center rounded-sm border border-line bg-surface-2 text-heat">
            {item.icon}
          </div>
          <h3 className="mt-4 text-base font-semibold text-ink">{item.title}</h3>
          <p className="mt-2 text-sm leading-6 text-ink-dim">{item.text}</p>
        </div>
      ))}
    </section>
  );
}

function SectionHeader({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="mb-3 flex items-center justify-between">
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.22em] text-ink-dim">
        {icon}
        {title}
      </div>
      {hint && (
        <span className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
          {hint}
        </span>
      )}
    </div>
  );
}
