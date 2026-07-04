"""CSV loaders for the openb trace (M0). Stdlib `csv` only — no pandas
dependency required, per DESIGN.md §6 tech-stack notes."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

from sentinel.models import Node, Pod

# The trace marks "still running at trace end" pods with this sentinel
# deletion time (DESIGN.md §2.2) — 34 such pods are treated as censored.
CENSORED_DELETION_TIME = 12_902_960


def load_nodes(path: Path) -> List[Node]:
    nodes: List[Node] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            nodes.append(
                Node(
                    sn=row["sn"],
                    cpu_milli=int(row["cpu_milli"]),
                    memory_mib=int(row["memory_mib"]),
                    gpu=int(row["gpu"]),
                    model=row["model"],
                )
            )
    return nodes


def _parse_optional_int(value: str) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value else None


def load_pods(path: Path) -> List[Pod]:
    pods: List[Pod] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            pods.append(
                Pod(
                    name=row["name"],
                    cpu_milli=int(row["cpu_milli"]),
                    memory_mib=int(row["memory_mib"]),
                    num_gpu=int(row["num_gpu"]),
                    gpu_milli=int(row["gpu_milli"]),
                    gpu_spec=row["gpu_spec"],
                    qos=row["qos"],
                    pod_phase=row["pod_phase"],
                    creation_time=int(row["creation_time"]),
                    deletion_time=int(row["deletion_time"]),
                    scheduled_time=_parse_optional_int(row["scheduled_time"]),
                )
            )
    return pods


def stats(nodes: List[Node], pods: List[Pod]) -> dict:
    """Reproduces the headline numbers in PRD.md §2 — used to sanity-check
    that the loaders match the documented grounding data."""
    total_gpus = sum(n.gpu for n in nodes)
    model_counts: dict = {}
    for n in nodes:
        m = model_counts.setdefault(n.model, {"nodes": 0, "gpus": 0})
        m["nodes"] += 1
        m["gpus"] += n.gpu

    gpu_pods = [p for p in pods if p.num_gpu > 0]
    fractional = [p for p in gpu_pods if 0 < p.gpu_milli < 1000]
    whole = [p for p in gpu_pods if p.gpu_milli == 1000]
    pending = [p for p in pods if p.is_pending]

    return {
        "node_count": len(nodes),
        "total_gpus": total_gpus,
        "model_counts": model_counts,
        "pod_count": len(pods),
        "gpu_pod_count": len(gpu_pods),
        "fractional_gpu_pods": len(fractional),
        "whole_gpu_pods": len(whole),
        "pending_pods": len(pending),
        "time_span_seconds": max((p.deletion_time for p in pods), default=0),
    }
