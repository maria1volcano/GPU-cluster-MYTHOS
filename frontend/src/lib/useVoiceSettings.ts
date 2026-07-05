import { useCallback, useEffect, useState } from "react";

import { stopRecommendationAlert } from "@/lib/alertAudio";
import {
  getVoiceSettings,
  setVoiceSettings,
  type VoiceSettings,
} from "@/lib/voiceSettings";

const CHANGE_EVENT = "sentinel-voice-settings-changed";

export function useVoiceSettings(): [VoiceSettings, (patch: Partial<VoiceSettings>) => void] {
  const [voice, setVoice] = useState<VoiceSettings>(() => getVoiceSettings());

  useEffect(() => {
    const sync = () => setVoice(getVoiceSettings());
    window.addEventListener(CHANGE_EVENT, sync);
    return () => window.removeEventListener(CHANGE_EVENT, sync);
  }, []);

  const update = useCallback((patch: Partial<VoiceSettings>) => {
    const next = { ...getVoiceSettings(), ...patch };
    setVoiceSettings(next);
    setVoice(next);
    if (patch.audioEnabled === false) stopRecommendationAlert();
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return [voice, update];
}
