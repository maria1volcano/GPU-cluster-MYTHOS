"""Gradium text-to-speech for operator alerts (from test.py pattern).

Speaks plain-language recommendation cards when GRADIUM_API_KEY is set.
Without a key or gradium package, calls are no-ops so demos still run offline.
"""
from __future__ import annotations

import asyncio
import logging
import re
import wave
from pathlib import Path
from typing import Optional

from sentinel import config
from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction, THERMAL_THROTTLE

logger = logging.getLogger(__name__)

_ISSUE_LABELS = {
    THERMAL_THROTTLE: "thermal throttling",
    "SCHEDULING_BOTTLENECK": "a scheduling bottleneck",
    "NODE_INSTABILITY": "node instability",
}


def _normalize_units_for_speech(text: str) -> str:
    """Replace symbols/abbreviations TTS misreads (e.g. °C → 'degrees Celsius')."""
    out = text
    out = re.sub(r"°\s*C\s*/\s*min", " degrees Celsius per minute", out, flags=re.IGNORECASE)
    out = re.sub(r"(-?\d+(?:\.\d+)?)\s*°\s*C", r"\1 degrees Celsius", out, flags=re.IGNORECASE)
    out = re.sub(r"°\s*C", " degrees Celsius", out, flags=re.IGNORECASE)
    # Bare "84 C" / "84C" temperature shorthand (not rack ids like rack-00).
    out = re.sub(
        r"(?<![A-Za-z0-9-])(-?\d+(?:\.\d+)?)\s*C(?=\s|[,.;:!?]|$)",
        r"\1 degrees Celsius",
        out,
    )
    out = out.replace("°", " degrees ")
    out = re.sub(r"\bdeg(?:rees)?\s+C\b", "degrees Celsius", out, flags=re.IGNORECASE)
    out = re.sub(r"\btemp_c\b", "temperature", out, flags=re.IGNORECASE)
    return out


def _sanitize_for_speech(text: str) -> str:
    cleaned = _normalize_units_for_speech(text.replace("\n", " "))
    cleaned = re.sub(r"\s+", " ", cleaned.strip())
    return cleaned


def build_alert_text(prediction: Prediction, recommendation: Optional[Recommendation]) -> str:
    """Spoken alert tied to the live prediction + agent recommendation (not a static script)."""
    rack = prediction.target.get("id", "the affected rack")
    issue = _ISSUE_LABELS.get(prediction.type, "an infrastructure risk")
    parts = [f"Sentinel alert for {rack}.", f"Detected {issue}."]

    for evidence in prediction.evidence[:3]:
        parts.append(_evidence_line(evidence))

    if prediction.eta_seconds <= 0:
        parts.append("Impact is happening now.")
    else:
        mins = max(1, round(prediction.eta_seconds / 60))
        parts.append(f"Estimated time to impact: about {mins} minutes.")

    if recommendation is None:
        parts.append("No safe migration is available right now. Monitor the rack closely.")
        return _sanitize_for_speech(" ".join(parts))

    if recommendation.justification.strip():
        parts.append(recommendation.justification.strip())
    if recommendation.job_id and recommendation.to_rack:
        parts.append(
            f"Recommended action: migrate job {recommendation.job_id} "
            f"from {recommendation.from_rack or rack} to {recommendation.to_rack}."
        )
    if recommendation.expected_effect.strip():
        parts.append(f"Expected effect: {recommendation.expected_effect.strip()}")
    parts.append("Approve or override in the dashboard.")
    return _sanitize_for_speech(" ".join(parts))


def _evidence_line(evidence: Evidence) -> str:
    if evidence.slope_per_min is not None and "temp" in evidence.metric:
        return f"Temperature trend {evidence.slope_per_min:+.1f} degrees per minute."
    if evidence.metric == "queued_heavy_jobs" and evidence.value is not None:
        return f"{int(evidence.value)} heavy jobs are queued on this rack."
    if evidence.current is not None and "temp" in evidence.metric:
        return f"Current rack temperature {evidence.current:.0f} degrees Celsius."
    if evidence.value is not None and evidence.metric == "rack_util":
        return f"Rack utilization is {evidence.value * 100:.0f} percent."
    if evidence.value is not None and "temp" in evidence.metric:
        return f"Projected temperature is {evidence.value:.0f} degrees Celsius."
    if evidence.value is not None:
        return f"{evidence.metric.replace('_', ' ')} is {evidence.value}."
    return ""


class AlertSpeaker:
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
        output_wav: Optional[Path] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.GRADIUM_API_KEY
        self.voice_id = voice_id or config.GRADIUM_VOICE_ID
        self.speed = speed if speed is not None else config.GRADIUM_TTS_SPEED
        self.output_wav = Path(output_wav or config.GRADIUM_OUTPUT_WAV)

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def speak(self, text: str, output_wav: Optional[Path] = None) -> Optional[Path]:
        if not self.is_configured:
            logger.info("Gradium TTS skipped (GRADIUM_API_KEY not set)")
            return None
        try:
            import gradium
        except ImportError:
            logger.warning("gradium package not installed — pip install gradium")
            return None

        out = Path(output_wav or self.output_wav)
        client = gradium.client.GradiumClient(api_key=self.api_key)
        spoken = _sanitize_for_speech(text)
        result = await client.tts(
            {
                "voice_id": self.voice_id,
                "output_format": "pcm",
                "json_config": {"speed": self.speed},
            },
            spoken,
        )
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(result.sample_rate)
            wav.writeframes(result.raw_data)

        duration = len(result.raw_data) / (result.sample_rate * 2)
        logger.info("Gradium alert saved to %s (%.1fs at %dHz)", out, duration, result.sample_rate)
        return out

    def speak_sync(self, text: str, output_wav: Optional[Path] = None) -> Optional[Path]:
        return asyncio.run(self.speak(text, output_wav=output_wav))

    def speak_recommendation(
        self,
        prediction: Prediction,
        recommendation: Optional[Recommendation],
        output_wav: Optional[Path] = None,
        *,
        alert_text: Optional[str] = None,
    ) -> Optional[Path]:
        text = alert_text or build_alert_text(prediction, recommendation)
        return self.speak_sync(text, output_wav=output_wav)
