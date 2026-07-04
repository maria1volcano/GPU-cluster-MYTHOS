"""Append-only decision log — M6 (DESIGN.md §3.6)."""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import List, Optional

from sentinel import config
from sentinel.models import DecisionLogEntry, Recommendation
from sentinel.predict.schema import Prediction

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
