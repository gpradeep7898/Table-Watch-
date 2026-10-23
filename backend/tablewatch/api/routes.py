"""HTTP + WebSocket API. Every endpoint is backed by real engine or storage logic."""

from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..analytics.metrics import compute as compute_analytics
from ..core.models import ActionError, ActionType
from ..core.thresholds import LIMITS
from ..core.zones import ANCHOR_MODES, Zone, ZoneAssigner, validate_zones
from ..simulation.history import HistoryJob, start_history_thread
from ..simulation.session import SIM_CAMERA_ID
from ..vision.sources import SourceError, VIDEO_EXTENSIONS, media_roots, redact, resolve_video_path, validate_rtsp_url

router = APIRouter(prefix="/api")
ws_router = APIRouter()

Dataset = Literal["simulation", "recorded", "live"]


def rt(request: Request):
    return request.app.state.runtime


def conflict(exc: ActionError) -> HTTPException:
    status = 404 if exc.code == "not_found" else 409
    return HTTPException(status, detail={"code": exc.code, "message": str(exc)})


def bad(message: str | list[str], code: str = "invalid") -> HTTPException:
    errors = message if isinstance(message, list) else [message]
    return HTTPException(400, detail={"code": code, "message": "; ".join(errors), "errors": errors})


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    from ..vision.detector import model_status

    r = rt(request)
    return {"ok": True, "app": "tablewatch", "mode": r.mode, "run_id": r.run_id, "vision": model_status(), "error": r.last_error}


@router.get("/state")
def state(request: Request) -> dict[str, Any]:
    return rt(request).snapshot()


@router.get("/frames")
def frames(request: Request) -> dict[str, Any]:
    return rt(request).frame_payload() or {"frames": []}


@router.get("/layout")
def layout(request: Request) -> dict[str, Any]:
    r = rt(request)
    anchor, zones, version = r.zones_for(SIM_CAMERA_ID)
    return {**r.layout.to_dict(), "sim_camera": {"id": SIM_CAMERA_ID, "anchor": anchor, "zones_version": version,
                                                 "zones": [z.to_dict() for z in zones]}}


@router.get("/tables/{table_id}")
def table_detail(request: Request, table_id: str) -> dict[str, Any]:
    r = rt(request)
    if r.engine is None or table_id not in r.engine.tables:
        raise HTTPException(404, detail={"code": "not_found", "message": f"Unknown table {table_id}"})
    view = r.engine.table_view(table_id, r.now())
    events = r.store.events(r.dataset, r.run_id, table_id=table_id, limit=40)
    return {"table": view, "events": events}


