"""The running application: one active session plus the glue around it.

Modes:
* ``simulation`` – the deterministic simulator drives the engine.
* ``recorded``   – a local video file runs through the real detector.
* ``live``       – explicitly configured webcams / RTSP cameras.

Exactly one session is active. Each session writes to its own dataset and
run ID, so simulated and real data never mix.
"""

from __future__ import annotations

import logging
import queue
import time
import uuid
from datetime import datetime
from typing import Any, Callable

import yaml

from ..config.layout import Layout, load_zone_file, project_root
from ..core.clock import RealClock
from ..core.engine import RestaurantEngine
from ..core.models import (
    ActionError,
    ActionType,
    Event,
    EventType,
    OperatorAction,
    Source,
    TableState,
    TrackFrame,
)
from ..core.thresholds import Thresholds
from ..core.zones import Zone
from ..simulation.scenarios import Scenario
from ..simulation.session import SIM_CAMERA_ID, SimSession
from ..storage.db import Store
from ..vision.sources import redact
from .settings import SettingsService

log = logging.getLogger("tablewatch.runtime")

GUIDE_STEPS = [
    ("enter", "Customers enter the restaurant"),
    ("seated", "Tables become occupied"),
    ("waiting", "A table waits without confirmed service"),
    ("alert", "The waiting alert appears"),
    ("served", "Service is confirmed"),
    ("leave", "Another party leaves"),
    ("cleaning", "The table needs cleaning"),
    ("cleaned", "Cleaning is confirmed and the table is free"),
    ("analytics", "Analytics update from the event history"),
]


def iso(wall: float | None) -> str | None:
    return datetime.fromtimestamp(wall).isoformat(timespec="seconds") if wall is not None else None


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


class VideoClock:
    """Domain time = position in the video. Wall time = run start + position."""

    def __init__(self) -> None:
        self._t = 0.0
        self._wall0 = time.time()

    def observe(self, t: float) -> None:
        self._t = max(self._t, t)

    def now(self) -> float:
        return self._t

    def wall(self, t: float | None = None) -> float:
        return self._wall0 + (self._t if t is None else t)


class CvSession:
    """Recorded-video or live-camera session using the real detection pipeline."""

    def __init__(self, dataset: str, run_id: str, layout: Layout, thresholds: Thresholds,
                 cameras: list[dict[str, Any]], store: Store, detector_factory: Callable[[], Any],
                 realtime: bool = True) -> None:
        from ..vision.pipeline import CameraWorker

        self.dataset = dataset
        self.run_id = run_id
        self.clock: Any = VideoClock() if dataset == "recorded" else RealClock()
        self.engine = RestaurantEngine(layout.table_infos(), thresholds, wall=self.clock.wall, session_prefix=f"{run_id}:")
        self.queue: "queue.Queue[TrackFrame]" = queue.Queue(maxsize=64)
        self.workers: dict[str, CameraWorker] = {}
        self.cameras = cameras
        for cam in cameras:
            self.engine.register_camera(cam["id"], cam["zones"], cam["anchor"], Source.COMPUTER_VISION, t=self.clock.now())
            cfg = cam.get("config", {})
            self.workers[cam["id"]] = CameraWorker(
                cam, detector_factory, self.queue, self.clock.now,
                sample_fps=float(cfg.get("sample_fps", 4)), realtime=realtime,
                tables_count=len(cam["zones"]),
                reconnect_min_s=float(cfg.get("reconnect_min_s", 1)),
                reconnect_max_s=float(cfg.get("reconnect_max_s", 30)),
            )
        self.engine.subscribe(lambda ev: store.append_event(ev, dataset, run_id))

    def start(self) -> None:
        for w in self.workers.values():
            w.start()

    def stop(self) -> None:
        for w in self.workers.values():
            w.stop()

    def now(self) -> float:
        return self.clock.now()

    def pump(self) -> int:
        n = 0
        while True:
            try:
                frame = self.queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(self.clock, VideoClock):
                self.clock.observe(frame.t)
            self.engine.ingest(frame)
            n += 1
        if isinstance(self.clock, RealClock) or n:
            self.engine.tick(self.clock.now())
        return n

    @property
    def finished(self) -> bool:
        return self.dataset == "recorded" and all(w.metrics.status in ("ended", "error", "stopped") for w in self.workers.values()) and self.queue.empty()


