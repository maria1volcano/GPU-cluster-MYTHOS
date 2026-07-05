import { Volume2, VolumeX } from "lucide-react";

import { useVoiceSettings } from "@/lib/useVoiceSettings";

export function VoiceAudioToggle({
  compact = false,
  className = "",
}: {
  compact?: boolean;
  className?: string;
}) {
  const [voice, updateVoice] = useVoiceSettings();

  return (
    <button
      type="button"
      onClick={() => updateVoice({ audioEnabled: !voice.audioEnabled })}
      aria-pressed={voice.audioEnabled}
      aria-label={voice.audioEnabled ? "Turn voice alerts off" : "Turn voice alerts on"}
      title={voice.audioEnabled ? "Voice alerts on — click to mute" : "Voice alerts off — click to unmute"}
      className={`inline-flex items-center gap-1.5 rounded-sm border font-mono text-[10px] uppercase tracking-widest transition ${
        voice.audioEnabled
          ? "border-heat/35 bg-heat/10 text-heat hover:bg-heat/15"
          : "border-line bg-surface-2 text-ink-dim hover:border-line hover:text-ink"
      } ${compact ? "px-2 py-1" : "px-2.5 py-1.5"} ${className}`}
    >
      {voice.audioEnabled ? (
        <Volume2 className="h-3.5 w-3.5" />
      ) : (
        <VolumeX className="h-3.5 w-3.5" />
      )}
      {!compact && (voice.audioEnabled ? "Voice on" : "Voice off")}
    </button>
  );
}
