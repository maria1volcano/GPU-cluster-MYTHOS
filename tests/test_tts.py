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
    assert "too hot" in text
    assert "rack-18" in text
    assert "thermal throttling" in text
    assert "openb-pod-0001" in text


def test_build_alert_text_without_recommendation():
    prediction = Prediction(
        prediction_id="pred-2",
        type="SCHEDULING_BOTTLENECK",
        target={"kind": "rack", "id": "rack-03"},
        eta_seconds=0.0,
        severity="high",
        confidence=0.7,
        evidence=[Evidence(metric="queued_heavy_jobs", value=4.0)],
        t=200,
    )
    text = build_alert_text(prediction, None)
    assert "rack-03" in text
    assert "scheduling bottleneck" in text
    assert "4 heavy jobs" in text


def test_build_alert_text_normalizes_celsius_for_speech():
    prediction = Prediction(
        prediction_id="pred-3",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=0.0,
        severity="high",
        confidence=0.9,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=-0.3, threshold=84.0, current=84.3)],
        t=100,
    )
    recommendation = Recommendation(
        recommendation_id="rec-3",
        prediction_id="pred-3",
        action="MIGRATE_JOB",
        job_id="openb-pod-0007",
        from_rack="rack-00",
        to_rack="rack-03",
        expected_effect="cool down",
        justification=(
            "Rack-00 is already at 84.3°C, above the 84°C threshold with -0.3°C headroom "
            "and 30 GPUs throttling."
        ),
        source="template_fallback",
    )
    text = build_alert_text(prediction, recommendation)
    assert "°" not in text
    assert "84.3 degrees Celsius" in text
    assert "84 degrees Celsius threshold" in text
    assert "-0.3 degrees Celsius headroom" in text
    assert " dollar " not in text.lower()


def test_alert_speaker_skips_without_api_key():
    speaker = AlertSpeaker(api_key="")
    assert speaker.speak_sync("hello") is None
