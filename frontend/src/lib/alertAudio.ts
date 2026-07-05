import { useEffect, useRef } from "react";

import { apiConfig } from "@/lib/api";
import { getVoiceSettings, isAudioEnabled } from "@/lib/voiceSettings";
import { useVoiceSettings } from "@/lib/useVoiceSettings";
import type { AgentRecommendation, OperatorActionResult } from "@/types/cluster";

const PLAYED_KEY_PREFIX = "sentinel-alert-played:";

/** Monotonic token — increment to invalidate in-flight playback. */
let cancelToken = 0;
let currentAudio: HTMLAudioElement | null = null;
let currentPlayKey: string | null = null;
/** Serializes play requests so only one alert runs at a time. */
let playChain: Promise<void> = Promise.resolve();
/** Recommendation waiting for a user gesture after autoplay was blocked. */
let pendingRetry: AgentRecommendation | null = null;
/** Prevent duplicate auto-play attempts for the same recommendation id. */
const inFlight = new Set<string>();
/** Session-only guard — avoids stale localStorage blocking fresh stress alerts. */
const playedThisSession = new Set<string>();

const playbackListeners = new Set<() => void>();

function notifyPlaybackListeners(): void {
  playbackListeners.forEach((cb) => cb());
}

export function subscribeVoiceAlertPlayback(cb: () => void): () => void {
  playbackListeners.add(cb);
  return () => playbackListeners.delete(cb);
}

export function getPendingVoiceAlert(): AgentRecommendation | null {
  return pendingRetry;
}

/** Same-origin in the browser so Vite proxies /api to the backend. */
export function resolveAlertAudioUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (typeof window !== "undefined") {
    return path.startsWith("/") ? path : `/${path}`;
  }
  const base = apiConfig.baseUrl.replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

export function primeAudioPlayback(): void {
  try {
    const audio = new Audio(
      "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBIAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAA==",
    );
    audio.volume = 0.01;
    void audio.play().catch(() => {
      /* gesture helps subsequent alerts when TTS finishes quickly */
    });
  } catch {
    /* ignore */
  }
}

export function shouldPlayRecommendationAlert(
  rec: AgentRecommendation | null,
): rec is AgentRecommendation {
  return Boolean(
    rec &&
      isAudioEnabled() &&
      rec.alertStatus === "ready" &&
      rec.alertAudioUrl &&
      getVoiceSettings().recommendationAlerts,
  );
}

function hasPlayed(recId: string): boolean {
  if (playedThisSession.has(recId)) return true;
  try {
    return localStorage.getItem(`${PLAYED_KEY_PREFIX}${recId}`) === "1";
  } catch {
    return false;
  }
}

function markPlayed(recId: string): void {
  playedThisSession.add(recId);
  try {
    localStorage.setItem(`${PLAYED_KEY_PREFIX}${recId}`, "1");
  } catch {
    /* ignore quota / private mode */
  }
  pendingRetry = null;
  notifyPlaybackListeners();
}

export function clearPlayedAlerts(): void {
  playedThisSession.clear();
  try {
    for (let i = localStorage.length - 1; i >= 0; i -= 1) {
      const k = localStorage.key(i);
      if (k?.startsWith(PLAYED_KEY_PREFIX)) localStorage.removeItem(k);
    }
  } catch {
    /* ignore */
  }
  pendingRetry = null;
  inFlight.clear();
  stopRecommendationAlert();
  notifyPlaybackListeners();
}

function detachCurrentAudio(): void {
  if (!currentAudio) return;
  try {
    currentAudio.pause();
    currentAudio.currentTime = 0;
    currentAudio.removeAttribute("src");
    currentAudio.load();
  } catch {
    /* ignore */
  }
  currentAudio = null;
  currentPlayKey = null;
}

/** Stop any in-progress voice alert (e.g. when the operator approves or overrides). */
export function stopRecommendationAlert(): void {
  cancelToken += 1;
  detachCurrentAudio();
  notifyPlaybackListeners();
}

function waitForPlaybackEnd(audio: HTMLAudioElement): Promise<void> {
  return new Promise((resolve, reject) => {
    const onEnded = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error("Alert audio playback failed"));
    };
    const cleanup = () => {
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onError);
    };
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("error", onError);
  });
}

function waitForAudioReady(audio: HTMLAudioElement): Promise<void> {
  if (audio.readyState >= HTMLMediaElement.HAVE_ENOUGH_DATA) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const onReady = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error("Alert audio failed to load"));
    };
    const cleanup = () => {
      audio.removeEventListener("canplaythrough", onReady);
      audio.removeEventListener("error", onError);
    };
    audio.addEventListener("canplaythrough", onReady, { once: true });
    audio.addEventListener("error", onError, { once: true });
    audio.load();
  });
}

function isAutoplayBlocked(err: unknown): boolean {
  if (!(err instanceof DOMException)) return false;
  return err.name === "NotAllowedError" || err.name === "AbortError";
}

