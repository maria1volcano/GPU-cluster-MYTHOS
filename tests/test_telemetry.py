from sentinel.telemetry import SimTelemetrySource


def _make_source(util_value: float, rack_util_value: float = 0.0):
    gpu_models = {"node-0/gpu-0": "G2"}
    gpu_node = {"node-0/gpu-0": "node-0"}
    gpu_rack = {"node-0/gpu-0": "rack-000"}
    return SimTelemetrySource(
        gpu_models=gpu_models,
        gpu_node=gpu_node,
        gpu_rack=gpu_rack,
        util_provider=lambda gpu_id: util_value,
        rack_util_provider=lambda rack_id: rack_util_value,
        seed=1,
    )


def test_low_util_stays_cool_and_unthrottled():
    source = _make_source(util_value=0.1)
    sample = None
    for t in range(0, 600, 30):
        sample = source.sample("node-0/gpu-0", t)
    assert sample.temp_c < 60
    assert sample.throttle_reasons == []
    assert sample.sm_clock_mhz == 1410  # G2 base clock, unthrottled


def test_high_util_heats_up_and_eventually_throttles():
    source = _make_source(util_value=0.99, rack_util_value=0.99)
    sample = None
    for t in range(0, 3000, 30):
        sample = source.sample("node-0/gpu-0", t)
    assert sample.temp_c > 83.0  # G2 throttle_temp
    assert sample.throttle_reasons
    assert sample.sm_clock_mhz < 1410


def test_temperature_is_deterministic_given_seed():
    source_a = _make_source(util_value=0.8, rack_util_value=0.5)
    source_b = _make_source(util_value=0.8, rack_util_value=0.5)
    samples_a = [source_a.sample("node-0/gpu-0", t) for t in range(0, 300, 30)]
    samples_b = [source_b.sample("node-0/gpu-0", t) for t in range(0, 300, 30)]
    assert [s.temp_c for s in samples_a] == [s.temp_c for s in samples_b]
