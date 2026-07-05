"""FastAPI application — REST routes matching frontend/src/lib/api.ts."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from sentinel.server.runtime import get_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_runtime().warm()
    yield


app = FastAPI(title="Sentinel API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OverrideBody(BaseModel):
    reason: str = ""
    voiceConfirm: bool = False


class ApproveBody(BaseModel):
    voiceConfirm: bool = False


class DismissAudioBody(BaseModel):
    recommendationId: str


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cluster/state")
def get_cluster_state() -> Dict[str, Any]:
    return get_runtime().cluster_state()


@app.get("/api/agent/recommendation")
def get_recommendation(response: Response):
    rec = get_runtime().current_recommendation()
    if rec is None:
        response.status_code = 204
        return None
    return rec


@app.post("/api/agent/recommendation/{recommendation_id}/approve")
def approve_recommendation(recommendation_id: str, body: ApproveBody = ApproveBody()) -> Dict[str, Any]:
    try:
        return get_runtime().approve_recommendation(
            recommendation_id, voice_confirm=body.voiceConfirm
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Recommendation not found")


@app.post("/api/agent/recommendation/{recommendation_id}/override")
def override_recommendation(recommendation_id: str, body: OverrideBody) -> Dict[str, Any]:
    try:
        return get_runtime().override_recommendation(
            recommendation_id, body.reason, voice_confirm=body.voiceConfirm
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Recommendation not found")


@app.post("/api/agent/recommendation/dismiss-audio")
def dismiss_recommendation_audio(body: DismissAudioBody) -> Dict[str, bool]:
    get_runtime().dismiss_recommendation_audio(body.recommendationId)
    return {"success": True}


@app.post("/api/agent/recommendation/{recommendation_id}/why")
def why_recommendation(recommendation_id: str) -> Dict[str, Any]:
    try:
        return get_runtime().explain_recommendation(recommendation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Recommendation not found")


@app.get("/api/agent/recommendation/{recommendation_id}/alert-audio")
def get_alert_audio(recommendation_id: str):
    path = get_runtime().alert_audio_path(recommendation_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Alert audio not ready")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/agent/recommendation/{recommendation_id}/operator-audio")
def get_operator_action_audio(recommendation_id: str):
    path = get_runtime().operator_action_audio_path(recommendation_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Operator action audio not ready")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/telemetry/events")
def get_telemetry_events() -> List[Dict[str, Any]]:
    return get_runtime().telemetry_events()


@app.get("/api/telemetry/dcgm/samples")
def get_dcgm_samples(limit: int = 32) -> List[Dict[str, Any]]:
    """Latest per-GPU DCGM-style samples from the replay frame."""
    frame = get_runtime().latest_frame_dict()
    if not frame:
        return []
    samples = frame.get("samples") or []
    return samples[: max(1, min(limit, 128))]


@app.post("/api/replay/start")
def replay_start() -> Dict[str, bool]:
    get_runtime().start_replay()
    return {"success": True}


@app.post("/api/replay/pause")
def replay_pause() -> Dict[str, bool]:
    get_runtime().pause_replay()
    return {"success": True}


@app.post("/api/replay/resume")
def replay_resume() -> Dict[str, bool]:
    get_runtime().resume_replay()
    return {"success": True}


@app.post("/api/replay/stress")
def replay_stress() -> Dict[str, Any]:
    payload = get_runtime().trigger_stress()
    return {"success": True, **payload}


@app.get("/api/decision-log")
def get_decision_log() -> List[Dict[str, Any]]:
    return get_runtime().decision_log_entries()


@app.post("/api/demo/reset")
def demo_reset() -> Dict[str, bool]:
    get_runtime().reset_demo()
    return {"success": True}


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    runtime = get_runtime()
    buf: List[Dict[str, Any]] = []
    import collections

    sub = collections.deque()
    runtime._ws_subscribers.append(sub)
    try:
        frame = runtime.latest_frame_dict()
        if frame:
            await websocket.send_text(json.dumps({"type": "frame", "data": frame}))
        while True:
            while sub:
                evt = sub.popleft()
                await websocket.send_text(json.dumps({"type": "event", "data": evt}))
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if sub in runtime._ws_subscribers:
            runtime._ws_subscribers.remove(sub)
