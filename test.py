"""Gradium TTS smoke test — set GRADIUM_API_KEY in .env first."""
from __future__ import annotations

import asyncio
import sys

from sentinel.tts import AlertSpeaker, build_alert_text
from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction


async def main() -> int:
    prediction = Prediction(
        prediction_id="pred-test",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=180.0,
        severity="high",
        confidence=0.85,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=12.8, threshold=84.0, current=82.0)],
        t=0,
    )
    recommendation = Recommendation(
        recommendation_id="rec-test",
        prediction_id="pred-test",
        action="MIGRATE_JOB",
        job_id="openb-pod-7671",
        from_rack="rack-00",
        to_rack="rack-18",
        expected_effect="temp drops below throttle line",
        justification="rack-00 is heating quickly",
        source="template_fallback",
    )
    text = build_alert_text(prediction, recommendation)
    print(f"Speaking: {text}")
    out = await AlertSpeaker().speak(text)
    if out is None:
        print("TTS skipped — install gradium and set GRADIUM_API_KEY", file=sys.stderr)
        return 1
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
