"""Append-only decision log — DESIGN.md §2.7 / §3.6, PRD FR-8 / NFR-7.

Every surfaced prediction+recommendation, the operator's action, and the
resolved outcome is appended as one JSON line. This is the substrate that
`sentinel.learning.OverrideLearner` reads to implement FR-9 (learning from
overrides): it is intentionally simple (JSONL, append-only, never
rewritten) so it stays auditable.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Iterator, List, Optional

from sentinel import config
from sentinel.models import DecisionLogEntry, Prediction, Recommendation

_decision_id_counter = itertools.count(1)


def next_decision_id() -> str:
    return f"dec-{next(_decision_id_counter):04d}"


class DecisionLog:
    def __init__(self, path: Path = config.DECISION_LOG_PATH):
        self.path = Path(path)

    def append(self, entry: DecisionLogEntry) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    def record(
        self,
        t: int,
        prediction: Prediction,
        recommendation: Optional[Recommendation],
        operator_action: str,
        operator_alternative: Optional[dict],
        outcome: str,
        lead_time_seconds: float,
        latency_ms: float,
    ) -> DecisionLogEntry:
        entry = DecisionLogEntry(
            decision_id=next_decision_id(),
            t=t,
            prediction=prediction.to_dict(),
            recommendation=recommendation.to_dict() if recommendation else {},
            operator_action=operator_action,
            operator_alternative=operator_alternative,
            outcome=outcome,
            lead_time_seconds=lead_time_seconds,
            latency_ms=latency_ms,
        )
        self.append(entry)
        return entry

    def read_all(self) -> List[dict]:
        if not self.path.exists():
            return []
        entries = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
