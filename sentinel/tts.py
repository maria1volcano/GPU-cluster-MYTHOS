"""Gradium text-to-speech for operator alerts (from test.py pattern).

Speaks plain-language recommendation cards when GRADIUM_API_KEY is set.
Without a key or gradium package, calls are no-ops so demos still run offline.
"""
from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path
from typing import Optional

from sentinel import config
from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction

logger = logging.getLogger(__name__)


def build_alert_text(prediction: Prediction, recommendation: Optional[Recommendation]) -> str:
    """Short spoken alert for the operator floor."""
    rack = prediction.target.get("id", "the hot rack")
    parts = [f"Alert. {rack}"]

    for e in prediction.evidence:
        if e.slope_per_min is not None and "temp" in e.metric:
            parts.append(f"is heating at nearly {abs(e.slope_per_min):.0f} degrees per minute")
            break

    if prediction.type == "THERMAL_THROTTLE":
        if prediction.eta_seconds <= 0:
            parts.append("and is already throttling")
        else:
            parts.append(f"and will likely throttle in about {prediction.eta_seconds / 60:.0f} minutes")

    if recommendation is None:
        parts.append("No safe migration is available right now.")
        return ". ".join(parts) + "."

    parts.append(
        f"I recommend migrating {recommendation.job_id} to {recommendation.to_rack}. "
        f"Approve or override."
    )
    return ". ".join(parts) + "."


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
        result = await client.tts(
            {
                "voice_id": self.voice_id,
                "output_format": "pcm",
                "json_config": {"speed": self.speed},
            },
            text,
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
        self, prediction: Prediction, recommendation: Optional[Recommendation], output_wav: Optional[Path] = None
    ) -> Optional[Path]:
        return self.speak_sync(build_alert_text(prediction, recommendation), output_wav=output_wav)
