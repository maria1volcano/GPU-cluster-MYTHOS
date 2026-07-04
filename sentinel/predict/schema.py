"""Prediction output schema — DESIGN §3.4.

Field-for-field compatible with the agent-loop branch's ``sentinel/models.py``
(``Prediction`` / ``Evidence``) so that ``Agent.recommend(prediction)`` consumes
M3 output with zero changes:

  - ``prediction.type`` / ``target["id"]`` / ``eta_seconds`` / ``prediction_id``
  - iterates ``prediction.evidence`` and narrates on the metric names
    ``rack_temp_c`` (``slope_per_min``), ``queued_heavy_jobs`` (``value``),
    ``rack_util`` (``value``).

Keep those names + the ``target={"kind","id"}`` shape stable — they are the
handshake with M4's recommender/template narration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Prediction stream frames ride the same WS /stream as telemetry, demuxed on
# "type" (CONTRACTS §1). Mirror the telemetry schema version.
FRAME_TYPE_PREDICTION = "prediction"
SCHEMA_VERSION = 1

# Prediction type tags (DESIGN §3.4).
THERMAL_THROTTLE = "THERMAL_THROTTLE"
SCHEDULING_BOTTLENECK = "SCHEDULING_BOTTLENECK"
NODE_INSTABILITY = "NODE_INSTABILITY"


@dataclass
class Evidence:
    """One numeric justification line behind a prediction (FR-11).

    ``to_dict`` drops ``None`` fields so a temp-trend evidence and a
    scalar-value evidence serialize cleanly from the same shape.
    """
    metric: str
    value: Optional[float] = None
    slope_per_min: Optional[float] = None
    threshold: Optional[float] = None
    current: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Prediction:
    """A typed, explainable prediction (DESIGN §3.4)."""
    prediction_id: str
    type: str                       # THERMAL_THROTTLE | SCHEDULING_BOTTLENECK | NODE_INSTABILITY
    target: Dict[str, str]          # {"kind": "rack"|"node", "id": "rack-00"}
    eta_seconds: float              # time-to-event; 0.0 == already occurring
    severity: str                   # "low" | "medium" | "high" | "critical"
    confidence: float               # 0..1
    evidence: List[Evidence]
    t: int                          # trace-seconds this prediction was made

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


def prediction_frame(t: int, tick: int, predictions: List[Prediction]) -> Dict[str, Any]:
    """Wrap a tick's predictions as a WS /stream frame (demux key ``type``).

    Interleaves with "telemetry" (M2) and "kpi" (M6) frames on the same socket
    (CONTRACTS §1). ``predictions`` may be empty — an empty frame still tells
    the dashboard "nothing firing this tick", which clears stale cards.
    """
    return {
        "type": FRAME_TYPE_PREDICTION,
        "v": SCHEMA_VERSION,
        "tick": tick,
        "t": t,
        "predictions": [p.to_dict() for p in predictions],
    }
