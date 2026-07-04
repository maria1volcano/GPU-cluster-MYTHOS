"""Core data models — see DESIGN.md §3 for the canonical schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Node:
    sn: str
    cpu_milli: int
    memory_mib: int
    gpu: int
    model: str
    rack_id: Optional[str] = None


@dataclass
class Pod:
    name: str
    cpu_milli: int
    memory_mib: int
    num_gpu: int
    gpu_milli: int
    gpu_spec: str
    qos: str
    pod_phase: str
    creation_time: int
    deletion_time: int
    scheduled_time: Optional[int]

    @property
    def is_pending(self) -> bool:
        return self.scheduled_time is None

    @property
    def is_heavy(self) -> bool:
        """A pod is "heavy" if it takes >=2 whole GPUs or a large fractional
        share of one (DESIGN §2.4 / PRD §5 "3 heavy jobs queued")."""
        return self.num_gpu >= 2 or self.gpu_milli >= 700

    @property
    def requested_gpu_milli(self) -> int:
        return self.num_gpu * self.gpu_milli


@dataclass
class Rack:
    rack_id: str
    node_ids: List[str] = field(default_factory=list)
    gpu_model: Optional[str] = None
    capacity_gpus: int = 0
    neighbors: List[str] = field(default_factory=list)


@dataclass
class GpuTelemetrySample:
    gpu_id: str
    node_sn: str
    rack_id: str
    model: str
    t: int
    util: float
    temp_c: float
    power_w: float
    sm_clock_mhz: int
    mem_clock_mhz: int
    mem_used_mib: int
    throttle_reasons: List[str] = field(default_factory=list)
    xid_errors: int = 0
    ecc_errors: Dict[str, int] = field(default_factory=lambda: {"volatile": 0, "aggregate": 0})


@dataclass
class Evidence:
    metric: str
    value: Optional[float] = None
    slope_per_min: Optional[float] = None
    threshold: Optional[float] = None
    current: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Prediction:
    prediction_id: str
    type: str  # THERMAL_THROTTLE | SCHEDULING_BOTTLENECK | NODE_INSTABILITY
    target: Dict[str, str]  # {"kind": "rack", "id": "rack-07"}
    eta_seconds: float
    severity: str
    confidence: float
    evidence: List[Evidence]
    t: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prediction_id": self.prediction_id,
            "type": self.type,
            "target": self.target,
            "eta_seconds": self.eta_seconds,
            "severity": self.severity,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "t": self.t,
        }


@dataclass
class Recommendation:
    recommendation_id: str
    prediction_id: str
    action: str  # MIGRATE_JOB | NO_ACTION
    job_id: Optional[str]
    from_rack: Optional[str]
    to_rack: Optional[str]
    expected_effect: str
    justification: str
    source: str  # "crusoe" | "template_fallback"
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
        """The plain-language, one-tap card text shown to the operator (FR-5)."""
        lines = [self.justification]
        if self.action == "MIGRATE_JOB":
            lines.append(f"Suggested action: migrate job {self.job_id} from {self.from_rack} to {self.to_rack}.")
        lines.append(f"Expected effect: {self.expected_effect}")
        return "\n".join(lines)


@dataclass
class DecisionLogEntry:
    decision_id: str
    t: int
    prediction: Dict[str, Any]
    recommendation: Dict[str, Any]
    operator_action: str  # APPROVE | OVERRIDE | DISMISS
    operator_alternative: Optional[Dict[str, Any]]
    outcome: str  # AVERTED | INCIDENT | UNKNOWN
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