class ActionIn(BaseModel):
    action: Literal["confirm_service", "confirm_cleaning", "correct_status", "verify_observation"]
    to_state: Literal["FREE", "SEATED_UNSERVED", "SERVED", "NEEDS_CLEANING"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    actor: str = Field(default="operator", min_length=1, max_length=40)
    request_id: str | None = Field(default=None, max_length=80)


ACTIONS = {
    "confirm_service": ActionType.CONFIRM_SERVICE,
    "confirm_cleaning": ActionType.CONFIRM_CLEANING,
    "correct_status": ActionType.CORRECT_STATUS,
    "verify_observation": ActionType.VERIFY_OBSERVATION,
}


@router.post("/tables/{table_id}/actions")
async def table_action(request: Request, table_id: str, body: ActionIn) -> dict[str, Any]:
    r = rt(request)
    try:
        events = r.act(ACTIONS[body.action], table_id=table_id, to_state=body.to_state, reason=body.reason,
                       actor=body.actor.strip(), request_id=body.request_id)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush()
    return {"ok": True, "events": events, "table": r.engine.table_view(table_id, r.now())}


# ---------------------------------------------------------------------------
# alerts and events
# ---------------------------------------------------------------------------
def _scope_run(r, dataset: str | None, scope: str, run_id: str | None) -> tuple[str, str | None]:
    ds = dataset or r.dataset
    if run_id:
        return ds, run_id
    if scope == "current":
        return ds, r.run_id if ds == r.dataset else None
    return ds, None


@router.get("/alerts")
def list_alerts(request: Request, dataset: Dataset | None = None, scope: Literal["current", "all"] = "current",
                run_id: str | None = None, status: Literal["ACTIVE", "ACKNOWLEDGED", "RESOLVED"] | None = None,
                type: str | None = None, table_id: str | None = None, limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    r = rt(request)
    ds, rid = _scope_run(r, dataset, scope, run_id)
    rows = r.store.alerts(ds, rid, status, type, table_id, limit)
    for a in rows:
        a["created_ts"] = datetime.fromtimestamp(a["created_wall"]).isoformat(timespec="seconds")
    return {"dataset": ds, "run_id": rid, "alerts": rows}


class AckIn(BaseModel):
    actor: str = Field(default="operator", min_length=1, max_length=40)
    request_id: str | None = Field(default=None, max_length=80)


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge(request: Request, alert_id: str, body: AckIn) -> dict[str, Any]:
    r = rt(request)
    try:
        events = r.act(ActionType.ACKNOWLEDGE_ALERT, alert_id=alert_id, actor=body.actor, request_id=body.request_id)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush()
    alert = r.engine.alert(alert_id)
    return {"ok": True, "events": events, "alert": alert.to_dict() if alert else None}


@router.get("/events")
def list_events(request: Request, dataset: Dataset | None = None, scope: Literal["current", "all"] = "current",
                run_id: str | None = None, table_id: str | None = None, types: str | None = None,
                limit: int = Query(100, ge=1, le=2000), before_seq: int | None = None) -> dict[str, Any]:
    r = rt(request)
    ds, rid = _scope_run(r, dataset, scope, run_id)
    type_list = [t for t in (types or "").split(",") if t]
    rows = r.store.events(ds, rid, table_id, type_list, limit, before_seq)
    for e in rows:
        e["ts"] = datetime.fromtimestamp(e["wall"]).isoformat(timespec="seconds")
    return {"dataset": ds, "run_id": rid, "events": rows}


@router.get("/runs")
def list_runs(request: Request, dataset: Dataset | None = None, kind: str | None = None) -> dict[str, Any]:
    return {"runs": rt(request).store.runs(dataset, kind)}


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------
@router.get("/sim/scenarios")
def scenarios(request: Request) -> dict[str, Any]:
    return {"scenarios": [s.to_dict() for s in rt(request).scenarios.values()]}


class SimStartIn(BaseModel):
    scenario: str | None = None
    seed: int | None = Field(default=None, ge=0, le=2**31)
    autoplay: bool | None = None
    speed: Literal[1, 5, 10] | None = None


@router.post("/sim/start")
async def sim_start(request: Request, body: SimStartIn) -> dict[str, Any]:
    r = rt(request)
    try:
        status = r.start_simulation(body.scenario, body.seed, body.autoplay, body.speed)
    except ValueError as exc:
        raise bad(str(exc)) from exc
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "sim": status}


class SimControlIn(BaseModel):
    action: Literal["play", "pause", "resume", "speed"]
    speed: float | None = None


@router.post("/sim/control")
async def sim_control(request: Request, body: SimControlIn) -> dict[str, Any]:
    try:
        status = rt(request).sim_control(body.action, body.speed)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "sim": status}


class AdvanceIn(BaseModel):
    seconds: float = Field(gt=0, le=3600)


@router.post("/sim/advance")
async def sim_advance(request: Request, body: AdvanceIn) -> dict[str, Any]:
    try:
        status = rt(request).advance(body.seconds)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "sim": status}


@router.get("/sim/ground-truth")
def ground_truth(request: Request) -> dict[str, Any]:
    r = rt(request)
    if r.sim is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "No simulation run."})
    return {**r.sim.sim.ground_truth(), "run_id": r.sim.run_id}


