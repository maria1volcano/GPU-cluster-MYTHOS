"""CSV loaders for the openb trace (stdlib csv, no pandas).

Null handling (verified against the real files):
  - scheduled_time is the only column that can be empty (897 Pending pods) -> None.
  - deletion_time == TRACE_END_S (34 pods) -> censored=True (still running at end).
  - creation_time / deletion_time are never empty; keys are never duplicated.
"""
from __future__ import annotations

import csv
from pathlib import Path

from sentinel.config import NODE_CSV, POD_CSV, TRACE_END_S
from sentinel.data.models import Node, Pod

NODE_COLUMNS = ["sn", "cpu_milli", "memory_mib", "gpu", "model"]
POD_COLUMNS = [
    "name", "cpu_milli", "memory_mib", "num_gpu", "gpu_milli", "gpu_spec",
    "qos", "pod_phase", "creation_time", "deletion_time", "scheduled_time",
]


def load_nodes(path: Path = NODE_CSV) -> list[Node]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != NODE_COLUMNS:
            raise ValueError(f"unexpected node CSV columns: {reader.fieldnames}")
        return [
            Node(
                sn=row["sn"],
                cpu_milli=int(row["cpu_milli"]),
                memory_mib=int(row["memory_mib"]),
                gpu=int(row["gpu"]),
                model=row["model"],
            )
            for row in reader
        ]


def load_pods(path: Path = POD_CSV) -> list[Pod]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != POD_COLUMNS:
            raise ValueError(f"unexpected pod CSV columns: {reader.fieldnames}")
        pods = []
        for row in reader:
            deletion = int(row["deletion_time"])
            pods.append(Pod(
                name=row["name"],
                cpu_milli=int(row["cpu_milli"]),
                memory_mib=int(row["memory_mib"]),
                num_gpu=int(row["num_gpu"]),
                gpu_milli=int(row["gpu_milli"]),
                gpu_spec=row["gpu_spec"],
                qos=row["qos"],
                pod_phase=row["pod_phase"],
                creation_time=int(row["creation_time"]),
                deletion_time=deletion,
                scheduled_time=int(row["scheduled_time"]) if row["scheduled_time"] != "" else None,
                censored=deletion == TRACE_END_S,
            ))
        return pods
