export type VoiceSettings = {
  /** Master switch — when false, no voice alerts play. */
  audioEnabled: boolean;
  /** Speak the agent recommendation when Gradium audio is ready. */
  recommendationAlerts: boolean;
  /** After approve/override, speak what the operator chose. */
  operatorActionConfirmations: boolean;
};

const STORAGE_KEY = "sentinel-voice-settings";

const DEFAULTS: VoiceSettings = {
  audioEnabled: true,
  recommendationAlerts: true,
  operatorActionConfirmations: true,
};

export function getVoiceSettings(): VoiceSettings {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<VoiceSettings>;
    return {
      audioEnabled: parsed.audioEnabled ?? DEFAULTS.audioEnabled,
      recommendationAlerts: parsed.recommendationAlerts ?? DEFAULTS.recommendationAlerts,
      operatorActionConfirmations:
        parsed.operatorActionConfirmations ?? DEFAULTS.operatorActionConfirmations,
    };
  } catch {
    return DEFAULTS;
  }
}

export function isAudioEnabled(): boolean {
  return getVoiceSettings().audioEnabled;
}

export function setVoiceSettings(next: VoiceSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota / private mode */
  }
}
