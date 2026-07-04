"""Derived rack / topology model — DESIGN.md §2.1.

The trace has no rack column, so racks are derived deterministically.
Default: group nodes so each rack is *homogeneous* by GPU model (cleaner
thermals, per DESIGN's recommended alternative), bucketed into groups of
`RACK_SIZE`. Falls back to plain contiguous-`sn` bucketing if
`HOMOGENEOUS_RACKS` is disabled.
"""
from __future__ import annotations

from typing import Dict, List

from sentinel import config
from sentinel.models import Node, Rack


def derive_racks(
    nodes: List[Node],
    rack_size: int = config.RACK_SIZE,
    homogeneous: bool = config.HOMOGENEOUS_RACKS,
) -> Dict[str, Rack]:
    """Assigns `node.rack_id` in place and returns {rack_id: Rack}."""
    racks: Dict[str, Rack] = {}

    if homogeneous:
        by_model: Dict[str, List[Node]] = {}
        for n in sorted(nodes, key=lambda n: n.sn):
            by_model.setdefault(n.model, []).append(n)
        rack_index = 0
        for model in sorted(by_model):
            group = by_model[model]
            for i in range(0, len(group), rack_size):
                chunk = group[i : i + rack_size]
                rack_id = f"rack-{rack_index:03d}"
                rack = Rack(rack_id=rack_id, gpu_model=model)
                for n in chunk:
                    n.rack_id = rack_id
                    rack.node_ids.append(n.sn)
                    rack.capacity_gpus += n.gpu
                racks[rack_id] = rack
                rack_index += 1
    else:
        ordered = sorted(nodes, key=lambda n: n.sn)
        for i in range(0, len(ordered), rack_size):
            chunk = ordered[i : i + rack_size]
            rack_id = f"rack-{i // rack_size:03d}"
            rack = Rack(rack_id=rack_id, gpu_model=None)
            for n in chunk:
                n.rack_id = rack_id
                rack.node_ids.append(n.sn)
                rack.capacity_gpus += n.gpu
            racks[rack_id] = rack

    # Neighbor relationships (adjacent rack ids) for thermal coupling / migration targets.
    rack_ids = list(racks.keys())
    for idx, rack_id in enumerate(rack_ids):
        neighbors = []
        if idx > 0:
            neighbors.append(rack_ids[idx - 1])
        if idx < len(rack_ids) - 1:
            neighbors.append(rack_ids[idx + 1])
        racks[rack_id].neighbors = neighbors

    return racks
