"""Derived rack topology (DESIGN §2.1). The trace has no rack column.

Approved rule: "model_homogeneous" — group nodes by GPU model (models ordered
by descending fleet GPU count, then name), sort by sn within each model, and
bucket into racks of RACK_SIZE. Every rack is single-model, so thermal
profiles are clean: G2/G3 racks are the natural hotspots, T4 racks the cool
migration targets (DESIGN §7).

Rack ids are global and stable: rack-00, rack-01, ... G2 racks come first
(rack-00..rack-17), then T4, G3, P100, V100M32, V100M16, A10 => 42 racks.
Neighbors are adjacent racks within the same model group (used for thermal
coupling context and migration-target scoring).

Deterministic: pure function of the node list + config. Run
`python3 -m sentinel.data.racks` for the rack table + coverage gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sentinel.config import RACK_RULE, RACK_SIZE
from sentinel.data.models import Node


@dataclass(frozen=True)
class Rack:
    rack_id: str
    gpu_model: Optional[str]      # None only under the "contiguous" rule (mixed)
    node_sns: tuple
    capacity_gpus: int
    neighbors: tuple              # adjacent rack_ids in the same model group


@dataclass(frozen=True)
class Topology:
    nodes: tuple                  # all Node objects, CSV order
    racks: tuple                  # all Rack objects, rack index order
    rack_of: dict                 # node sn -> rack_id
    node_by_sn: dict              # sn -> Node
    rack_by_id: dict              # rack_id -> Rack

    def gpu_ids(self) -> list[str]:
        return [g for n in self.nodes for g in n.gpu_ids()]


def derive_racks(nodes: list[Node], rule: str = RACK_RULE, rack_size: int = RACK_SIZE) -> Topology:
    if rule == "model_homogeneous":
        fleet_gpus: dict = {}
        for n in nodes:
            fleet_gpus[n.model] = fleet_gpus.get(n.model, 0) + n.gpu
        models = sorted(fleet_gpus, key=lambda m: (-fleet_gpus[m], m))
        groups = [
            (model, sorted((n for n in nodes if n.model == model), key=lambda n: n.sn))
            for model in models
        ]
    elif rule == "contiguous":
        groups = [(None, sorted(nodes, key=lambda n: n.sn))]
    else:
        raise ValueError(f"unknown rack rule: {rule}")

    racks: list[Rack] = []
    rack_of: dict = {}
    idx = 0
    for model, group_nodes in groups:
        group_start = idx
        buckets = [group_nodes[i:i + rack_size] for i in range(0, len(group_nodes), rack_size)]
        for b, bucket in enumerate(buckets):
            rack_id = f"rack-{idx:02d}"
            neighbors = []
            if b > 0:
                neighbors.append(f"rack-{idx - 1:02d}")
            if b < len(buckets) - 1:
                neighbors.append(f"rack-{idx + 1:02d}")
            racks.append(Rack(
                rack_id=rack_id,
                gpu_model=model,
                node_sns=tuple(n.sn for n in bucket),
                capacity_gpus=sum(n.gpu for n in bucket),
                neighbors=tuple(neighbors),
            ))
            for n in bucket:
                rack_of[n.sn] = rack_id
            idx += 1
        assert idx > group_start  # every model yields at least one rack

    return Topology(
        nodes=tuple(nodes),
        racks=tuple(racks),
        rack_of=rack_of,
        node_by_sn={n.sn: n for n in nodes},
        rack_by_id={r.rack_id: r for r in racks},
    )


def main() -> int:
    from sentinel.data.loader import load_nodes

    nodes = load_nodes()
    topo = derive_racks(nodes)
    print(f"{len(topo.racks)} racks derived (rule={RACK_RULE}, size={RACK_SIZE}):\n")
    print(f"{'rack':8} {'model':8} {'nodes':>5} {'gpus':>5}  neighbors")
    for r in topo.racks:
        print(f"{r.rack_id:8} {str(r.gpu_model):8} {len(r.node_sns):>5} {r.capacity_gpus:>5}  {', '.join(r.neighbors)}")

    assert len(topo.rack_of) == len(nodes), "every node must be assigned a rack"
    assert sum(r.capacity_gpus for r in topo.racks) == sum(n.gpu for n in nodes)
    assert all(
        len({topo.node_by_sn[sn].model for sn in r.node_sns}) == 1
        for r in topo.racks if r.gpu_model is not None
    ), "racks must be model-homogeneous"
    assert topo.racks == derive_racks(load_nodes()).racks, "derivation must be deterministic"
    print("\nRACK GATE PASSED — full coverage, homogeneous, deterministic.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
