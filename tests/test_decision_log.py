from sentinel.decision_log import DecisionLog
from sentinel.models import Recommendation
from sentinel.predict.schema import Evidence, Prediction


def _prediction(pred_id="pred-1"):
    return Prediction(
        prediction_id=pred_id,
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=200.0,
        severity="high",
        confidence=0.8,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=2.0, threshold=84.0, current=70.0)],
        t=100,
    )


def _recommendation():
    return Recommendation(
        recommendation_id="rec-1",
        prediction_id="pred-1",
        action="MIGRATE_JOB",
        job_id="job-1",
        from_rack="rack-00",
        to_rack="rack-01",
        expected_effect="temp drops",
        justification="because reasons",
        source="template_fallback",
    )


def test_append_only_log_is_append_only_and_readable(tmp_path):
    log = DecisionLog(path=tmp_path / "decision_log.jsonl")
    log.record(
        t=100,
        prediction=_prediction(),
        recommendation=_recommendation(),
        operator_action="APPROVE",
        operator_alternative=None,
        outcome="AVERTED",
        lead_time_seconds=200.0,
        latency_ms=900.0,
    )
    log.record(
        t=200,
        prediction=_prediction("pred-2"),
        recommendation=None,
        operator_action="DISMISS",
        operator_alternative=None,
        outcome="UNKNOWN",
        lead_time_seconds=0.0,
        latency_ms=500.0,
    )

    entries = log.read_all()
    assert len(entries) == 2
    assert entries[0]["operator_action"] == "APPROVE"
    assert entries[0]["outcome"] == "AVERTED"
    assert entries[1]["recommendation"] == {}

    lines = (tmp_path / "decision_log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_read_all_on_missing_file_returns_empty_list(tmp_path):
    log = DecisionLog(path=tmp_path / "does_not_exist.jsonl")
    assert log.read_all() == []
