import { useEffect, useRef } from "react";

import { apiConfig } from "@/lib/api";
import type { AgentRecommendation } from "@/types/cluster";

export function resolveAlertAudioUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const base = apiConfig.baseUrl.replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function shouldPlayRecommendationAlert(
  rec: AgentRecommendation | null,
): rec is AgentRecommendation {
  return Boolean(rec && rec.alertStatus === "ready" && rec.alertAudioUrl);
}

export function playRecommendationAlert(rec: AgentRecommendation): Promise<void> {
  if (!shouldPlayRecommendationAlert(rec)) return Promise.resolve();
  const audio = new Audio(resolveAlertAudioUrl(rec.alertAudioUrl!));
  audio.preload = "auto";
  return audio.play().then(() => undefined);
}

/** Play each recommendation's voice alert once when Gradium audio becomes ready. */
export function useRecommendationAlert(rec: AgentRecommendation | null) {
  const playedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!shouldPlayRecommendationAlert(rec)) return;
    if (playedRef.current.has(rec.id)) return;

    playedRef.current.add(rec.id);
    playRecommendationAlert(rec).catch((err) => {
      console.warn("Failed to play recommendation alert", err);
      playedRef.current.delete(rec.id);
    });
  }, [rec]);
}