class HistoryIn(BaseModel):
    days: int = Field(default=7, ge=1, le=14)


@router.post("/sim/history")
def sim_history(request: Request, body: HistoryIn) -> dict[str, Any]:
    r = rt(request)
    job: HistoryJob | None = request.app.state.history_job
    if job is not None and job.status == "running":
        raise HTTPException(409, detail={"code": "busy", "message": "History generation is already running."})
    anchor, zones, _ = r.zones_for(SIM_CAMERA_ID)
    request.app.state.history_job = start_history_thread(r.store.path, r.layout, r.thresholds, zones, anchor, body.days)
    return {"ok": True, "job": request.app.state.history_job.to_dict()}


@router.get("/sim/history")
def sim_history_status(request: Request) -> dict[str, Any]:
    job = request.app.state.history_job
    return {"job": job.to_dict() if job else None,
            "runs": len(rt(request).store.runs("simulation", "history"))}


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    r = rt(request)
    th, version = r.settings.thresholds()
    return {
        "thresholds": th.to_dict(),
        "thresholds_version": version,
        "threshold_limits": {k: {"min": lo, "max": hi, "description": desc} for k, (lo, hi, desc) in LIMITS.items()},
        "simulation": r.settings.simulation(),
        "privacy": r.settings.privacy(),
        "scenarios": [s.to_dict() for s in r.scenarios.values()],
        "bind_host": request.app.state.bind_host,
    }


@router.put("/settings/thresholds")
async def put_thresholds(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    r = rt(request)
    try:
        th, version = r.settings.save_thresholds(body)
    except ValueError as exc:
        errs = exc.args[0] if exc.args and isinstance(exc.args[0], list) else [str(exc)]
        raise bad(errs) from exc
    r.apply_thresholds(th, version)
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "thresholds": th.to_dict(), "version": version}


@router.post("/settings/thresholds/reset")
async def reset_thresholds(request: Request) -> dict[str, Any]:
    r = rt(request)
    th, version = r.settings.reset_thresholds()
    r.apply_thresholds(th, version)
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "thresholds": th.to_dict(), "version": version}


@router.put("/settings/simulation")
def put_sim_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    r = rt(request)
    try:
        return {"ok": True, "simulation": r.settings.save_simulation(body, set(r.scenarios))}
    except ValueError as exc:
        raise bad(exc.args[0]) from exc


