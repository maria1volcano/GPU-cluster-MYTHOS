from sentinel.config import Thresholds
from sentinel.models import GpuTelemetrySample, Rack
from sentinel.predict.engine import PredictionEngine


def _sample(t, temp, util, rack_id="rack-000", model="G2"):
    return GpuTelemetrySample(
        gpu_id=f"node-0/gpu-0",
        node_sn="node-0",
        rack_id=rack_id,
        model=model,
        t=t,
        util=util,
        temp_c=temp,
        power_w=200.0,
        sm_clock_mhz=1410,
        mem_clock_mhz=900,
        mem_used_mib=1000,
    )


def _engine():
    racks = {"rack-000": Rack(rack_id="rack-000", node_ids=["node-0"], gpu_model="G2", capacity_gpus=8)}
    thresholds = Thresholds()
    return PredictionEngine(racks, thresholds, window_size=6)


def test_thermal_throttle_fires_with_evidence_when_temp_climbs_toward_threshold():
    engine = _engine()
    predictions = []
    # Climb from 50C toward the 83C throttle line at ~3C/tick (30s ticks).
    for i in range(6):
        t = i * 30
        temp = 50.0 + i * 3.0
        predictions.extend(engine.ingest_tick(t, [_sample(t, temp, util=0.95)], pending_heavy_count=0))

    throttle_preds = [p for p in predictions if p.type == "THERMAL_THROTTLE"]
    assert throttle_preds, "expected at least one THERMAL_THROTTLE prediction as temp climbs"

    pred = throttle_preds[-1]
    assert pred.target == {"kind": "rack", "id": "rack-000"}
    assert pred.eta_seconds < Thresholds().thermal_throttle.lead_time_seconds
    assert pred.confidence > 0
    # FR-11: evidence must carry the numeric trend that justified the alert.
    metrics = {e.metric for e in pred.evidence}
    assert "rack_temp_c" in metrics
    temp_evidence = next(e for e in pred.evidence if e.metric == "rack_temp_c")
    assert temp_evidence.slope_per_min is not None and temp_evidence.slope_per_min > 0
    assert temp_evidence.threshold == 83.0


def test_thermal_throttle_does_not_fire_when_flat_and_cool():
    engine = _engine()
    predictions = []
    for i in range(6):
        t = i * 30
        predictions.extend(engine.ingest_tick(t, [_sample(t, 40.0, util=0.1)], pending_heavy_count=0))
    assert not [p for p in predictions if p.type == "THERMAL_THROTTLE"]


def test_scheduling_bottleneck_fires_on_high_util_and_queued_heavy_jobs():
    engine = _engine()
    predictions = engine.ingest_tick(0, [_sample(0, 60.0, util=0.95)], pending_heavy_count=3)
    bottleneck = [p for p in predictions if p.type == "SCHEDULING_BOTTLENECK"]
    assert bottleneck
    pred = bottleneck[0]
    values = {e.metric: e.value for e in pred.evidence}
    assert values["queued_heavy_jobs"] == 3
    assert values["rack_util"] == 0.95


def test_scheduling_bottleneck_does_not_fire_below_thresholds():
    engine = _engine()
    predictions = engine.ingest_tick(0, [_sample(0, 60.0, util=0.5)], pending_heavy_count=3)
    assert not [p for p in predictions if p.type == "SCHEDULING_BOTTLENECK"]

    predictions = engine.ingest_tick(30, [_sample(30, 60.0, util=0.95)], pending_heavy_count=1)
    assert not [p for p in predictions if p.type == "SCHEDULING_BOTTLENECK"]
