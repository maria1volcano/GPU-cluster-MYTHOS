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
        bits = []
        for e in prediction.evidence:
            if e.slope_per_min is not None and "temp" in e.metric:
                bits.append(f"{prediction.target['id']} is heating ~{e.slope_per_min:.1f}°C/min")
            elif e.metric == "queued_heavy_jobs" and e.value is not None:
                bits.append(f"{int(e.value)} heavy jobs queued")
            elif e.metric == "queued_heavy_gpu_minutes" and e.value is not None:
                bits.append(f"{e.value:.0f} GPU-minutes of heavy work queued")
            elif e.metric == "rack_util" and e.value is not None:
                bits.append(f"rack utilization at {e.value * 100:.0f}%")
            elif e.metric == "xid_errors" and e.value:
                bits.append(f"{int(e.value)} XID driver fault(s) this tick")
            elif e.metric == "ecc_errors_volatile" and e.value:
                bits.append(f"{int(e.value)} volatile ECC error(s)")
            elif e.metric == "hw_thermal_gpus" and e.value:
                bits.append(f"{int(e.value)} GPU(s) in hardware thermal limit")
            elif e.metric == "clock_derated_gpus" and e.value:
                bits.append(f"{int(e.value)} GPU(s) with derated clocks")

        if prediction.type == "THERMAL_THROTTLE":
            status = (
                "is already throttling"
                if prediction.eta_seconds <= 0
                else f"will likely throttle in ~{prediction.eta_seconds / 60:.0f} min"
            )
        elif prediction.type == "SCHEDULING_BOTTLENECK":
            status = "is becoming a scheduling bottleneck"
        elif prediction.type == "NODE_INSTABILITY":
            node = prediction.target.get("id", "node")
            status = f"node {node} is showing instability signals"
        else:
            status = "needs attention"

        target_label = prediction.target.get("rack_id") or prediction.target.get("id", "rack")
        trend = f" ({', '.join(bits)})" if bits else ""
        return (
            f"{target_label} {status}{trend}. "
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