@router.put("/settings/privacy")
def put_privacy(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    r = rt(request)
    try:
        return {"ok": True, "privacy": r.settings.save_privacy(body)}
    except ValueError as exc:
        raise bad(exc.args[0]) from exc


# ---------------------------------------------------------------------------
# cameras, video, zones
# ---------------------------------------------------------------------------
class CameraIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["file", "webcam", "rtsp"]
    path: str | None = Field(default=None, max_length=500)
    index: int | None = Field(default=None, ge=0, le=9)
    url: str | None = Field(default=None, max_length=1000)
    url_env: str | None = Field(default=None, max_length=64)
    anchor: Literal["bottom_center", "centroid"] = "bottom_center"
    sample_fps: float = Field(default=4.0, ge=0.5, le=15)
    reconnect_min_s: float = Field(default=1.0, ge=0.5, le=60)
    reconnect_max_s: float = Field(default=30.0, ge=1, le=600)
    authorized: bool = False


@router.get("/cameras")
def list_cameras(request: Request) -> dict[str, Any]:
    from ..vision.detector import model_status

    r = rt(request)
    return {"cameras": r.cameras(), "vision": model_status(), "media": list_media_files()}


@router.post("/cameras")
def add_camera(request: Request, body: CameraIn) -> dict[str, Any]:
    r = rt(request)
    if body.id == SIM_CAMERA_ID or r.camera(body.id) is not None:
        raise bad(f"Camera ID '{body.id}' is already used.")
    if not body.authorized:
        raise bad("Confirm that you are authorized to use this video source.", "not_authorized")
    cfg: dict[str, Any] = {"anchor": body.anchor, "sample_fps": body.sample_fps,
                           "reconnect_min_s": body.reconnect_min_s, "reconnect_max_s": body.reconnect_max_s}
    try:
        if body.kind == "file":
            path = resolve_video_path(body.path or "")
            cfg["path"] = _relative_media_path(path)
        elif body.kind == "webcam":
            cfg["index"] = int(body.index or 0)
        else:
            if body.url_env:
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", body.url_env):
                    raise SourceError("url_env must be an environment variable name like TABLEWATCH_CAM_URL")
                cfg["url_env"] = body.url_env
            else:
                cfg["url"] = validate_rtsp_url(body.url or "")
    except SourceError as exc:
        raise bad(redact(str(exc))) from exc
    if body.reconnect_min_s > body.reconnect_max_s:
        raise bad("reconnect_min_s must not exceed reconnect_max_s")
    r.store.upsert_camera(body.id, r.layout.id, body.name, body.kind, cfg)
    return {"ok": True, "camera": next(c for c in r.cameras() if c["id"] == body.id)}


def _relative_media_path(path: Path) -> str:
    for root in media_roots():
        if root == path or root in path.parents:
            from ..config.layout import project_root

            try:
                return str(path.relative_to(project_root()))
            except ValueError:
                return str(path)
    return str(path)


def list_media_files() -> list[dict[str, Any]]:
    from ..config.layout import project_root

    out = []
    for root in media_roots():
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                try:
                    rel = str(p.relative_to(project_root()))
                except ValueError:
                    rel = str(p)
                out.append({"path": rel, "name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)})
    return out[:200]


@router.delete("/cameras/{camera_id}")
def delete_camera(request: Request, camera_id: str) -> dict[str, Any]:
    r = rt(request)
    if camera_id == SIM_CAMERA_ID:
        raise bad("The simulation camera is built in.")
    if r.camera(camera_id) is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "Unknown camera."})
    if r.cv is not None and camera_id in r.cv.workers:
        raise HTTPException(409, detail={"code": "in_use", "message": "Stop video processing before deleting this camera."})
    r.store.delete_camera(camera_id)
    return {"ok": True}


def _grab_frame(r, camera_id: str) -> bytes:
    """Latest processed preview, or one fresh frame from an authorized source."""
    if r.cv is not None and camera_id in r.cv.workers and r.cv.workers[camera_id].latest_jpeg:
        return r.cv.workers[camera_id].latest_jpeg
    cam = r.camera(camera_id)
    if cam is None or cam["kind"] == "simulation":
        raise HTTPException(404, detail={"code": "no_frame", "message": "This camera has no video frames."})
    try:
        import cv2

        from ..vision.sources import make_source
    except Exception as exc:
        raise HTTPException(409, detail={"code": "vision_unavailable",
                                         "message": "OpenCV is not installed. Run ./scripts/setup.sh --vision"}) from exc
    try:
        src = make_source(cam)
        src.open()
        try:
            ok, frame, _ = src.read()
        finally:
            src.close()
    except SourceError as exc:
        raise HTTPException(409, detail={"code": "source_error", "message": redact(str(exc))}) from exc
    if not ok:
        raise HTTPException(409, detail={"code": "source_error", "message": "Could not read a frame from this source."})
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes()


@router.get("/cameras/{camera_id}/snapshot.jpg")
def snapshot_jpg(request: Request, camera_id: str) -> Response:
    data = _grab_frame(rt(request), camera_id)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/stream.mjpg")
async def stream_mjpg(request: Request, camera_id: str) -> StreamingResponse:
    r = rt(request)
    if r.cv is None or camera_id not in r.cv.workers:
        raise HTTPException(409, detail={"code": "not_running", "message": "This camera is not being processed."})
    worker = r.cv.workers[camera_id]

    async def gen():
        last = -1
        while not await request.is_disconnected():
            if worker.latest_jpeg_seq != last and worker.latest_jpeg:
                last = worker.latest_jpeg_seq
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + worker.latest_jpeg + b"\r\n"
            await asyncio.sleep(0.1)
            if r.cv is None or r.cv.workers.get(camera_id) is not worker:
                break

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store"})


class ModeIn(BaseModel):
    mode: Literal["simulation", "recorded", "live"]
    camera_ids: list[str] = Field(default_factory=list, max_length=8)


@router.post("/mode")
async def set_mode(request: Request, body: ModeIn) -> dict[str, Any]:
    r = rt(request)
    try:
        if body.mode == "simulation":
            out = r.back_to_simulation()
        else:
            out = r.start_cv(body.mode, body.camera_ids)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "mode": r.mode, "detail": out}


