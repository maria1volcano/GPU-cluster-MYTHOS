"""Agent and decision-log models (M4/M6).

Prediction/Evidence live in ``sentinel.predict.schema`` — the M3 handshake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sentinel.predict.schema import Evidence


@dataclass
class Recommendation:
    recommendation_id: str
    prediction_id: str
    action: str
    job_id: Optional[str]
    from_rack: Optional[str]
    to_rack: Optional[str]
    expected_effect: str
    justification: str
    source: str
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "prediction_id": self.prediction_id,
            "action": self.action,
            "job_id": self.job_id,
            "from_rack": self.from_rack,
            "to_rack": self.to_rack,
            "expected_effect": self.expected_effect,
            "justification": self.justification,
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    def as_card(self) -> str:
        lines = [self.justification]
        if self.action == "MIGRATE_JOB":
            lines.append(
                f"Suggested action: migrate job {self.job_id} from {self.from_rack} to {self.to_rack}."
            )
        lines.append(f"Expected effect: {self.expected_effect}")
        return "\n".join(lines)


@dataclass
class DecisionLogEntry:
    decision_id: str
    t: int
    prediction: Dict[str, Any]
    recommendation: Dict[str, Any]
    operator_action: str
    operator_alternative: Optional[Dict[str, Any]]
    outcome: str
    lead_time_seconds: float
    latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "t": self.t,
            "prediction": self.prediction,
            "recommendation": self.recommendation,
            "operator_action": self.operator_action,
            "operator_alternative": self.operator_alternative,
            "outcome": self.outcome,
            "lead_time_seconds": self.lead_time_seconds,
            "latency_ms": self.latency_ms,
        }
