"""Learning from overrides (FR-9, M7) — DESIGN.md §2.7.

Aggregates `DecisionLog` outcomes per prediction type and nudges that
type's thresholds:

- Operators **repeatedly override/dismiss** an alert class → the class is
  firing too early / too often → **tighten** it (raise the bar to fire),
  bounded so it can never fire less than the PRD's minimum useful lead
  time (NFR-2: >= 5 min).
- Operators **approve** and the projected incident is **averted** → the
  current setting is working → slowly **relax** back toward the
  documented default (so a temporary spell of overrides doesn't
  permanently silence a whole alert class).

Every adjustment is appended to `Thresholds.adjustment_log` and persisted
to `config.STATE_PATH`, so the tuning is itself auditable (NFR-7) — this
is deliberately a simple, explainable rule rather than a black-box
optimizer, consistent with the project's "explainable before clever"
principle (PRD NG4).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from sentinel import config
from sentinel.config import Thresholds
from sentinel.decision_log import DecisionLog

OVERRIDE_ACTIONS = ("OVERRIDE", "DISMISS")


class OverrideLearner:
    def __init__(self, log: DecisionLog, thresholds: Thresholds):
        self.log = log
        self.thresholds = thresholds

    def apply(self) -> List[Dict[str, Any]]:
        """Re-reads the full decision log and adjusts thresholds in place.

        Returns the list of adjustments made this call (empty if nothing
        crossed the minimum-sample bar yet)."""
        entries = self.log.read_all()
        by_type: Dict[str, List[dict]] = defaultdict(list)
        for e in entries:
            pred_type = (e.get("prediction") or {}).get("type")
            if pred_type:
                by_type[pred_type].append(e)

        adjustments: List[Dict[str, Any]] = []
        for pred_type, group in by_type.items():
            if len(group) < config.LEARNING_MIN_SAMPLES:
                continue
            recent = group[-config.LEARNING_WINDOW :]
            adjustment = self._adjust_for_type(pred_type, recent)
            if adjustment:
                adjustments.append(adjustment)

        if adjustments:
            self.thresholds.adjustment_log.extend(adjustments)
            self.thresholds.save()
        return adjustments

    @staticmethod
    def _override_rate(group: List[dict]) -> float:
        overrides = sum(1 for e in group if e.get("operator_action") in OVERRIDE_ACTIONS)
        return overrides / len(group)

    @staticmethod
    def _approved_averted_rate(group: List[dict]) -> float:
        hits = sum(1 for e in group if e.get("operator_action") == "APPROVE" and e.get("outcome") == "AVERTED")
        return hits / len(group)

    def _adjust_for_type(self, pred_type: str, group: List[dict]) -> Dict[str, Any] | None:
        override_rate = self._override_rate(group)
        averted_rate = self._approved_averted_rate(group)

        if pred_type == "THERMAL_THROTTLE":
            return self._adjust_thermal(group, override_rate, averted_rate)
        if pred_type == "SCHEDULING_BOTTLENECK":
            return self._adjust_bottleneck(group, override_rate, averted_rate)
        return None

    def _adjust_thermal(self, group: List[dict], override_rate: float, averted_rate: float):
        th = self.thresholds.thermal_throttle
        old = th.lead_time_seconds

        if override_rate >= config.LEARNING_OVERRIDE_RATE_HIGH:
            new = max(config.MIN_THERMAL_LEAD_TIME_SECONDS, old * (1 - config.LEARNING_TIGHTEN_STEP))
            direction = "tightened"
        elif averted_rate >= 0.5 and old < config.DEFAULT_THERMAL_LEAD_TIME_SECONDS:
            new = min(config.DEFAULT_THERMAL_LEAD_TIME_SECONDS, old * (1 + config.LEARNING_RELAX_STEP))
            direction = "relaxed"
        else:
            return None

        new = round(new, 1)
        if abs(new - old) < 0.5:
            return None
        th.lead_time_seconds = new
        return {
            "type": "THERMAL_THROTTLE",
            "field": "lead_time_seconds",
            "old": old,
            "new": new,
            "override_rate": round(override_rate, 2),
            "direction": direction,
            "samples": len(group),
        }

    def _adjust_bottleneck(self, group: List[dict], override_rate: float, averted_rate: float):
        th = self.thresholds.scheduling_bottleneck
        old_util, old_heavy = th.util_threshold, th.queued_heavy_threshold

        if override_rate >= config.LEARNING_OVERRIDE_RATE_HIGH:
            new_util = min(config.MAX_BOTTLENECK_UTIL_THRESHOLD, round(old_util + 0.02, 3))
            new_heavy = min(config.MAX_BOTTLENECK_QUEUED_HEAVY, old_heavy + 1)
            direction = "tightened"
        elif averted_rate >= 0.5 and (old_util > config.DEFAULT_BOTTLENECK_UTIL_THRESHOLD or old_heavy > config.DEFAULT_BOTTLENECK_QUEUED_HEAVY):
            new_util = max(config.DEFAULT_BOTTLENECK_UTIL_THRESHOLD, round(old_util - 0.01, 3))
            new_heavy = max(config.DEFAULT_BOTTLENECK_QUEUED_HEAVY, old_heavy - 1)
            direction = "relaxed"
        else:
            return None

        if new_util == old_util and new_heavy == old_heavy:
            return None
        th.util_threshold, th.queued_heavy_threshold = new_util, new_heavy
        return {
            "type": "SCHEDULING_BOTTLENECK",
            "field": "util_threshold,queued_heavy_threshold",
            "old": [old_util, old_heavy],
            "new": [new_util, new_heavy],
            "override_rate": round(override_rate, 2),
            "direction": direction,
            "samples": len(group),
        }