class VideoControlIn(BaseModel):
    action: Literal["play", "pause"]


@router.post("/video/control")
async def video_control(request: Request, body: VideoControlIn) -> dict[str, Any]:
    try:
        rt(request).cv_control(body.action)
    except ActionError as exc:
        raise conflict(exc) from exc
    await request.app.state.flush(snapshot=True)
    return {"ok": True}


class ZonesIn(BaseModel):
    anchor: Literal["bottom_center", "centroid"]
    zones: list[dict[str, Any]] = Field(max_length=64)


def _validate(r, camera_id: str, body: ZonesIn):
    known = [c["id"] for c in r.cameras()]
    return validate_zones(body.zones, camera_id, known, [t.id for t in r.layout.tables])


@router.get("/zones/{camera_id}")
def get_zones(request: Request, camera_id: str) -> dict[str, Any]:
    r = rt(request)
    if r.camera(camera_id) is None:
        raise HTTPException(404, detail={"code": "not_found", "message": "Unknown camera."})
    anchor, zones, version = r.zones_for(camera_id)
    return {"camera_id": camera_id, "anchor": anchor, "version": version, "anchor_modes": list(ANCHOR_MODES),
            "zones": [{"table_id": z.table_id, "polygon": [list(p) for p in z.polygon]} for z in zones]}


@router.post("/zones/{camera_id}/validate")
def check_zones(request: Request, camera_id: str, body: ZonesIn) -> dict[str, Any]:
    res = _validate(rt(request), camera_id, body)
    return {"ok": res.ok, "errors": res.errors, "warnings": res.warnings}


@router.put("/zones/{camera_id}")
async def save_zones(request: Request, camera_id: str, body: ZonesIn) -> dict[str, Any]:
    r = rt(request)
    res = _validate(r, camera_id, body)
    if not res.ok:
        raise bad(res.errors, "invalid_zones")
    clean = [{"table_id": str(z["table_id"]).strip(), "polygon": [[round(float(x), 5), round(float(y), 5)] for x, y in z["polygon"]]}
             for z in body.zones]
    version = r.set_zones(camera_id, body.anchor, clean)
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "version": version, "warnings": res.warnings}


@router.post("/zones/{camera_id}/reset-demo")
async def reset_demo_zones(request: Request, camera_id: str) -> dict[str, Any]:
    r = rt(request)
    if camera_id != SIM_CAMERA_ID:
        raise bad("Demo zones exist only for the simulation camera.")
    anchor, zones = r.demo_zones()
    version = r.set_zones(camera_id, anchor, [{"table_id": z.table_id, "polygon": [list(p) for p in z.polygon]} for z in zones])
    await request.app.state.flush(snapshot=True)
    return {"ok": True, "version": version}