class Runtime:
    def __init__(self, store: Store, layout: Layout, scenarios: dict[str, Scenario], settings: SettingsService,
                 detector_factory: Callable[[], Any] | None = None) -> None:
        self.store = store
        self.layout = layout
        self.scenarios = scenarios
        self.settings = settings
        self.detector_factory = detector_factory
        self.thresholds, self.thresholds_version = settings.thresholds()
        self.mode = "simulation"
        self.sim: SimSession | None = None
        self.cv: CvSession | None = None
        self.outbox: list[dict[str, Any]] = []
        self.frame_seq = 0
        self.guide: dict[str, bool] = {}
        self.last_error: str | None = None
        self._sim_frames: list[float] = []
        store.ensure_restaurant(layout.id, layout.name)
        self.sim_camera = self._load_sim_camera()

    # ------------------------------------------------------------------
    # cameras and zones
    # ------------------------------------------------------------------
    def _load_sim_camera(self) -> dict[str, Any]:
        path = project_root() / "configs" / "cameras.yaml"
        cams = (yaml.safe_load(path.read_text()) or {}).get("cameras", []) if path.exists() else []
        sim = next((c for c in cams if c.get("kind") == "simulation"), None) or {
            "id": SIM_CAMERA_ID, "name": "Synthetic overhead camera (SIMULATED)", "kind": "simulation",
            "anchor": "centroid", "zones_file": "configs/zones/demo.json",
        }
        return sim

    def demo_zones(self) -> tuple[str, list[Zone]]:
        _, anchor, zones = load_zone_file(project_root() / self.sim_camera["zones_file"], SIM_CAMERA_ID)
        return anchor, zones

    def zones_for(self, camera_id: str) -> tuple[str, list[Zone], int]:
        anchor, raw, version = self.store.zones(camera_id)
        if raw:
            return anchor or "bottom_center", [Zone.from_dict(z, camera_id) for z in raw], version
        if camera_id == SIM_CAMERA_ID:
            a, zones = self.demo_zones()
            return a, zones, 0
        cam = self.camera(camera_id)
        if cam and cam["config"].get("zones_file"):
            path = (project_root() / cam["config"]["zones_file"]).resolve()
            if project_root() in path.parents and path.exists():
                _, a, zones = load_zone_file(path, camera_id)
                return a, zones, 0
        anchor = (cam or {}).get("config", {}).get("anchor", "bottom_center")
        return anchor, [], 0

    def cameras(self) -> list[dict[str, Any]]:
        sim = {"id": SIM_CAMERA_ID, "name": self.sim_camera.get("name"), "kind": "simulation", "enabled": True,
               "config": {"anchor": self.sim_camera.get("anchor", "centroid")}, "builtin": True}
        out = [sim]
        for c in self.store.cameras():
            cfg = dict(c["config"])
            if "url" in cfg:
                cfg["url"] = redact(cfg["url"])
            out.append({**c, "config": cfg, "builtin": False})
        return out

    def camera(self, camera_id: str, raw: bool = False) -> dict[str, Any] | None:
        if camera_id == SIM_CAMERA_ID:
            return {"id": SIM_CAMERA_ID, "kind": "simulation", "config": {"anchor": self.sim_camera.get("anchor", "centroid")}}
        for c in self.store.cameras():
            if c["id"] == camera_id:
                return c
        return None

    def set_zones(self, camera_id: str, anchor: str, zones: list[dict[str, Any]]) -> int:
        version = self.store.save_zones(camera_id, anchor, zones)
        parsed = [Zone.from_dict(z, camera_id) for z in zones]
        engine = self.engine
        if engine is not None and camera_id in engine.cameras:
            src = engine.cameras[camera_id].source
            engine.register_camera(camera_id, parsed, anchor, src, t=self.now())
        return version

    # ------------------------------------------------------------------
    # session properties
    # ------------------------------------------------------------------
    @property
    def engine(self) -> RestaurantEngine | None:
        if self.mode == "simulation":
            return self.sim.engine if self.sim else None
        return self.cv.engine if self.cv else None

    @property
    def dataset(self) -> str:
        return {"simulation": "simulation", "recorded": "recorded", "live": "live"}[self.mode]

    @property
    def run_id(self) -> str | None:
        if self.mode == "simulation":
            return self.sim.run_id if self.sim else None
        return self.cv.run_id if self.cv else None

    def now(self) -> float:
        if self.mode == "simulation":
            return self.sim.t if self.sim else 0.0
        return self.cv.now() if self.cv else 0.0

    def wall(self, t: float | None = None) -> float:
        e = self.engine
        return e.wall(self.now() if t is None else t) if e else time.time()

    def _on_event(self, ev: Event) -> None:
        d = ev.to_dict()
        d["ts"] = iso(ev.wall)
        d["dataset"] = self.dataset
        d["run_id"] = self.run_id
        self.outbox.append({"type": "event", "event": d})
        g = self.guide
        if ev.type == EventType.GUESTS_SEATED:
            g["seated"] = True
        elif ev.type == EventType.ALERT_RAISED and ev.payload["alert"]["type"] == "UNSERVED_TOO_LONG":
            g["waiting"] = g["alert"] = True
            g.setdefault("alert_sessions", set()).add(ev.session_id)  # type: ignore[union-attr]
        elif ev.type == EventType.SERVICE_CONFIRMED and ev.session_id in g.get("alert_sessions", set()):  # type: ignore[operator]
            g["served"] = True
        elif ev.type == EventType.GUESTS_DEPARTED:
            g["cleaning"] = True
        elif ev.type == EventType.CLEANING_CONFIRMED:
            g["cleaned"] = g["analytics"] = True

    # ------------------------------------------------------------------
    # simulation control
    # ------------------------------------------------------------------
    def start_simulation(self, scenario_id: str | None = None, seed: int | None = None, autoplay: bool | None = None,
                         speed: float | None = None) -> dict[str, Any]:
        sim_settings = self.settings.simulation()
        scenario_id = scenario_id or (self.sim.scenario.id if self.sim else sim_settings["default_scenario"])
        if scenario_id not in self.scenarios:
            raise ValueError(f"Unknown scenario '{scenario_id}'")
        self.stop_cv()
        self._end_current_sim("reset")
        sc = self.scenarios[scenario_id]
        seed = seed if seed is not None else sim_settings.get("seed")
        run_id = new_run_id(f"sim-{sc.id}")
        anchor, zones, zone_version = self.zones_for(SIM_CAMERA_ID)
        self.store.create_run(
            run_id, "simulation", "scenario", epoch_wall=sc.start_time.timestamp(), label=sc.name, scenario=sc.id,
            seed=seed if seed is not None else sc.seed,
            meta={"thresholds_version": self.thresholds_version, "zones_version": zone_version,
                  "thresholds": self.thresholds.to_dict(), "synthetic": True},
        )
        self.sim = SimSession(self.layout, sc, self.thresholds, zones, anchor, run_id, seed=seed,
                              staff_auto_confirm=sim_settings.get("staff_auto_confirm"), store=self.store,
                              on_event=self._on_event)
        self.mode = "simulation"
        self.guide = {}
        self.sim.clock.set_speed(float(speed or sim_settings.get("speed", 5.0)))
        if autoplay if autoplay is not None else sim_settings.get("autoplay", True):
            self.sim.clock.resume()
        self.outbox.append({"type": "reset", "run_id": run_id})
        return self.sim_status()

    def _end_current_sim(self, status: str) -> None:
        if self.sim is not None:
            self.store.touch_run(self.sim.run_id, self.sim.t, self.sim.clock.wall(self.sim.t))
            self.store.end_run(self.sim.run_id, status)

    def sim_control(self, action: str, speed: float | None = None) -> dict[str, Any]:
        if self.mode != "simulation" or self.sim is None:
            raise ActionError("Simulation is not the active mode.", "wrong_mode")
        clock = self.sim.clock
        if action == "pause":
            clock.pause()
        elif action in ("play", "resume"):
            if self.sim.finished:
                raise ActionError("This scenario has finished. Restart it to play again.", "finished")
            clock.resume()
        elif action == "speed":
            if speed not in (0, 1, 5, 10):
                raise ActionError("Speed must be 0 (pause), 1, 5 or 10.", "bad_speed")
            clock.set_speed(float(speed))
            if speed and not self.sim.finished:
                clock.resume()
        else:
            raise ActionError(f"Unknown simulation action '{action}'.", "bad_request")
        return self.sim_status()

    def advance(self, seconds: float) -> dict[str, Any]:
        """Skip ahead in simulated time (runs every step; nothing is skipped)."""
        if self.mode != "simulation" or self.sim is None:
            raise ActionError("Simulation is not the active mode.", "wrong_mode")
        if not 0 < seconds <= 3600:
            raise ActionError("Advance between 1 and 3600 simulated seconds.", "bad_request")
        target = min(self.sim.t + seconds, self.sim.scenario.duration_s)
        with self.store.batch():
            self.sim.run_headless(until=target)
        self._after_sim_steps()
        return self.sim_status()

    def sim_status(self) -> dict[str, Any] | None:
        if self.sim is None:
            return None
        sc = self.sim.scenario
        return {
            "scenario": sc.to_dict(),
            "seed": self.sim.sim.seed,
            "speed": self.sim.clock.speed,
            "paused": self.sim.clock.paused,
            "finished": self.sim.finished,
            "t": self.sim.t,
            "duration_s": sc.duration_s,
            "progress": min(1.0, self.sim.t / sc.duration_s) if sc.duration_s else 0,
            "staff_auto_confirm": self.sim.sim.auto_confirm,
            "waiting_parties": self.sim.last.scene["waiting_parties"] if self.sim.last else 0,
            "simulated_actions": self.sim.sim_actions,
        }

    # ------------------------------------------------------------------
    # computer vision sessions
    # ------------------------------------------------------------------
    def start_cv(self, mode: str, camera_ids: list[str]) -> dict[str, Any]:
        if mode not in ("recorded", "live"):
            raise ActionError("Mode must be recorded or live.", "bad_request")
        if not camera_ids:
            raise ActionError("Choose at least one camera.", "bad_request")
        if self.detector_factory is None:
            raise ActionError("Video processing is not available in this installation.", "vision_unavailable")
        from ..vision.detector import model_status

        status = model_status()
        if not status["ready"]:
            missing = []
            if not (status["onnxruntime"] and status["opencv"]):
                missing.append("vision packages (./scripts/setup.sh --vision)")
            if not status["present"]:
                missing.append("model weights (./scripts/download-model.sh)")
            elif not status["verified"]:
                missing.append("a valid model file (checksum mismatch; re-run ./scripts/download-model.sh)")
            raise ActionError("Real video needs " + " and ".join(missing) + ".", "vision_unavailable")
        cams = []
        for cid in camera_ids:
            cam = self.camera(cid)
            if cam is None or cam["kind"] == "simulation":
                raise ActionError(f"Camera '{cid}' is not a configured video source.", "bad_camera")
            if mode == "recorded" and cam["kind"] != "file":
                raise ActionError("Recorded mode only accepts video-file cameras.", "bad_camera")
            if mode == "live" and cam["kind"] == "file":
                raise ActionError("Live mode needs webcam or RTSP cameras.", "bad_camera")
            if not cam.get("enabled", True):
                raise ActionError(f"Camera '{cid}' is disabled.", "bad_camera")
            anchor, zones, _ = self.zones_for(cid)
            if not zones:
                raise ActionError(f"Draw table zones for camera '{cid}' before analysing it.", "no_zones")
            cams.append({**cam, "zones": zones, "anchor": anchor})
        if mode == "recorded" and len(cams) != 1:
            raise ActionError("Analyse one recorded video at a time.", "bad_request")
        self.stop_cv()
        if self.sim is not None:
            self.sim.clock.pause()
        run_id = new_run_id(mode)
        self.store.create_run(run_id, mode, "camera" if mode == "live" else "video", epoch_wall=time.time(),
                              label=", ".join(c["name"] for c in cams),
                              meta={"cameras": [c["id"] for c in cams], "thresholds": self.thresholds.to_dict(),
                                    "synthetic": False})
        self.cv = CvSession(mode, run_id, self.layout, self.thresholds, cams, self.store, self.detector_factory)
        self.cv.engine.subscribe(self._on_event)
        self.mode = mode
        self.cv.start()
        self.outbox.append({"type": "reset", "run_id": run_id})
        return {"mode": mode, "run_id": run_id}

    def stop_cv(self) -> None:
        if self.cv is not None:
            self.cv.stop()
            self.store.touch_run(self.cv.run_id, self.cv.now(), self.cv.clock.wall(self.cv.now()))
            self.store.end_run(self.cv.run_id, "stopped")
            self.cv = None

    def cv_control(self, action: str) -> None:
        if self.cv is None:
            raise ActionError("No video session is running.", "wrong_mode")
        for w in self.cv.workers.values():
            if action == "pause":
                w.pause()
            elif action in ("play", "resume"):
                w.resume()
            else:
                raise ActionError(f"Unknown action '{action}'.", "bad_request")

    def back_to_simulation(self) -> dict[str, Any]:
        self.stop_cv()
        self.mode = "simulation"
        if self.sim is None:
            return self.start_simulation()
        self.outbox.append({"type": "reset", "run_id": self.sim.run_id})
        return self.sim_status() or {}

    # ------------------------------------------------------------------
    # thresholds
    # ------------------------------------------------------------------
    def apply_thresholds(self, th: Thresholds, version: int) -> None:
        self.thresholds, self.thresholds_version = th, version
        for eng in (self.sim.engine if self.sim else None, self.cv.engine if self.cv else None):
            if eng is not None:
                eng.set_thresholds(th)

    # ------------------------------------------------------------------
    # operator actions
    # ------------------------------------------------------------------
    def act(self, action_type: ActionType, table_id: str | None = None, alert_id: str | None = None,
            to_state: str | None = None, reason: str | None = None, actor: str = "operator",
            request_id: str | None = None) -> list[dict[str, Any]]:
        engine = self.engine
        if engine is None:
            raise ActionError("No active session.", "wrong_mode")
        t = self.now()
        try:
            state = TableState(to_state) if to_state else None
        except ValueError:
            raise ActionError(f"Unknown state '{to_state}'.", "bad_request") from None
        record = {"request_id": request_id, "action": action_type.value, "table_id": table_id, "alert_id": alert_id,
                  "source": Source.OPERATOR.value, "actor": actor, "reason": reason, "t": t, "wall": self.wall(t)}
        try:
            with self.store.batch():
                events = engine.act(OperatorAction(action_type, t, Source.OPERATOR, table_id, alert_id, state, reason,
                                                   actor, request_id))
                self.store.log_action(self.dataset, self.run_id or "", {**record, "result": "ok"})
        except ActionError as exc:
            self.store.log_action(self.dataset, self.run_id or "", {**record, "request_id": None, "result": "rejected",
                                                                    "error": str(exc)})
            raise
        return [{**e.to_dict(), "ts": iso(e.wall)} for e in events]

    # ------------------------------------------------------------------
    # loop
    # ------------------------------------------------------------------
    def tick(self, real_dt: float) -> bool:
        """Advance whatever session is active. Returns True if anything moved."""
        moved = False
        try:
            if self.mode == "simulation" and self.sim is not None:
                self.sim.clock.advance(real_dt)
                if self.sim.sim.t + 1e-9 <= self.sim.clock.now():
                    with self.store.batch():
                        n = 0
                        while self.sim.sim.t + 1e-9 <= self.sim.clock.now() and not self.sim.finished and n < 400:
                            self.sim.step()
                            n += 1
                            self._sim_frames.append(time.monotonic())
                    if n:
                        moved = True
                        self._after_sim_steps()
                if self.sim.finished and not self.sim.clock.paused:
                    self.sim.clock.pause()
                    self.store.end_run(self.sim.run_id, "completed")
                    moved = True
            elif self.cv is not None:
                n = self.cv.pump()
                if n:
                    moved = True
                    self.store.touch_run(self.cv.run_id, self.cv.now(), self.cv.clock.wall())
                if self.cv.finished:
                    self.store.end_run(self.cv.run_id, "completed")
        except Exception as exc:  # keep the loop alive, show the problem
            self.last_error = redact(f"{type(exc).__name__}: {exc}")
            log.exception("runtime tick failed")
        return moved

    def _after_sim_steps(self) -> None:
        assert self.sim is not None
        if self.sim.last and any(p["role"] == "guest" for p in self.sim.last.scene["people"]):
            self.guide["enter"] = True
        if self.sim.last and any(p["role"] == "guest" and p["activity"] == "leaving" for p in self.sim.last.scene["people"]):
            self.guide["leave"] = True
        self.store.touch_run(self.sim.run_id, self.sim.t, self.sim.clock.wall(self.sim.t))
        self.frame_seq += 1

    # ------------------------------------------------------------------
    # read models
    # ------------------------------------------------------------------
    def frame_payload(self) -> dict[str, Any] | None:
        engine = self.engine
        if engine is None:
            return None
        frames = []
        if self.mode == "simulation" and self.sim and self.sim.last:
            st = self.sim.last
            frames.append(self._frame(SIM_CAMERA_ID, st.t, True, [
                {"id": tr.track_id, "bbox": [round(v, 4) for v in tr.bbox], "conf": tr.confidence}
                for tr in (st.frame.tracks if st.frame else ())
            ], people=st.scene["people"], outage=st.scene["camera_outage"]))
        elif self.cv:
            for cid, w in self.cv.workers.items():
                frames.append(self._frame(cid, w.metrics.last_frame_t or 0.0, False, w.latest_tracks,
                                          jpeg_seq=w.latest_jpeg_seq, size=list(w.latest_size)))
        return {"type": "frames", "seq": self.frame_seq, "frames": frames}

    def _frame(self, cid: str, t: float, simulated: bool, tracks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        engine = self.engine
        assoc = engine.cameras[cid].last_association if engine and cid in engine.cameras else {}
        table_of = {tid: table for table, ids in assoc.get("per_table", {}).items() for tid in ids}
        ambiguous = set(assoc.get("ambiguous", []))
        for tr in tracks:
            tr["table"] = None if tr["id"] in ambiguous else table_of.get(tr["id"])
            tr["ambiguous"] = tr["id"] in ambiguous
        return {"camera_id": cid, "t": t, "simulated": simulated, "tracks": tracks, **extra}

    def camera_health(self) -> list[dict[str, Any]]:
        engine = self.engine
        if engine is None:
            return []
        out = []
        now = time.monotonic()
        self._sim_frames = [x for x in self._sim_frames if now - x < 3.0]
        for snap in engine.snapshot(self.now())["cameras"]:
            cid = snap["camera_id"]
            item = {**snap, "kind": "simulation" if cid == SIM_CAMERA_ID else (self.camera(cid) or {}).get("kind")}
            if self.mode == "simulation":
                fps = len(self._sim_frames) / 3.0 if self._sim_frames else 0.0
                item.update({
                    "status": "paused" if self.sim and self.sim.clock.paused else "running",
                    "processing_fps": round(fps, 2),
                    "observations_per_s": round(fps * len(engine.cameras[cid].assigner.zones), 2),
                    "detector": "None: synthetic tracks generated by the simulator",
                    "last_frame_wall": self.sim.clock.wall(snap["last_frame_t"]) if self.sim and snap["last_frame_t"] is not None else None,
                })
            elif self.cv and cid in self.cv.workers:
                m = self.cv.workers[cid].metrics.to_dict()
                item.update(m)
            out.append(item)
        return out

    def snapshot(self) -> dict[str, Any]:
        engine = self.engine
        t = self.now()
        snap = engine.snapshot(t) if engine else {"tables": [], "summary": {}, "alerts": [], "cameras": []}
        wall = self.wall(t)
        run = self.store.run(self.run_id) if self.run_id else None
        clock = {
            "t": t,
            "wall": wall,
            "iso": iso(wall),
            "simulated": self.mode == "simulation",
            "speed": self.sim.clock.speed if self.mode == "simulation" and self.sim else 1.0,
            "paused": (self.sim.clock.paused if self.mode == "simulation" and self.sim else
                       bool(self.cv and all(w.metrics.status == "paused" for w in self.cv.workers.values()))),
        }
        guide = None
        if self.mode == "simulation":
            g = dict(self.guide)
            if engine and not g.get("waiting"):
                g["waiting"] = any(
                    tv["state"] == "SEATED_UNSERVED" and (tv["wait_s"] or 0) >= 0.6 * self.thresholds.unserved_warning_s
                    for tv in snap["tables"]
                )
                self.guide["waiting"] = g["waiting"]
            guide = [{"id": sid, "title": title, "done": bool(g.get(sid))} for sid, title in GUIDE_STEPS]
        return {
            "type": "snapshot",
            "mode": self.mode,
            "dataset": self.dataset,
            "run": {"id": run["id"], "label": run["label"], "scenario": run["scenario"], "seed": run["seed"],
                    "status": run["status"]} if run else None,
            "restaurant": {"id": self.layout.id, "name": self.layout.name},
            "clock": clock,
            "sim": self.sim_status() if self.mode == "simulation" else None,
            "thresholds": self.thresholds.to_dict(),
            "tables": snap["tables"],
            "summary": snap["summary"],
            "alerts": [{**a, "created_ts": iso(self.wall(a["created_t"]))} for a in snap["alerts"]],
            "cameras": self.camera_health(),
            "guide": guide,
            "error": self.last_error,
        }
