import { useEffect } from "react";

import { apiConfig } from "@/lib/api";
import type { AgentRecommendation } from "@/types/cluster";

const PLAYED_KEY_PREFIX = "sentinel-alert-played:";

export function resolveAlertAudioUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const base = apiConfig.baseUrl.replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function shouldPlayRecommendationAlert(
  rec: AgentRecommendation | null,
): rec is AgentRecommendation {
  return Boolean(rec && rec.alertStatus === "ready" && rec.alertAudioUrl && rec.alertText);
}

function playbackKey(rec: AgentRecommendation): string {
  return `${rec.id}:${rec.alertText ?? ""}:${rec.alertAudioUrl ?? ""}`;
}

function hasPlayed(key: string): boolean {
  try {
    return sessionStorage.getItem(`${PLAYED_KEY_PREFIX}${key}`) === "1";
  } catch {
    return false;
  }
}

function markPlayed(key: string): void {
  try {
    sessionStorage.setItem(`${PLAYED_KEY_PREFIX}${key}`, "1");
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearPlayedAlerts(): void {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const k = sessionStorage.key(i);
      if (k?.startsWith(PLAYED_KEY_PREFIX)) sessionStorage.removeItem(k);
    }
  } catch {
    /* ignore */
  }
}

export function playRecommendationAlert(rec: AgentRecommendation): Promise<void> {
  if (!shouldPlayRecommendationAlert(rec)) return Promise.resolve();
  const audio = new Audio(resolveAlertAudioUrl(rec.alertAudioUrl!));
  audio.preload = "auto";
  return audio.play().then(() => undefined);
}

/** Play each recommendation's voice alert once when Gradium audio becomes ready. */
export function useRecommendationAlert(rec: AgentRecommendation | null) {
  useEffect(() => {
    if (!shouldPlayRecommendationAlert(rec)) return;

    const key = playbackKey(rec);
    if (hasPlayed(key)) return;

    markPlayed(key);
    playRecommendationAlert(rec).catch((err) => {
      console.warn("Failed to play recommendation alert", err);
      try {
        sessionStorage.removeItem(`${PLAYED_KEY_PREFIX}${key}`);
      } catch {
        /* ignore */
      }
    });
  }, [rec?.id, rec?.alertStatus, rec?.alertAudioUrl, rec?.alertText]);
}