@router.post("/zones/{camera_id}/test")
def test_zones(request: Request, camera_id: str, body: ZonesIn) -> dict[str, Any]:
    """Associate the camera's current tracks with the proposed (unsaved) zones."""
    r = rt(request)
    res = _validate(r, camera_id, body)
    if not res.ok:
        raise bad(res.errors, "invalid_zones")
    payload = r.frame_payload() or {"frames": []}
    frame = next((f for f in payload["frames"] if f["camera_id"] == camera_id), None)
    if frame is None:
        raise HTTPException(409, detail={"code": "no_frame", "message": "This camera is not producing tracks right now."})
    from ..core.models import Source, TrackedPerson, TrackFrame

    zones = [Zone.from_dict(z, camera_id) for z in body.zones]
    tf = TrackFrame(camera_id, frame["t"], tuple(TrackedPerson(t["id"], tuple(t["bbox"]), t["conf"]) for t in frame["tracks"]),
                    Source.SIMULATOR if frame["simulated"] else Source.COMPUTER_VISION)
    assoc = ZoneAssigner(zones, body.anchor, r.thresholds.ambiguity_margin).associate(tf)
    return {"t": frame["t"], "tracks": len(frame["tracks"]),
            "per_table": {k: [z.track_id for z in v] for k, v in assoc.per_table.items()},
            "unassigned": assoc.unassigned, "ambiguous": assoc.ambiguous}


@router.get("/vision/status")
def vision_status(request: Request) -> dict[str, Any]:
    from ..vision.detector import model_status

    r = rt(request)
    return {"model": model_status(), "running": r.mode in ("recorded", "live"),
            "workers": {cid: w.metrics.to_dict() for cid, w in (r.cv.workers.items() if r.cv else [])}}


# ---------------------------------------------------------------------------
# analytics and export
# ---------------------------------------------------------------------------
def _analytics_inputs(r, dataset: str, scope: str, run_id: str | None):
    runs = r.store.runs(dataset, limit=5000)
    if run_id:
        runs = [x for x in runs if x["id"] == run_id]
    elif scope == "current":
        runs = [x for x in runs if x["id"] == r.run_id]
    elif scope == "history":
        runs = [x for x in runs if x["kind"] == "history"]
    ids = [x["id"] for x in runs]
    sessions = r.store.sessions(dataset, ids)
    alerts = [a for a in r.store.alerts(dataset, limit=100000) if a["run_id"] in set(ids)]
    events = []
    if ids:
        marks = ",".join("?" * len(ids))
        events = r.store.q(
            f"SELECT run_id, type, t, wall, camera_id, table_id, payload_json FROM events WHERE dataset=? AND run_id IN ({marks}) "
            "AND type IN ('CAMERA_OFFLINE','CAMERA_ONLINE','STATUS_CORRECTED','POSSIBLE_STAFF_VISIT') ORDER BY seq",
            [dataset, *ids],
        )
        import json as _json

        for e in events:
            e["payload"] = _json.loads(e.pop("payload_json"))
    return runs, sessions, alerts, events


@router.get("/analytics")
def analytics(request: Request, dataset: Dataset | None = None,
              scope: Literal["current", "all", "history"] = "current", run_id: str | None = None,
              tables_per_waiter: float = Query(4.0, ge=0.5, le=20)) -> dict[str, Any]:
    r = rt(request)
    ds = dataset or r.dataset
    if scope == "current" and ds != r.dataset:
        scope = "all"
    runs, sessions, alerts, events = _analytics_inputs(r, ds, scope, run_id)
    result = compute_analytics(ds, runs, sessions, alerts, events, [t.id for t in r.layout.tables], tables_per_waiter)
    result["scope"] = scope
    return result


EXPORTS = {
    "events": ["seq", "dataset", "run_id", "ts", "t", "type", "table_id", "camera_id", "session_id", "source",
               "from_state", "to_state", "payload"],
    "sessions": ["session_id", "dataset", "run_id", "table_id", "status", "seated_ts", "service_ts", "service_source",
                 "wait_s", "left_ts", "occupied_s", "cleaned_ts", "cleaning_s", "turnover_s", "visits", "corrected",
                 "unserved_alert", "cleaning_overdue", "departed_during_gap"],
    "alerts": ["id", "dataset", "run_id", "type", "severity", "status", "table_id", "camera_id", "session_id",
               "created_ts", "acknowledged_by", "resolution", "message"],
}


