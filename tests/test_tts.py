from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction
from sentinel.tts import AlertSpeaker, build_alert_text


def test_build_alert_text_includes_rack_and_migration():
    prediction = Prediction(
        prediction_id="pred-1",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=180.0,
        severity="high",
        confidence=0.8,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=12.0, threshold=84.0, current=80.0)],
        t=100,
    )
    recommendation = Recommendation(
        recommendation_id="rec-1",
        prediction_id="pred-1",
        action="MIGRATE_JOB",
        job_id="openb-pod-0001",
        from_rack="rack-00",
        to_rack="rack-18",
        expected_effect="cool down",
        justification="too hot",
        source="template_fallback",
    )
    text = build_alert_text(prediction, recommendation)
    assert "rack-00" in text
    assert "openb-pod-0001" in text
    assert "rack-18" in text


def test_alert_speaker_skips_without_api_key():
    speaker = AlertSpeaker(api_key="")
    assert speaker.speak_sync("hello") is None
