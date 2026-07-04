"""Crusoe Inference client — DESIGN.md §2.5 (M4)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from sentinel import config
from sentinel.agent.recommender import Candidate
from sentinel.predict.schema import Prediction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Sentinel, an assistant that explains GPU cluster operations alerts "
    "to a non-technical shift operator in plain language. You are given a "
    "prediction (with numeric trend evidence) and a list of pre-validated "
    "candidate migration actions. You MUST choose exactly one candidate by its "
    "'index' field — never invent a job id, rack, or a candidate not in the "
    "list. Respond with STRICT JSON ONLY, no prose before or after, in this "
    'exact shape: {"candidate_index": <int>, "justification": "<1-2 short '
    'plain-language sentences citing the trend numbers from the prediction>"}.'
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_user_prompt(prediction: Prediction, candidates: List[Candidate]) -> str:
    payload = {
        "prediction": prediction.to_dict(),
        "candidates": [
            {
                "index": i,
                "job_id": c.job_id,
                "from_rack": c.from_rack,
                "to_rack": c.to_rack,
                "to_rack_free_capacity_fraction": c.to_rack_free_capacity_frac,
                "to_rack_thermal_headroom_c": c.to_rack_thermal_headroom_c,
            }
            for i, c in enumerate(candidates)
        ],
    }
    return json.dumps(payload)


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(content)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


class CrusoeClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.CRUSOE_API_KEY
        self.base_url = base_url or config.CRUSOE_BASE_URL
        self.model = model or config.CRUSOE_MODEL
        self.timeout = timeout if timeout is not None else config.CRUSOE_TIMEOUT_SECONDS
        self._client: Optional[OpenAI] = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        return self._client

    def choose_candidate(
        self, prediction: Prediction, candidates: List[Candidate]
    ) -> Optional[Dict[str, Any]]:
        if not self.is_configured or not candidates:
            return None
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(prediction, candidates)},
                ],
                timeout=self.timeout,
            )
            content = response.choices[0].message.content or ""
            parsed = _extract_json(content)
            if not parsed or "candidate_index" not in parsed:
                logger.warning("Crusoe response missing candidate_index; falling back. raw=%r", content)
                return None
            return parsed
        except Exception:
            logger.exception("Crusoe Inference call failed; falling back to template recommendation")
            return None
