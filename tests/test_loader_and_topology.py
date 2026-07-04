from sentinel import config
from sentinel.data.loader import load_nodes, load_pods, stats
from sentinel.topology import derive_racks


def test_loader_matches_prd_grounding_numbers():
    nodes = load_nodes(config.NODE_CSV)
    pods = load_pods(config.POD_CSV)
    s = stats(nodes, pods)

    # PRD.md §2.1 / §2.2
    assert s["node_count"] == 1213
    assert s["total_gpus"] == 6212
    assert s["pod_count"] == 8152
    assert s["fractional_gpu_pods"] == 3078
    assert s["whole_gpu_pods"] == 3986


def test_derive_racks_homogeneous_assigns_every_node():
    nodes = load_nodes(config.NODE_CSV)
    racks = derive_racks(nodes, rack_size=32, homogeneous=True)

    total_nodes_in_racks = sum(len(r.node_ids) for r in racks.values())
    assert total_nodes_in_racks == len(nodes)
    assert all(n.rack_id is not None for n in nodes)

    for rack in racks.values():
        models_in_rack = {n.model for n in nodes if n.rack_id == rack.rack_id}
        assert len(models_in_rack) == 1, "homogeneous racks must contain a single GPU model"

    for rack in racks.values():
        assert len(rack.node_ids) <= 32


def test_derive_racks_neighbors_are_within_bounds():
    nodes = load_nodes(config.NODE_CSV)
    racks = derive_racks(nodes)
    rack_ids = set(racks.keys())
    for rack in racks.values():
        for neighbor in rack.neighbors:
            assert neighbor in rack_ids
