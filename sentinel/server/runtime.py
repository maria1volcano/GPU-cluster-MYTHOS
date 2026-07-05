"""Shared cluster runtime — engine replay loop, predictions, agent state."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from sentinel import config
from sentinel.agent.agent import Agent
from sentinel.agent.recommender import Recommender
from sentinel.config import BURN_IN_S, DEMO_WINDOW, SPEEDUP, TICK_TRACE_S, Thresholds
from sentinel.decision_log import DecisionLog
from sentinel.engine import Engine
from sentinel.learning import OverrideLearner
from sentinel.models import Recommendation
from sentinel.predict.engine import PredictionEngine
from sentinel.predict.schema import Prediction
from sentinel.server.dcgm_events import events_from_frame
from sentinel.server.mappers import (
    decision_entry_to_frontend,
    frame_to_cluster_state,
    merge_map_racks_preserving_activity,
    predictions_to_frontend,
    rank_actionable_predictions,
    prediction_lead_seconds,
    recommendation_to_agent,
    telemetry_event,
    _rack_load_snapshot,
    _time_to_impact_minutes,
)
from sentinel.telemetry.sample import TelemetryFrame
from sentinel.tts import AlertSpeaker, build_alert_text, build_operator_action_text

logger = logging.getLogger(__name__)

MAX_EVENTS = 500
STRESS_SEEK_T = 12_824_105  # queue peak in demo window
@dataclass
class PendingRecommendation:
    recommendation: Recommendation
    prediction: Prediction
    frame_t: int
    status: str = "pending"
    alert_text: Optional[str] = None
    alert_wav: Optional[Path] = None
    alert_status: str = "pending"


class ClusterRuntime:
    """Thread-safe singleton backing the FastAPI routes."""

    def __init__(
        self,
        *,
        log_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
        tts_enabled: Optional[bool] = None,
        alert_speaker: Optional[AlertSpeaker] = None,
        alert_dir: Optional[Path] = None,
    ):
        self._lock = threading.RLock()
        self.engine = Engine()
        self.thresholds = Thresholds.load(state_path) if state_path else Thresholds.load()
        self.predictor = PredictionEngine(engine=self.engine)
        self.agent: Optional[Agent] = None
        self._refresh_agent()
        self.decision_log = DecisionLog(log_path) if log_path else DecisionLog()
        self.learner = OverrideLearner(self.decision_log, self.thresholds)

        self._tts_enabled = config.SENTINEL_TTS_ENABLED if tts_enabled is None else tts_enabled
        self._tts_operator_actions = config.SENTINEL_TTS_OPERATOR_ACTIONS
        self._speaker = alert_speaker or AlertSpeaker()
        self._alert_dir = Path(alert_dir or config.ALERT_AUDIO_DIR)
        self._alert_dir.mkdir(parents=True, exist_ok=True)
        self._dismissed_alert_ids: set[str] = set()
        self._operator_action_clips: Dict[str, Path] = {}

        self.replay_status = "idle"
        self._pause = threading.Event()
        self._pause.set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stress = False

        self.latest_frame: Optional[TelemetryFrame] = None
        self._history: Dict[str, List[Dict]] = {}
        self._last_map_racks: List[Dict[str, Any]] = []
        self._migration_pin_racks: List[str] = []
        self._latest_predictions: List[Prediction] = []
        self._pending: Optional[PendingRecommendation] = None
        self._events: Deque[Dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._ws_subscribers: List[deque] = []

    def warm(self) -> None:
        """Pre-seek the engine so the first dashboard fetch is fast."""
        with self._lock:
            if self.latest_frame is not None:
                return
            self.engine.seek(DEMO_WINDOW[0])
            self.latest_frame = self.engine.tick()

    # --- lifecycle -----------------------------------------------------------

    def start_replay(self) -> None:
        with self._lock:
            status = "stress" if self._stress else "running"
            if self._thread and self._thread.is_alive():
                self.replay_status = status
                self._pause.set()
                self._push_event("Replay resumed", event_type="agent_event", severity="healthy")
                return
            self._stop.clear()
            self._pause.set()
            self.replay_status = status
            self._thread = threading.Thread(target=self._replay_loop, daemon=True, name="sentinel-replay")
            self._thread.start()
            self._push_event("Replay started", event_type="agent_event", severity="healthy")

    def pause_replay(self) -> None:
        with self._lock:
            self._pause.clear()
            self.replay_status = "paused"
            self._push_event("Replay paused", event_type="agent_event", severity="watch")

    def resume_replay(self) -> None:
        with self._lock:
            self._pause.set()
            self.replay_status = "stress" if self._stress else "running"
            self._push_event("Replay resumed", event_type="agent_event", severity="healthy")

    def trigger_stress(self) -> Dict[str, Any]:
        with self._lock:
            self._stress = True
            self.replay_status = "stress"
            self._cancel_pending_alert()
            self._pending = None
            self._dismissed_alert_ids.clear()
            self._migration_pin_racks.clear()
            saved_map = list(self._last_map_racks)
            t_now = self.engine.replayer.t
            preds: List[Prediction] = []
            if t_now < STRESS_SEEK_T:
                warm_start = max(DEMO_WINDOW[0], STRESS_SEEK_T - BURN_IN_S)
                self._history.clear()
                self.engine.seek(warm_start)
                frame = None
                while self.engine.replayer.t < STRESS_SEEK_T:
                    frame = self.engine.tick()
                    preds = self.predictor.on_frame(frame)
                if frame is None:
                    frame = self.engine.tick()
                    preds = self.predictor.on_frame(frame)
            else:
                frame = self.latest_frame
                if frame is None or frame.t < STRESS_SEEK_T:
                    self.engine.seek(STRESS_SEEK_T)
                    frame = self.engine.tick()
                    preds = self.predictor.on_frame(frame)
                elif frame.t > STRESS_SEEK_T:
                    preds = self.predictor.on_frame(frame)
                else:
                    frame = self.engine.tick()
                    preds = self.predictor.on_frame(frame)
            assert frame is not None
            self.latest_frame = frame
            self._latest_predictions = preds
            self._maybe_alert(frame, preds)
            self._emit_dcgm_events(frame)
            state = self._build_cluster_state(frame, preds)
            hot_id = "rack-00"
            if saved_map and state.get("mapRacks"):
                state["mapRacks"] = merge_map_racks_preserving_activity(
                    saved_map,
                    state["mapRacks"],
                    hot_id=hot_id,
                )
            if state.get("mapRacks"):
                self._last_map_racks = state["mapRacks"]
            self._push_event(
                "Stress scenario — seeking to queue peak in demo trace",
                event_type="agent_event",
                severity="warning",
            )
            if not (self._thread and self._thread.is_alive()):
                self.start_replay()
            rec = None
            if self._pending is not None and self._pending.status == "pending":
                rec = self._pending_to_agent(self._pending)
            return {"state": state, "recommendation": rec}

    def reset_demo(self) -> None:
        with self._lock:
            self._stop.set()
            self._pause.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None
            self._stop.clear()
            self._stress = False
            self.replay_status = "idle"
            self.latest_frame = None
            self._history.clear()
            self._last_map_racks.clear()
            self._migration_pin_racks.clear()
            self._latest_predictions.clear()
            self._pending = None
            self._events.clear()
            self._dismissed_alert_ids.clear()
            self._operator_action_clips.clear()
            self.engine = Engine()
            self.predictor = PredictionEngine(engine=self.engine)
            self._refresh_agent()
            self.decision_log.clear()
            self._push_event("Demo reset", event_type="agent_event", severity="watch")

    # --- read API ------------------------------------------------------------

    def cluster_state(self) -> Dict[str, Any]:
        with self._lock:
            if self.latest_frame is None:
                self.engine.seek(DEMO_WINDOW[0])
                frame = self.engine.tick()
                self.latest_frame = frame
            state = self._build_cluster_state(self.latest_frame, self._latest_predictions)
            return self._apply_map_preservation(state)

    def _apply_map_preservation(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Keep floor activity + migration targets visible across polls (live stress/resolved)."""
        map_racks = state.get("mapRacks")
        if not map_racks:
            return state
        preserve = self._stress or self.replay_status in ("stress", "resolved")
        if not preserve or not self._last_map_racks:
            self._last_map_racks = list(map_racks)
            return state
        hot_id = None
        if self._stress and self.latest_frame is not None:
            hot_id = max(
                self.latest_frame.racks,
                key=lambda r: (r.gpu_demand, r.temp_c_mean_active, r.queued_heavy),
            ).rack_id
        merged = merge_map_racks_preserving_activity(
            self._last_map_racks,
            map_racks,
            hot_id=hot_id,
        )
        state = {**state, "mapRacks": merged}
        self._last_map_racks = merged
        return state

    def _build_cluster_state(
        self, frame: TelemetryFrame, predictions: Optional[List[Prediction]] = None
    ) -> Dict[str, Any]:
        return frame_to_cluster_state(
            frame,
            self.replay_status,
            trends=self.predictor._trends,
            history=self._history,
            pod_rack=self.engine.replayer.state.pod_rack,
            map_pin_racks=self._map_pin_rack_ids(),
            predictions=predictions if predictions is not None else self._latest_predictions,
        )

    def current_recommendation(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._pending is None or self._pending.status != "pending":
                return None
            return self._pending_to_agent(self._pending)

    def alert_audio_path(self, recommendation_id: str) -> Optional[Path]:
        with self._lock:
            if (
                self._pending is None
                or self._pending.recommendation.recommendation_id != recommendation_id
            ):
                return None
            if self._pending.alert_status != "ready" or self._pending.alert_wav is None:
                return None
            path = self._pending.alert_wav
            return path if path.exists() else None

    def telemetry_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self._events))

    def decision_log_entries(self) -> List[Dict[str, Any]]:
        """Append-only JSONL only — no live telemetry mixed in."""
        entries = self.decision_log.read_all()
        mapped = [decision_entry_to_frontend(e) for e in entries]
        return mapped[-MAX_EVENTS:]

    def latest_frame_dict(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.latest_frame.to_dict() if self.latest_frame else None

    def dismiss_recommendation_audio(self, recommendation_id: str) -> None:
        """Stop serving recommendation alert audio (tab closed or operator dismissed)."""
        with self._lock:
            self._dismissed_alert_ids.add(recommendation_id)
            if (
                self._pending is not None
                and self._pending.recommendation.recommendation_id == recommendation_id
            ):
                self._cancel_pending_alert()

    def operator_action_audio_path(self, recommendation_id: str) -> Optional[Path]:
        with self._lock:
            path = self._operator_action_clips.get(recommendation_id)
            return path if path and path.exists() else None

    # --- operator actions ----------------------------------------------------

    def approve_recommendation(
        self, recommendation_id: str, *, voice_confirm: bool = False
    ) -> Dict[str, Any]:
        with self._lock:
            pending = self._require_pending(recommendation_id)
            rec = pending.recommendation
            pred = pending.prediction
            self._cancel_pending_alert()
            ok = False
            before_src = before_dst = None
            if self.latest_frame and rec.from_rack and rec.to_rack:
                before_src = _rack_load_snapshot(self.latest_frame, rec.from_rack)
                before_dst = _rack_load_snapshot(self.latest_frame, rec.to_rack)
            if rec.job_id and rec.to_rack:
                ok = self.engine.apply_action("MIGRATE_JOB", rec.job_id, rec.to_rack)
            if ok:
                self.latest_frame = self.engine.refresh_frame()
                self._refresh_agent()
                self._stress = False
                for rid in (rec.from_rack, rec.to_rack):
                    if rid and rid not in self._migration_pin_racks:
                        self._migration_pin_racks.append(rid)
                migrated_state = self._apply_map_preservation(
                    self._build_cluster_state(self.latest_frame, self._latest_predictions)
                )
                if migrated_state.get("mapRacks"):
                    self._last_map_racks = migrated_state["mapRacks"]
            pending.status = "approved"
            self.decision_log.record(
                t=pending.frame_t,
                prediction=pred,
                recommendation=rec,
                operator_action="APPROVE",
                operator_alternative=None,
                outcome="AVERTED" if ok else "UNKNOWN",
                lead_time_seconds=prediction_lead_seconds(pred),
                latency_ms=0.0,
            )
            rack_id = rec.from_rack or pred.target.get("id")
            after_src = after_dst = None
            if ok and self.latest_frame and rec.from_rack and rec.to_rack:
                after_src = _rack_load_snapshot(self.latest_frame, rec.from_rack)
                after_dst = _rack_load_snapshot(self.latest_frame, rec.to_rack)
            if ok:
                title = "Incident averted — workload migrated"
                if after_src and after_dst:
                    detail = (
                        f"Migrated {rec.job_id} from {rec.from_rack} to {rec.to_rack}. "
                        f"{rec.from_rack}: {after_src.get('gpuUtilizationPct', 0):.1f}% scheduled load "
                        f"({after_src.get('gpuDemandGpus', 0):.1f} GPU), "
                        f"queue {after_src.get('queuePressurePct', 0):.0f}%. "
                        f"{rec.to_rack}: {after_dst.get('gpuUtilizationPct', 0):.1f}% scheduled load "
                        f"({after_dst.get('gpuDemandGpus', 0):.1f} GPU), "
                        f"queue {after_dst.get('queuePressurePct', 0):.0f}%."
                    )
                else:
                    detail = (
                        f"Migrated {rec.job_id} from {rec.from_rack} to {rec.to_rack}. "
                        f"Thermal load shifted off {rec.from_rack}; {rec.to_rack} absorbs the job with headroom."
                    )
                self._push_event(
                    detail,
                    event_type="operator_event",
                    severity="healthy",
                    rack_id=rec.to_rack,
                )
                self._push_event(
                    f"Projected throttling risk on {rec.from_rack} reduced after operator approval",
                    event_type="agent_event",
                    severity="healthy",
                    rack_id=rack_id,
                )
            else:
                title = "Approval recorded"
                detail = (
                    f"Operator approved the recommendation for {rec.job_id}, "
                    f"but migration could not be applied automatically."
                )
                self._push_event(
                    f"Operator approved: migrate {rec.job_id} to {rec.to_rack}",
                    event_type="operator_event",
                    severity="watch",
                    rack_id=rack_id,
                )
            self._pending = None
            self.replay_status = "resolved"
            result = {
                "success": True,
                "action": "approved",
                "outcome": "averted" if ok else "unknown",
                "title": title,
                "detail": detail,
                "jobId": rec.job_id,
                "fromRack": rec.from_rack,
                "toRack": rec.to_rack,
                "fromRackDemandGpus": after_src.get("gpuDemandGpus") if after_src else None,
                "toRackDemandGpus": after_dst.get("gpuDemandGpus") if after_dst else None,
                "fromRackUtilizationPct": after_src.get("gpuUtilizationPct") if after_src else None,
                "toRackUtilizationPct": after_dst.get("gpuUtilizationPct") if after_dst else None,
                "fromRackQueuePressurePct": after_src.get("queuePressurePct") if after_src else None,
                "toRackQueuePressurePct": after_dst.get("queuePressurePct") if after_dst else None,
                "fromRackBefore": before_src,
                "toRackBefore": before_dst,
                "expectedEffect": rec.expected_effect,
                "timeToImpactMinutes": _time_to_impact_minutes(pred),
            }
        result.update(
            self._operator_voice_payload(
                recommendation_id,
                "approved",
                detail=detail,
                voice_confirm=voice_confirm,
                job_id=rec.job_id,
                from_rack=rec.from_rack,
                to_rack=rec.to_rack,
            )
        )
        return result

    def override_recommendation(
        self, recommendation_id: str, reason: str, *, voice_confirm: bool = False
    ) -> Dict[str, Any]:
        with self._lock:
            pending = self._require_pending(recommendation_id)
            rec = pending.recommendation
            pred = pending.prediction
            self._cancel_pending_alert()
            pending.status = "overridden"
            self.decision_log.record(
                t=pending.frame_t,
                prediction=pred,
                recommendation=rec,
                operator_action="OVERRIDE",
                operator_alternative={"reason": reason},
                outcome="OVERRIDDEN",
                lead_time_seconds=prediction_lead_seconds(pred),
                latency_ms=0.0,
            )
            rack_id = rec.from_rack or pred.target.get("id")
            eta_min = _time_to_impact_minutes(pred)
            detail = (
                f"No migration applied — {rec.job_id or 'workload'} stays on {rack_id}. "
                f"Reason: {reason}. "
                f"Impact window unchanged (~{eta_min} min to projected throttling)."
            )
            self.learner.apply()
            self.thresholds.save()
            self._push_event(
                detail,
                event_type="operator_event",
                severity="warning",
                rack_id=rack_id,
            )
            self._push_event(
                "Agent updated future recommendations based on operator feedback",
                event_type="agent_event",
                severity="watch",
            )
            self._pending = None
            self.replay_status = "resolved"
            result = {
                "success": True,
                "action": "overridden",
                "outcome": "overridden",
                "title": "Recommendation overridden — no migration",
                "detail": detail,
                "jobId": rec.job_id,
                "fromRack": rack_id,
                "toRack": rec.to_rack,
                "reason": reason,
                "timeToImpactMinutes": eta_min,
            }
        result.update(
            self._operator_voice_payload(
                recommendation_id,
                "overridden",
                detail=detail,
                voice_confirm=voice_confirm,
                job_id=rec.job_id,
                from_rack=rack_id,
                to_rack=rec.to_rack,
                reason=reason,
            )
        )
        return result

    def explain_recommendation(self, recommendation_id: str) -> Dict[str, Any]:
        with self._lock:
            pending = self._require_pending(recommendation_id)
            rec = pending.recommendation
            self._push_event(
                f"Operator asked agent to explain {rec.from_rack} risk",
                event_type="operator_event",
                severity="watch",
                rack_id=rec.from_rack,
            )
            return self._pending_to_agent(pending)

    # --- internal ------------------------------------------------------------

    def _map_pin_rack_ids(self) -> Optional[List[str]]:
        """Keep hot + migration racks visible on the 3D floor during stress."""
        ids: List[str] = list(self._migration_pin_racks)
        if self._pending is not None and self._pending.status == "pending":
            rec = self._pending.recommendation
            for rid in (rec.from_rack, rec.to_rack):
                if rid and rid not in ids:
                    ids.append(rid)
        if self._stress and self.latest_frame is not None:
            hottest = max(
                self.latest_frame.racks,
                key=lambda r: (r.gpu_demand, r.temp_c_mean_active, r.queued_heavy),
            )
            if hottest.rack_id not in ids:
                ids.insert(0, hottest.rack_id)
        return ids or None

    def _cancel_pending_alert(self) -> None:
        if self._pending is None:
            return
        if self._pending.alert_status in ("pending", "generating", "ready"):
            self._pending.alert_status = "cancelled"

    def _operator_action_wav_path(self, recommendation_id: str) -> Path:
        safe_id = recommendation_id.replace("/", "_")
        return self._alert_dir / f"{safe_id}-operator.wav"

    def _operator_voice_payload(
        self,
        recommendation_id: str,
        action: str,
        *,
        detail: str,
        voice_confirm: bool,
        job_id: Optional[str] = None,
        from_rack: Optional[str] = None,
        to_rack: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not voice_confirm or not self._tts_enabled or not self._tts_operator_actions:
            return {"operatorAlertStatus": "skipped"}
        if not self._speaker.is_configured:
            return {"operatorAlertStatus": "skipped"}

        text = build_operator_action_text(
            action,
            detail=detail,
            job_id=job_id,
            from_rack=from_rack,
            to_rack=to_rack,
            reason=reason,
        )
        out = self._operator_action_wav_path(recommendation_id)
        try:
            wav = self._speaker.speak_sync(text, output_wav=out)
        except Exception:
            logger.exception("Operator action TTS failed for %s", recommendation_id)
            return {"operatorAlertText": text, "operatorAlertStatus": "failed"}

        if wav is None:
            return {"operatorAlertText": text, "operatorAlertStatus": "failed"}

        with self._lock:
            self._operator_action_clips[recommendation_id] = wav
        return {
            "operatorAlertText": text,
            "operatorAlertStatus": "ready",
            "operatorAlertAudioUrl": (
                f"/api/agent/recommendation/{recommendation_id}/operator-audio"
            ),
        }

    def _pending_to_agent(self, pending: PendingRecommendation) -> Dict[str, Any]:
        rec_id = pending.recommendation.recommendation_id
        dismissed = rec_id in self._dismissed_alert_ids
        alert_status = "dismissed" if dismissed else pending.alert_status
        audio_url = None
        if (
            not dismissed
            and pending.alert_status == "ready"
            and pending.alert_wav is not None
        ):
            audio_url = f"/api/agent/recommendation/{rec_id}/alert-audio"
        return recommendation_to_agent(
            pending.recommendation,
            pending.prediction,
            status=pending.status,
            alert_text=pending.alert_text,
            alert_status=alert_status,
            alert_audio_url=audio_url,
        )

    def _alert_wav_path(self, recommendation_id: str) -> Path:
        safe_id = recommendation_id.replace("/", "_")
        return self._alert_dir / f"{safe_id}.wav"

    def _queue_tts(self, recommendation_id: str) -> None:
        with self._lock:
            pending = self._pending
            if pending is None or pending.recommendation.recommendation_id != recommendation_id:
                return
            if pending.alert_status in ("generating", "ready"):
                return
            alert_text = build_alert_text(pending.prediction, pending.recommendation)
            pending.alert_text = alert_text

        if not self._tts_enabled:
            with self._lock:
                if (
                    self._pending
                    and self._pending.recommendation.recommendation_id == recommendation_id
                ):
                    self._pending.alert_status = "skipped"
            return

        if not self._speaker.is_configured:
            with self._lock:
                if (
                    self._pending
                    and self._pending.recommendation.recommendation_id == recommendation_id
                ):
                    self._pending.alert_status = "skipped"
            return

        def _run() -> None:
            with self._lock:
                pending = self._pending
                if pending is None or pending.recommendation.recommendation_id != recommendation_id:
                    return
                if pending.alert_status in ("generating", "ready", "cancelled"):
                    return
                rec = pending.recommendation
                pred = pending.prediction
                text = pending.alert_text or build_alert_text(pred, rec)
                pending.alert_status = "generating"
                out = self._alert_wav_path(recommendation_id)

            try:
                wav = None
                for attempt in range(3):
                    try:
                        wav = self._speaker.speak_recommendation(
                            pred, rec, output_wav=out, alert_text=text
                        )
                        if wav is not None:
                            break
                    except Exception:
                        if attempt == 2:
                            raise
                        logger.warning(
                            "TTS attempt %s failed for %s — retrying",
                            attempt + 1,
                            recommendation_id,
                        )
                        time.sleep(0.4 * (attempt + 1))
                with self._lock:
                    if (
                        self._pending
                        and self._pending.recommendation.recommendation_id == recommendation_id
                    ):
                        if self._pending.alert_status == "cancelled":
                            return
                        if recommendation_id in self._dismissed_alert_ids:
                            self._pending.alert_status = "dismissed"
                            return
                        self._pending.alert_wav = wav
                        self._pending.alert_status = "ready" if wav else "failed"
                if wav:
                    self._push_event(
                        "Voice alert ready for operator",
                        event_type="agent_event",
                        severity="watch",
                        rack_id=rec.from_rack,
                    )
            except Exception:
                logger.exception("TTS failed for recommendation %s", recommendation_id)
                with self._lock:
                    if (
                        self._pending
                        and self._pending.recommendation.recommendation_id == recommendation_id
                    ):
                        self._pending.alert_status = "failed"

        threading.Thread(
            target=_run, daemon=True, name=f"tts-{recommendation_id}"
        ).start()

    def _require_pending(self, recommendation_id: str) -> PendingRecommendation:
        if self._pending is None or self._pending.recommendation.recommendation_id != recommendation_id:
            raise KeyError(recommendation_id)
        return self._pending

    def _refresh_agent(self) -> None:
        recommender = Recommender(
            topology=self.engine.topology,
            state=self.engine.replayer.state,
            placement=self.engine.replayer.placement,
            pods_by_name=self.engine.replayer.pods,
            rack_temp_provider=lambda rid: (
                self.predictor._trends[rid].level if rid in self.predictor._trends else None
            ),
        )
        if self.agent is None:
            self.agent = Agent(recommender)
        else:
            self.agent.recommender = recommender

    def _push_event(self, message: str, **kwargs) -> None:
        if "t" not in kwargs and self.latest_frame is not None:
            kwargs["t"] = self.latest_frame.t
        evt = telemetry_event(message, **kwargs)
        self._events.append(evt)
        for sub in self._ws_subscribers:
            sub.append(evt)

    def _emit_dcgm_events(self, frame: TelemetryFrame) -> None:
        for evt in events_from_frame(frame):
            self._events.append(evt)
            for sub in self._ws_subscribers:
                sub.append(evt)

    def _maybe_alert(self, frame: TelemetryFrame, preds: List[Prediction]) -> None:
        if self.replay_status == "resolved":
            return
        if self._pending is not None and self._pending.status == "pending":
            return
        ranked = rank_actionable_predictions(preds)
        if not ranked:
            return
        self._refresh_agent()
        rec = None
        chosen: Optional[Prediction] = None
        for p in ranked:
            rec = self.agent.recommend(p)
            if rec is not None:
                chosen = p
                break
        if rec is None or chosen is None:
            return
        p = chosen
        self._pending = PendingRecommendation(recommendation=rec, prediction=p, frame_t=frame.t)
        rack_id = p.target.get("rack_id") or p.target.get("id")
        lead_s = prediction_lead_seconds(p)
        self.decision_log.record(
            t=frame.t,
            prediction=p,
            recommendation=rec,
            operator_action="SURFACED",
            operator_alternative=None,
            outcome="PENDING",
            lead_time_seconds=lead_s,
            latency_ms=0.0,
        )
        self._push_event(
            f"Risk detected ({p.type}) on {rack_id}",
            event_type="risk_detected",
            severity="warning",
            rack_id=rack_id,
            t=frame.t,
        )
        self._push_event(
            rec.justification,
            event_type="agent_event",
            severity="watch",
            rack_id=rack_id,
            t=frame.t,
        )
        self._queue_tts(rec.recommendation_id)

    def step_once(self) -> TelemetryFrame:
        """Single tick — used in tests without background thread."""
        with self._lock:
            if self.latest_frame is None:
                self.engine.seek(DEMO_WINDOW[0])
            frame = self.engine.tick()
            preds = self.predictor.on_frame(frame)
            self.latest_frame = frame
            self._latest_predictions = preds
            self._maybe_alert(frame, preds)
            self._emit_dcgm_events(frame)
            return frame

    def _replay_loop(self) -> None:
        try:
            with self._lock:
                if self.latest_frame is None and not self._stress:
                    self.engine.seek(DEMO_WINDOW[0])
                start, end = DEMO_WINDOW
            while not self._stop.is_set():
                self._pause.wait()
                if self._stop.is_set():
                    break
                with self._lock:
                    if self.engine.replayer.t >= end:
                        self.engine.seek(start)
                        self.predictor = PredictionEngine(engine=self.engine)
                        self._history.clear()
                        self._pending = None
                    frame = self.engine.tick()
                    preds = self.predictor.on_frame(frame)
                    self.latest_frame = frame
                    self._latest_predictions = preds
                    self._maybe_alert(frame, preds)
                    self._emit_dcgm_events(frame)
                    state = self._build_cluster_state(frame, preds)
                    state = self._apply_map_preservation(state)
                    if state.get("mapRacks"):
                        self._last_map_racks = state["mapRacks"]
                time.sleep(TICK_TRACE_S / SPEEDUP)
        except Exception:
            logger.exception("Replay loop failed")
            with self._lock:
                self.replay_status = "idle"


_runtime: Optional[ClusterRuntime] = None
_runtime_lock = threading.Lock()


def get_runtime() -> ClusterRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = ClusterRuntime()
        return _runtime


def reset_runtime_for_tests(**kwargs) -> ClusterRuntime:
    global _runtime
    with _runtime_lock:
        _runtime = ClusterRuntime(**kwargs)
        return _runtime
