from sentinel.agent.agent import Agent
from sentinel.agent.recommender import Recommender
from sentinel.models import Evidence, Node, Pod, Prediction
from sentinel.replay import ClusterSimulator
from sentinel.topology import derive_racks


def _build_scenario():
    nodes = [
        Node(sn="n0", cpu_milli=8000, memory_mib=16384, gpu=2, model="G2"),
        Node(sn="n1", cpu_milli=8000, memory_mib=16384, gpu=2, model="G2"),
        Node(sn="n2", cpu_milli=8000, memory_mib=16384, gpu=2, model="T4"),
    ]
    racks = derive_racks(nodes, rack_size=2, homogeneous=True)
    hot_rack = next(r.rack_id for r in racks.values() if r.gpu_model == "G2")
    cool_rack = next(r.rack_id for r in racks.values() if r.gpu_model == "T4")

    simulator = ClusterSimulator(nodes, racks, pods=[])
    heavy_pod = Pod(
        name="job-heavy",
        cpu_milli=4000,
        memory_mib=8192,
        num_gpu=2,
        gpu_milli=1000,
        gpu_spec="",
        qos="LS",
        pod_phase="Running",
        creation_time=0,
        deletion_time=100_000,
        scheduled_time=0,
    )
    assert simulator.force_place_on_rack(heavy_pod, hot_rack)
    pods_by_name = {heavy_pod.name: heavy_pod}

    prediction = Prediction(
        prediction_id="pred-test-1",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": hot_rack},
        eta_seconds=180.0,
        severity="high",
        confidence=0.8,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=3.0, threshold=83.0, current=70.0)],
        t=0,
    )
    return simulator, racks, pods_by_name, hot_rack, cool_rack, prediction


def test_recommender_only_returns_capacity_validated_candidates():
    simulator, racks, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(racks, simulator, pods_by_name, rack_temp_provider=lambda rid: 40.0)

    candidates = recommender.candidates(prediction)
    assert candidates, "expected at least one capacity-validated candidate"
    for c in candidates:
        assert c.to_rack != hot_rack
        free = simulator.rack_capacity_milli(c.to_rack) - simulator.rack_committed_milli(c.to_rack)
        assert free >= pods_by_name[c.job_id].requested_gpu_milli, "guardrail: candidate must have real spare capacity"


def test_recommender_returns_nothing_when_no_capacity_anywhere():
    simulator, racks, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    # Fill every other rack to capacity so nothing can safely absorb the job.
    for rack_id, rack in racks.items():
        if rack_id == hot_rack:
            continue
        for nid in rack.node_ids:
            simulator.committed_milli[nid] = simulator.node_capacity_milli[nid]

    recommender = Recommender(racks, simulator, pods_by_name, rack_temp_provider=lambda rid: 40.0)
    assert recommender.candidates(prediction) == []


class _StubCrusoeClient:
    def __init__(self, response):
        self._response = response
        self.is_configured = True

    def choose_candidate(self, prediction, candidates):
        return self._response


def test_agent_uses_crusoe_choice_when_valid():
    simulator, racks, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(racks, simulator, pods_by_name, rack_temp_provider=lambda rid: 40.0)
    stub = _StubCrusoeClient({"candidate_index": 0, "justification": "Rack is hot, move the job."})
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "crusoe"
    assert rec.justification == "Rack is hot, move the job."
    assert rec.to_rack == cool_rack
    assert rec.evidence == prediction.evidence


def test_agent_falls_back_to_template_on_invalid_llm_choice():
    simulator, racks, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(racks, simulator, pods_by_name, rack_temp_provider=lambda rid: 40.0)
    # Out-of-range index — the guardrail must reject this, never invent a candidate.
    stub = _StubCrusoeClient({"candidate_index": 99, "justification": "nonsense"})
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "template_fallback"
    assert "rack" in rec.justification.lower()


def test_agent_falls_back_to_template_when_llm_unavailable():
    simulator, racks, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(racks, simulator, pods_by_name, rack_temp_provider=lambda rid: 40.0)
    stub = _StubCrusoeClient(None)  # simulates timeout / no API key / network error
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "template_fallback"
    assert rec.job_id == "job-heavy"
    assert rec.to_rack == cool_rack