export function playAudioUrl(url: string, playKey: string): Promise<void> {
  const token = cancelToken;

  const attempt = async () => {
    if (token !== cancelToken) return;
    if (currentPlayKey === playKey && currentAudio && !currentAudio.paused) return;

    detachCurrentAudio();
    currentPlayKey = playKey;

    const audio = new Audio(resolveAlertAudioUrl(url));
    audio.preload = "auto";
    currentAudio = audio;

    try {
      await waitForAudioReady(audio);
      if (token !== cancelToken) {
        detachCurrentAudio();
        return;
      }
      await audio.play();
      if (token !== cancelToken) {
        detachCurrentAudio();
        return;
      }
      await waitForPlaybackEnd(audio);
    } catch (err) {
      if (token === cancelToken) detachCurrentAudio();
      throw err;
    } finally {
      if (token === cancelToken && currentAudio === audio) {
        currentAudio = null;
        currentPlayKey = null;
      }
    }
  };

  const thisPlay = playChain.then(attempt, attempt);
  playChain = thisPlay.catch((err) => {
    if (token === cancelToken) {
      console.warn("Failed to play alert audio", err);
    }
  });
  return thisPlay;
}

export function playRecommendationAlert(rec: AgentRecommendation): Promise<void> {
  if (!shouldPlayRecommendationAlert(rec)) return Promise.resolve();
  return playAudioUrl(rec.alertAudioUrl!, `rec:${rec.id}`);
}

/** User-initiated play — always allowed when audio is ready (click restores gesture). */
export function playRecommendationAlertManual(rec: AgentRecommendation): Promise<void> {
  if (!isAudioEnabled() || !getVoiceSettings().recommendationAlerts) {
    return Promise.resolve();
  }
  if (rec.alertStatus !== "ready" || !rec.alertAudioUrl) {
    return Promise.resolve();
  }
  inFlight.delete(rec.id);
  pendingRetry = null;
  notifyPlaybackListeners();
  return playAudioUrl(rec.alertAudioUrl, `rec:${rec.id}`).then(() => markPlayed(rec.id));
}

function attemptRecommendationPlay(rec: AgentRecommendation): void {
  const recId = rec.id;
  if (hasPlayed(recId) || inFlight.has(recId)) return;

  inFlight.add(recId);
  const token = cancelToken;
  playRecommendationAlert(rec)
    .then(() => {
      if (token === cancelToken) markPlayed(recId);
    })
    .catch((err) => {
      if (token !== cancelToken) return;
      if (isAutoplayBlocked(err)) {
        pendingRetry = rec;
        notifyPlaybackListeners();
        return;
      }
      try {
        localStorage.removeItem(`${PLAYED_KEY_PREFIX}${recId}`);
        playedThisSession.delete(recId);
      } catch {
        /* ignore */
      }
    })
    .finally(() => {
      inFlight.delete(recId);
    });
}

export function retryPendingRecommendationAlert(): void {
  const rec = pendingRetry;
  if (!rec || !shouldPlayRecommendationAlert(rec) || hasPlayed(rec.id) || inFlight.has(rec.id)) {
    return;
  }
  pendingRetry = null;
  notifyPlaybackListeners();
  attemptRecommendationPlay(rec);
}

export function playOperatorActionAlert(impact: OperatorActionResult): Promise<void> {
  return playOperatorActionAlertManual(impact);
}

/** User-initiated operator confirmation — plays when TTS clip is ready. */
export function playOperatorActionAlertManual(impact: OperatorActionResult): Promise<void> {
  if (!isAudioEnabled() || !getVoiceSettings().operatorActionConfirmations) {
    return Promise.resolve();
  }
  if (impact.operatorAlertStatus !== "ready" || !impact.operatorAlertAudioUrl) {
    return Promise.resolve();
  }
  return playAudioUrl(
    impact.operatorAlertAudioUrl,
    `operator:${impact.action}:${impact.jobId ?? impact.fromRack ?? "action"}`,
  );
}

export async function dismissRecommendationAudio(recommendationId: string): Promise<void> {
  if (!recommendationId || apiConfig.mode === "mock") return;
  markPlayed(recommendationId);
  pendingRetry = null;
  try {
    const base =
      typeof window !== "undefined" ? "" : apiConfig.baseUrl.replace(/\/$/, "");
    await fetch(`${base}/api/agent/recommendation/dismiss-audio`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ recommendationId }),
      keepalive: true,
    });
  } catch {
    /* ignore — tab may be closing */
  }
}

/** Auto-play when Gradium audio becomes ready; manual play via VoiceAlertBrief button. */
export function useRecommendationAlert(
  rec: AgentRecommendation | null,
  options?: { enabled?: boolean },
) {
  const enabled = options?.enabled ?? true;
  const [voice] = useVoiceSettings();
  const recIdRef = useRef<string | undefined>();

  useEffect(() => {
    recIdRef.current = rec?.id;
  }, [rec?.id]);

  useEffect(() => {
    if (!enabled) stopRecommendationAlert();
  }, [enabled]);

  useEffect(() => {
    const onHide = () => {
      stopRecommendationAlert();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") stopRecommendationAlert();
    };
    const onGesture = () => retryPendingRecommendationAlert();

    window.addEventListener("pagehide", onHide);
    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("pointerdown", onGesture, { capture: true });
    return () => {
      window.removeEventListener("pagehide", onHide);
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("pointerdown", onGesture, { capture: true });
    };
  }, []);

  useEffect(() => {
    if (!enabled || !voice.audioEnabled || !voice.recommendationAlerts) return;
    if (!rec || rec.alertStatus === "dismissed" || rec.alertStatus === "cancelled") return;
    if (!shouldPlayRecommendationAlert(rec)) return;
    attemptRecommendationPlay(rec);
  }, [
    enabled,
    voice.audioEnabled,
    voice.recommendationAlerts,
    rec?.id,
    rec?.alertStatus,
    rec?.alertAudioUrl,
  ]);
}
