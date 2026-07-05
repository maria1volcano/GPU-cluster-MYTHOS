import { useEffect, useState } from "react";
import { ChevronDown, Info } from "lucide-react";
import { apiConfig, pingBackend } from "@/lib/api";
import { useVoiceSettings } from "@/lib/useVoiceSettings";
import { VoiceAudioToggle } from "@/components/VoiceAudioToggle";
import { softGlassPanel } from "@/lib/glassStyles";

export function IntegrationStatus() {
  const [open, setOpen] = useState(false);
  const [reachable, setReachable] = useState<boolean | null>(apiConfig.backendReachable);
  const [voice, updateVoice] = useVoiceSettings();

  useEffect(() => {
    if (apiConfig.mode === "mock") {
      setReachable(true);
      return;
    }
    void pingBackend().then(setReachable);
    const id = window.setInterval(() => {
      void pingBackend().then(setReachable);
    }, 5000);
    return () => window.clearInterval(id);
  }, []);

  const liveOk = apiConfig.mode === "live" && reachable === true;
  const liveDown = apiConfig.mode === "live" && reachable === false;
  const subVoiceDisabled = !voice.audioEnabled;

  return (
    <div className={softGlassPanel}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="Toggle backend integration status"
        className="flex w-full items-center justify-between px-5 py-3.5 text-left"
      >
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-ink-dim">
          <Info className="h-3 w-3" /> Backend integration status
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`rounded-sm border px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-widest ${
              apiConfig.mode === "mock"
                ? "border-warn/40 bg-warn/10 text-warn"
                : liveOk
                  ? "border-good/40 bg-good/10 text-good"
                  : liveDown
                    ? "border-crit/40 bg-crit/10 text-crit"
                    : "border-warn/40 bg-warn/10 text-warn"
            }`}
          >
            {apiConfig.mode === "mock"
              ? "Mock data"
              : liveOk
                ? "Live backend"
                : liveDown
                  ? "Backend offline"
                  : "Checking…"}
          </span>
          <ChevronDown className={`h-4 w-4 text-ink-dim transition ${open ? "rotate-180" : ""}`} />
        </div>
      </button>
      {open && (
        <div className="grid gap-3 border-t border-line p-5 text-xs text-ink-dim sm:grid-cols-2">
          <Row k="API mode" v={apiConfig.mode} />
          <Row k="Backend URL" v={apiConfig.baseUrl} />
          <Row k="Last fetch" v={apiConfig.lastFetch ?? "—"} />
          <Row k="Reachable" v={reachable == null ? "—" : reachable ? "yes" : "no"} />
          <Row k="Contract" v="INTEGRATION.md" />
          <div className="sm:col-span-2 rounded-sm border border-line bg-surface/60 p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
                Voice alerts
              </p>
              <VoiceAudioToggle compact />
            </div>
            <label className="flex cursor-pointer items-start gap-3 border-b border-line py-2.5">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={voice.audioEnabled}
                onChange={(e) => updateVoice({ audioEnabled: e.target.checked })}
              />
              <span>
                <span className="block text-ink">Enable all voice alerts</span>
                <span className="text-[11px] text-ink-dim">
                  Master switch — turns off recommendation and operator confirmation audio.
                </span>
              </span>
            </label>
            <label
              className={`flex cursor-pointer items-start gap-3 py-1.5 ${subVoiceDisabled ? "opacity-50" : ""}`}
            >
              <input
                type="checkbox"
                className="mt-0.5"
                disabled={subVoiceDisabled}
                checked={voice.recommendationAlerts}
                onChange={(e) => updateVoice({ recommendationAlerts: e.target.checked })}
              />
              <span>
                <span className="block text-ink">Recommendation alerts</span>
                <span className="text-[11px] text-ink-dim">
                  Speak the agent recommendation when Gradium audio is ready.
                </span>
              </span>
            </label>
            <label
              className={`flex cursor-pointer items-start gap-3 py-1.5 ${subVoiceDisabled ? "opacity-50" : ""}`}
            >
              <input
                type="checkbox"
                className="mt-0.5"
                disabled={subVoiceDisabled}
                checked={voice.operatorActionConfirmations}
                onChange={(e) => updateVoice({ operatorActionConfirmations: e.target.checked })}
              />
              <span>
                <span className="block text-ink">Operator action confirmation</span>
                <span className="text-[11px] text-ink-dim">
                  After you approve or override, speak what action was taken.
                </span>
              </span>
            </label>
          </div>
          {liveDown && (
            <p className="sm:col-span-2 text-crit">
              Start the API with <code className="font-mono">python -m sentinel.server</code> from
              the repo root, then refresh.
            </p>
          )}
          <div className="sm:col-span-2">
            <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              Expected endpoints
            </p>
            <ul className="mt-1 space-y-0.5 break-all font-mono text-[11px] text-ink-dim">
              <li>GET /api/cluster/state</li>
              <li>GET /api/agent/recommendation</li>
              <li>POST /api/agent/recommendation/:id/approve</li>
              <li>POST /api/agent/recommendation/:id/override</li>
              <li>POST /api/agent/recommendation/dismiss-audio</li>
              <li>GET /api/agent/recommendation/:id/operator-audio</li>
              <li>POST /api/agent/recommendation/:id/why</li>
              <li>GET /api/telemetry/events</li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">{k}</p>
      <p className="mt-0.5 font-mono text-[11px] text-ink">{v}</p>
    </div>
  );
}
