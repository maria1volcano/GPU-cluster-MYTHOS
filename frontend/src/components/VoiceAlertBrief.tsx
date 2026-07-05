import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Play, Volume2 } from "lucide-react";

import {
  playRecommendationAlertManual,
  subscribeVoiceAlertPlayback,
} from "@/lib/alertAudio";
import { buildAlertBriefSections, formatPredictedIssue } from "@/lib/formatAlertBrief";
import { useVoiceSettings } from "@/lib/useVoiceSettings";
import type { AgentRecommendation } from "@/types/cluster";
import { innerGlassPanel } from "@/lib/glassStyles";
import { riskColorHex } from "@/lib/riskStyles";

export function VoiceAlertBrief({ rec }: { rec: AgentRecommendation }) {
  const [voice] = useVoiceSettings();
  const [, tick] = useState(0);
  const [playing, setPlaying] = useState(false);

  useEffect(() => subscribeVoiceAlertPlayback(() => tick((n) => n + 1)), []);

  const canPlay =
    voice.audioEnabled &&
    voice.recommendationAlerts &&
    rec.alertStatus === "ready" &&
    Boolean(rec.alertAudioUrl);

  const sections = buildAlertBriefSections(rec);
  const issueColor = riskColorHex(rec.riskLevel);

  const handlePlay = () => {
    if (!canPlay || playing) return;
    setPlaying(true);
    void playRecommendationAlertManual(rec).finally(() => setPlaying(false));
  };

  return (
    <div className={`mt-5 rounded-sm border border-heat/25 bg-heat/[0.06] p-4 ${innerGlassPanel}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-heat">
            <Volume2 className="h-3.5 w-3.5" />
            Voice briefing
          </div>
          <p className="mt-2 flex items-center gap-2 text-base font-semibold text-ink">
            <AlertTriangle className="h-4 w-4 shrink-0" style={{ color: issueColor }} />
            {formatPredictedIssue(rec.predictedIssue)} · {rec.affectedRackId}
          </p>
        </div>

        {voice.audioEnabled && voice.recommendationAlerts && (
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            {rec.alertStatus === "generating" && (
              <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest text-ink-dim">
                <Loader2 className="h-3 w-3 animate-spin" />
                Preparing audio…
              </span>
            )}
            {canPlay && (
              <button
                type="button"
                onClick={handlePlay}
                disabled={playing}
                className="inline-flex items-center gap-2 rounded-sm border border-heat/40 bg-heat/15 px-3 py-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-heat transition hover:bg-heat/25 disabled:opacity-60"
              >
                <Play className="h-3.5 w-3.5" />
                {playing ? "Playing…" : "Play briefing"}
              </button>
            )}
            {rec.alertStatus === "failed" && (
              <span className="font-mono text-[10px] uppercase tracking-widest text-crit">
                Voice unavailable
              </span>
            )}
          </div>
        )}
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {sections.map((section) => (
          <div key={section.title} className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-widest text-ink-faint">
              {section.title}
            </p>
            <ul className="mt-2 space-y-1.5 text-sm leading-relaxed text-ink">
              {section.lines.map((line) => (
                <li key={line} className="flex gap-2">
                  <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-heat/70" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
