from sentinel.data.models import Node, Pod
from sentinel.data.racks import derive_racks
from sentinel.predict.schema import Evidence, Prediction
from sentinel.replay.placement import BinPackPlacement
from sentinel.replay.state import ClusterState


def _build_scenario():
    nodes = [
        Node(sn="openb-node-0000", cpu_milli=8000, memory_mib=16384, gpu=2, model="G2"),
        Node(sn="openb-node-0001", cpu_milli=8000, memory_mib=16384, gpu=2, model="G2"),
        Node(sn="openb-node-0002", cpu_milli=8000, memory_mib=16384, gpu=2, model="T4"),
    ]
    topology = derive_racks(nodes, rack_size=2)
    hot_rack = next(r.rack_id for r in topology.racks if r.gpu_model == "G2")
    cool_rack = next(r.rack_id for r in topology.racks if r.gpu_model == "T4")

    state = ClusterState(topology)
    placement = BinPackPlacement(topology, state)
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
        censored=False,
    )
    assignments = placement.choose_in_rack(heavy_pod, hot_rack)
    assert assignments is not None
    state.place(heavy_pod, assignments, t=0)
    pods_by_name = {heavy_pod.name: heavy_pod}

    prediction = Prediction(
        prediction_id="pred-test-1",
        type="THERMAL_THROTTLE",
        target={"kind": "rack", "id": hot_rack},
        eta_seconds=180.0,
        severity="high",
        confidence=0.8,
        evidence=[Evidence(metric="rack_temp_c", slope_per_min=3.0, threshold=84.0, current=70.0)],
        t=0,
    )
    return topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction


def test_recommender_only_returns_capacity_validated_candidates():
    from sentinel.agent.recommender import Recommender

    topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(
        topology, state, placement, pods_by_name, rack_temp_provider=lambda rid: 40.0
    )

    candidates = recommender.candidates(prediction)
    assert candidates, "expected at least one capacity-validated candidate"
    for c in candidates:
        assert c.to_rack != hot_rack
        rack = topology.rack_by_id[c.to_rack]
        free = rack.capacity_gpus * 1000 - state.rack_demand_milli[c.to_rack]
        assert free >= pods_by_name[c.job_id].num_gpu * pods_by_name[c.job_id].gpu_milli


def test_recommender_returns_nothing_when_no_capacity_anywhere():
    from sentinel.agent.recommender import Recommender

    topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    for rack in topology.racks:
        if rack.rack_id == hot_rack:
            continue
        state.rack_demand_milli[rack.rack_id] = rack.capacity_gpus * 1000

    recommender = Recommender(
        topology, state, placement, pods_by_name, rack_temp_provider=lambda rid: 40.0
    )
    assert recommender.candidates(prediction) == []


class _StubCrusoeClient:
    def __init__(self, response):
        self._response = response
        self.is_configured = True

    def choose_candidate(self, prediction, candidates):
        return self._response


def test_agent_uses_crusoe_choice_when_valid():
    from sentinel.agent.agent import Agent
    from sentinel.agent.recommender import Recommender

    topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(
        topology, state, placement, pods_by_name, rack_temp_provider=lambda rid: 40.0
    )
    stub = _StubCrusoeClient({"candidate_index": 0, "justification": "Rack is hot, move the job."})
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "crusoe"
    assert rec.justification == "Rack is hot, move the job."
    assert rec.to_rack == cool_rack
    assert rec.evidence == prediction.evidence


def test_agent_falls_back_to_template_on_invalid_llm_choice():
    from sentinel.agent.agent import Agent
    from sentinel.agent.recommender import Recommender

    topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(
        topology, state, placement, pods_by_name, rack_temp_provider=lambda rid: 40.0
    )
    stub = _StubCrusoeClient({"candidate_index": 99, "justification": "nonsense"})
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "template_fallback"
    assert "rack" in rec.justification.lower()


def test_agent_falls_back_to_template_when_llm_unavailable():
    from sentinel.agent.agent import Agent
    from sentinel.agent.recommender import Recommender

    topology, state, placement, pods_by_name, hot_rack, cool_rack, prediction = _build_scenario()
    recommender = Recommender(
        topology, state, placement, pods_by_name, rack_temp_provider=lambda rid: 40.0
    )
    stub = _StubCrusoeClient(None)
    agent = Agent(recommender=recommender, crusoe_client=stub)

    rec = agent.recommend(prediction)
    assert rec is not None
    assert rec.source == "template_fallback"
    assert rec.job_id == "job-heavy"
    assert rec.to_rack == cool_rack
