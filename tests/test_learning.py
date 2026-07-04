from sentinel import config
from sentinel.config import ClassThresholds, Thresholds
from sentinel.decision_log import DecisionLog
from sentinel.learning import OverrideLearner
from sentinel.predict.schema import Evidence, Prediction


def _thermal_prediction():
    return Prediction(
        prediction_id="pred-1",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": "rack-00"},
        eta_seconds=200.0,
        severity="high",
        confidence=0.8,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=2.0, threshold=84.0, current=70.0)],
        t=0,
    )


def _log_decision(log, action, outcome, t):
    log.record(
        t=t,
        prediction=_thermal_prediction(),
        recommendation=None,
        operator_action=action,
        operator_alternative=None,
        outcome=outcome,
        lead_time_seconds=200.0,
        latency_ms=1000.0,
    )


def test_repeated_overrides_tighten_the_lead_time_threshold(tmp_path):
    log = DecisionLog(path=tmp_path / "log.jsonl")
    thresholds = Thresholds()
    baseline = thresholds.thermal_throttle.lead_time_seconds

    for i in range(4):
        _log_decision(log, "OVERRIDE", "UNKNOWN", t=i * 100)

    learner = OverrideLearner(log, thresholds)
    adjustments = learner.apply()

    assert adjustments
    assert thresholds.thermal_throttle.lead_time_seconds < baseline
    assert thresholds.thermal_throttle.lead_time_seconds >= config.MIN_THERMAL_LEAD_TIME_SECONDS
    assert adjustments[0]["direction"] == "tightened"


def test_lead_time_never_drops_below_the_configured_floor(tmp_path):
    log = DecisionLog(path=tmp_path / "log.jsonl")
    thresholds = Thresholds(thermal_throttle=ClassThresholds(lead_time_seconds=config.MIN_THERMAL_LEAD_TIME_SECONDS))

    for i in range(4):
        _log_decision(log, "OVERRIDE", "UNKNOWN", t=i * 100)

    learner = OverrideLearner(log, thresholds)
    learner.apply()
    assert thresholds.thermal_throttle.lead_time_seconds == config.MIN_THERMAL_LEAD_TIME_SECONDS


def test_approved_averted_decisions_relax_a_tightened_threshold_back_up(tmp_path):
    log = DecisionLog(path=tmp_path / "log.jsonl")
    thresholds = Thresholds()

    for i in range(4):
        _log_decision(log, "OVERRIDE", "UNKNOWN", t=i * 100)
    learner = OverrideLearner(log, thresholds)
    learner.apply()
    tightened = thresholds.thermal_throttle.lead_time_seconds
    assert tightened < config.DEFAULT_THERMAL_LEAD_TIME_SECONDS

    for i in range(3):
        _log_decision(log, "APPROVE", "AVERTED", t=1000 + i * 100)
    learner.apply()
    relaxed = thresholds.thermal_throttle.lead_time_seconds
    assert relaxed > tightened


def test_no_adjustment_below_minimum_sample_size(tmp_path):
    log = DecisionLog(path=tmp_path / "log.jsonl")
    thresholds = Thresholds()
    baseline = thresholds.thermal_throttle.lead_time_seconds

    _log_decision(log, "OVERRIDE", "UNKNOWN", t=0)
    learner = OverrideLearner(log, thresholds)
    adjustments = learner.apply()

    assert adjustments == []
    assert thresholds.thermal_throttle.lead_time_seconds == baseline


def test_thresholds_persist_across_save_and_load(tmp_path):
    state_path = tmp_path / "state.json"
    thresholds = Thresholds()
    thresholds.thermal_throttle.lead_time_seconds = 321.0
    thresholds.save(state_path)

    reloaded = Thresholds.load(state_path)
    assert reloaded.thermal_throttle.lead_time_seconds == 321.0