def _ts(v: float | None) -> str:
    return datetime.fromtimestamp(v).isoformat(timespec="seconds") if v else ""


@router.get("/export/{kind}.csv")
def export_csv(request: Request, kind: Literal["events", "sessions", "alerts"], dataset: Dataset | None = None,
               run_id: str | None = None) -> Response:
    import json as _json

    r = rt(request)
    ds = dataset or r.dataset
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# TableWatch export", f"dataset={ds}", "SIMULATED DATA" if ds == "simulation" else "observed data",
                f"exported={datetime.now().isoformat(timespec='seconds')}"])
    w.writerow(EXPORTS[kind])
    if kind == "events":
        for e in reversed(r.store.events(ds, run_id, limit=200000)):
            w.writerow([e["seq"], e["dataset"], e["run_id"], _ts(e["wall"]), round(e["t"], 2), e["type"], e["table_id"],
                        e["camera_id"], e["session_id"], e["source"], e["from_state"], e["to_state"], _json.dumps(e["payload"])])
    elif kind == "sessions":
        rows = r.store.sessions(ds, [run_id] if run_id else None)
        for s in rows:
            w.writerow([s["session_id"], s["dataset"], s["run_id"], s["table_id"], s["status"], _ts(s["seated_wall"]),
                        _ts(s["service_wall"]), s["service_source"], s["wait_s"], _ts(s["left_wall"]), s["occupied_s"],
                        _ts(s["cleaned_wall"]), s["cleaning_s"], s["turnover_s"], s["visits"], s["corrected"],
                        s["unserved_alert"], s["cleaning_overdue"], s["departed_during_gap"]])
    else:
        for a in r.store.alerts(ds, run_id, limit=200000):
            w.writerow([a["id"], a["dataset"], a["run_id"], a["type"], a["severity"], a["status"], a["table_id"],
                        a["camera_id"], a["session_id"], _ts(a["created_wall"]), a["acknowledged_by"], a["resolution"],
                        a["message"]])
    name = f"tablewatch-{ds}-{kind}-{datetime.now():%Y%m%d-%H%M%S}.csv"
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# data management (privacy)
# ---------------------------------------------------------------------------
@router.get("/data/summary")
def data_summary(request: Request) -> dict[str, Any]:
    r = rt(request)
    return {"counts": r.store.counts(), "database": "data/tablewatch.db (local file)", "video_stored": False,
            "privacy": r.settings.privacy()}


class DeleteIn(BaseModel):
    dataset: Dataset
    run_id: str | None = None
    confirm: str


@router.post("/data/delete")
async def delete_data(request: Request, body: DeleteIn) -> dict[str, Any]:
    r = rt(request)
    if body.confirm != f"DELETE {body.dataset}":
        raise bad(f"Type 'DELETE {body.dataset}' to confirm.", "confirmation_required")
    if body.dataset == r.dataset and (body.run_id is None or body.run_id == r.run_id):
        raise HTTPException(409, detail={"code": "in_use", "message": "Switch away from this dataset (or reset the simulation) before deleting its active run."})
    n = r.store.delete_dataset(body.dataset, body.run_id)
    return {"ok": True, "deleted_events": n}


# ---------------------------------------------------------------------------
# websocket
# ---------------------------------------------------------------------------
@ws_router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    app = ws.app
    origin = ws.headers.get("origin")
    if origin is not None and origin not in app.state.allowed_origins:
        await ws.close(code=1008)
        return
    hub = app.state.hub
    await hub.connect(ws)
    try:
        r = app.state.runtime
        await hub.send(ws, {"type": "hello", "server_time": time.time(), "mode": r.mode})
        await hub.send(ws, r.snapshot())
        frame = r.frame_payload()
        if frame:
            await hub.send(ws, frame)
        while True:
            await ws.receive_text()  # clients only listen; reading keeps the socket healthy
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.disconnect(ws)
