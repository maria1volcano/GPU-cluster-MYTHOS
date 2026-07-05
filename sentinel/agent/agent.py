"""Agent layer — M4 via Crusoe Inference (DESIGN.md §2.5)."""
from __future__ import annotations

import itertools
import logging
from typing import Any, Dict, List, Optional, Tuple

from sentinel.agent.crusoe_client import CrusoeClient
from sentinel.agent.recommender import Candidate, Recommender
from sentinel.models import Recommendation
from sentinel.predict.schema import Prediction

logger = logging.getLogger(__name__)

_rec_id_counter = itertools.count(1)


class Agent:
    def __init__(self, recommender: Recommender, crusoe_client: Optional[CrusoeClient] = None) -> None:
        self.recommender = recommender
        self.crusoe_client = crusoe_client or CrusoeClient()

    def recommend(self, prediction: Prediction) -> Optional[Recommendation]:
        candidates = self.recommender.candidates(prediction)
        if not candidates:
            logger.info("No safe migration candidate for %s on %s", prediction.type, prediction.target)
            return None

        llm_choice = self.crusoe_client.choose_candidate(prediction, candidates)
        chosen, justification, source = self._validate_or_fallback(llm_choice, candidates, prediction)
        return self._build_recommendation(prediction, chosen, justification, source)

    def _validate_or_fallback(
        self, llm_choice: Optional[Dict[str, Any]], candidates: List[Candidate], prediction: Prediction
    ) -> Tuple[Candidate, str, str]:
        if llm_choice is not None:
            idx = llm_choice.get("candidate_index")
            justification = llm_choice.get("justification")
            if (
                isinstance(idx, int)
                and 0 <= idx < len(candidates)
                and isinstance(justification, str)
                and justification.strip()
            ):
                return candidates[idx], justification.strip(), "crusoe"
            logger.warning("Rejecting invalid LLM choice %r — using template fallback", llm_choice)
        return self._template_fallback(prediction, candidates)

    @staticmethod
    def _template_fallback(prediction: Prediction, candidates: List[Candidate]) -> Tuple[Candidate, str, str]:
        best = candidates[0]
        return best, Agent._template_justification(prediction, best), "template_fallback"

    @staticmethod
    def _template_justification(prediction: Prediction, candidate: Candidate) -> str:
        by_metric = {e.metric: e for e in prediction.evidence}

        def val(metric: str):
            e = by_metric.get(metric)
            return e.value if e is not None else None

        bits = []
        temp = by_metric.get("rack_temp_c")
        if temp is not None and temp.slope_per_min is not None:
            # Only narrate a slope that is actually moving; a saturated rack is
            # "pinned at the line", never "heating at 0.0°C/min".
            if abs(temp.slope_per_min) >= 0.35:
                bits.append(f"heating ~{temp.slope_per_min:+.1f}°C/min")
            elif (temp.current is not None and temp.threshold
                  and temp.current >= temp.threshold - 1.0):
                bits.append(f"{temp.current:.0f}°C, pinned at the "
                            f"{temp.threshold:.0f}°C throttle line")
        queued_heavy = val("queued_heavy_jobs")
        if queued_heavy:
            bits.append(f"{int(queued_heavy)} more heavy jobs queued")
        for metric, fmt in (
            ("queued_heavy_gpu_minutes", "{:.0f} GPU-minutes of heavy work inbound"),
            ("xid_errors", "{:.0f} XID driver fault(s) this tick"),
            ("ecc_errors_volatile", "{:.0f} volatile ECC error(s)"),
            ("hw_thermal_gpus", "{:.0f} GPU(s) in hardware thermal limit"),
            ("clock_derated_gpus", "{:.0f} GPU(s) with derated clocks"),
        ):
            v = val(metric)
            if v:
                bits.append(fmt.format(float(v)))

        throttling = val("throttling_gpus") or 0
        if prediction.type == "THERMAL_THROTTLE":
            if prediction.eta_seconds <= 0 and throttling:
                status = f"is throttling now — {int(throttling)} GPUs capped"
            elif prediction.eta_seconds <= 0:
                status = "is at its throttle line"
            else:
                status = f"will likely throttle in ~{max(1, round(prediction.eta_seconds / 60))} min"
        elif prediction.type == "SCHEDULING_BOTTLENECK":
            status = "is becoming a scheduling bottleneck"
        elif prediction.type == "NODE_INSTABILITY":
            node = prediction.target.get("id", "node")
            status = f"node {node} is showing instability signals"
        else:
            status = "needs attention"

        # Forward-looking clause from the digital twin's do-nothing projection —
        # this, not a fabricated ETA, is the "unless we act" number.
        projected = ""
        peak = val("projected_peak_throttling_gpus")
        horizon = val("projected_horizon_min")
        if peak and horizon:
            if peak > throttling:
                projected = (f"Projected to worsen to {int(peak)} throttling GPUs "
                             f"over the next ~{horizon:.0f} min unless we act. ")
            else:
                projected = (f"Projected to stay throttled ({int(peak)} GPUs) "
                             f"for the next ~{horizon:.0f} min unless we act. ")

        target_label = prediction.target.get("rack_id") or prediction.target.get("id", "rack")
        trend = f" ({', '.join(bits)})" if bits else ""
        return (
            f"{target_label} {status}{trend}. {projected}"
            f"{candidate.to_rack} has {candidate.to_rack_free_capacity_frac * 100:.0f}% free capacity and "
            f"{candidate.to_rack_thermal_headroom_c:.0f}°C of thermal headroom, so migrating "
            f"{candidate.job_id} there should resolve it."
        )

    @staticmethod
    def _build_recommendation(
        prediction: Prediction, candidate: Candidate, justification: str, source: str
    ) -> Recommendation:
        return Recommendation(
            recommendation_id=f"rec-{next(_rec_id_counter):04d}",
            prediction_id=prediction.prediction_id,
            action="MIGRATE_JOB",
            job_id=candidate.job_id,
            from_rack=candidate.from_rack,
            to_rack=candidate.to_rack,
            expected_effect=candidate.expected_effect,
            justification=justification,
            source=source,
            evidence=prediction.evidence,
        )
