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
    """Spoken alert: present state, inbound pressure, and the twin's
    do-nothing projection — real numbers only, then the action. Never
    narrates a temperature slope (a saturated rack has none worth speaking)."""
    rack = prediction.target.get("rack_id") or prediction.target.get("id", "the rack")
    issue = _ISSUE_LABELS.get(prediction.type, "an infrastructure risk")
    by_metric = {e.metric: e for e in prediction.evidence}

    def val(metric: str):
        e = by_metric.get(metric)
        return e.value if e is not None else None

    throttling = val("throttling_gpus") or 0
    if prediction.type == THERMAL_THROTTLE and prediction.eta_seconds <= 0 and throttling:
        opening = f"{rack} is thermal throttling now — {int(throttling)} GPUs capped."
    elif prediction.type == THERMAL_THROTTLE and prediction.eta_seconds > 30:
        opening = (f"{rack} is projected to start thermal throttling in about "
                   f"{max(1, round(prediction.eta_seconds / 60))} minutes.")
    else:
        opening = f"{issue} on {rack}."

    clauses = []
    queued = val("queued_heavy_jobs")
    if queued:
        clauses.append(f"{int(queued)} more heavy jobs are queued for it.")
    peak = val("projected_peak_throttling_gpus")
    horizon = val("projected_horizon_min")
    if peak and horizon and peak > throttling:
        clauses.append(f"Projected to worsen to {int(peak)} GPUs "
                       f"within {horizon:.0f} minutes unless we act.")

    if recommendation is None:
        action = "Monitor the rack."
    else:
        job = recommendation.job_id or "the workload"
        dest = recommendation.to_rack or "a cooler rack"
        action = f"Recommended action: migrate {job} to {dest}."

    parts = ["Sentinel alert.", opening] + clauses + [action]
    return _sanitize_for_speech(" ".join(parts))


def build_operator_action_text(
    action: str,
    *,
    detail: str,
    job_id: Optional[str] = None,
    from_rack: Optional[str] = None,
    to_rack: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """Short spoken confirmation after approve or override (detail is UI-only)."""
    if action == "approved":
        if job_id and from_rack and to_rack:
            spoken = f"Approved. Migrating {job_id} to {to_rack}."
        elif job_id:
            spoken = f"Approved. Applying migration for {job_id}."
        else:
            spoken = "Approved. Migration applied."
    elif action == "overridden":
        if reason and reason.strip():
            brief = reason.strip()
            if len(brief) > 72:
                brief = f"{brief[:69]}..."
            spoken = f"Override recorded. {brief}"
        elif job_id and from_rack:
            spoken = f"Override recorded. {job_id} stays on {from_rack}."
        else:
            spoken = "Override recorded. No migration applied."
    else:
        spoken = "Operator action recorded."
    return _sanitize_for_speech(spoken)


def _evidence_line(evidence: Evidence, *, throttling: float = 0.0, headroom: Optional[float] = None) -> str:
    if evidence.slope_per_min is not None and evidence.metric == "rack_temp_c":
        temp = evidence.current
        slope = evidence.slope_per_min
        throttle = evidence.threshold or 84.0
        if temp is not None and abs(slope) < 0.35 and (
            temp >= throttle - 1.0 or (headroom is not None and headroom <= 1.0) or throttling >= 5
        ):
            return f"Rack temperature {temp:.0f} degrees Celsius, pinned at the throttle line."
        if slope >= 0.35:
            return f"Temperature trend plus {slope:.1f} degrees per minute."
        if slope <= -0.35:
            return f"Temperature trend {slope:.1f} degrees per minute."
        if temp is not None:
            return f"Rack temperature {temp:.0f} degrees Celsius, holding steady."
    if evidence.metric == "thermal_headroom_c" and evidence.value is not None:
        throttle = 84.0
        val = float(evidence.value)
        if val <= 0:
            return "Thermal headroom exhausted — above the throttle limit."
        return f"Only {val:.1f} degrees Celsius of thermal headroom remain."
    if evidence.metric == "queued_heavy_jobs" and evidence.value is not None:
        return f"{int(evidence.value)} heavy jobs are queued on this rack."
    if evidence.metric == "queued_heavy_gpu_minutes" and evidence.value is not None:
        return f"About {float(evidence.value):.0f} GPU-minutes of heavy work is inbound."
    if evidence.metric == "xid_errors" and evidence.value:
        return f"{int(evidence.value)} XID driver fault(s) detected on the node."
    if evidence.metric == "ecc_errors_volatile" and evidence.value:
        return f"{int(evidence.value)} volatile ECC error(s) on the node."
    if evidence.metric == "hw_thermal_gpus" and evidence.value:
        return f"{int(evidence.value)} GPU(s) hit the hardware thermal limit."
    if evidence.metric == "clock_derated_gpus" and evidence.value:
        return f"{int(evidence.value)} GPU(s) running with derated clocks."
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
