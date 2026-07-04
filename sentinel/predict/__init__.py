"""Sentinel prediction layer (M3) — DESIGN §2.4, PRD FR-2/FR-3/FR-4/FR-11.

Consumes the FROZEN telemetry frame (CONTRACTS.md, produced by M0-M2) one tick
at a time and emits typed, explainable ``Prediction`` objects:

  - THERMAL_THROTTLE      (FR-2, P0) — a rack heading into / sitting in throttle
  - SCHEDULING_BOTTLENECK (FR-3, P0) — heavy jobs queued onto a hot/full rack
  - NODE_INSTABILITY      (FR-4, P1) — XID/ECC/clock-instability on a node

Explainable-first (PRD NG4): EWMA smoothing + robust linear extrapolation +
fixed thresholds, no black-box ML. Every prediction carries the numeric
``evidence`` that fired it (FR-11).

The public surface is ``PredictionEngine`` (see ``engine.py``). It accepts the
telemetry frame in its **dict form** (``TelemetryFrame.to_dict()`` / the JSON in
``fixtures/`` / a ``WS /stream`` "telemetry" frame), so the identical code path
serves live replay, recorded fixtures, and the websocket.
"""
from __future__ import annotations

from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import Evidence, Prediction, prediction_frame

__all__ = ["PredictionEngine", "Prediction", "Evidence", "prediction_frame"]
