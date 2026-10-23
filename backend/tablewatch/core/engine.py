"""Restaurant engine: one place where observations, timers and operator
actions meet the table state machines.

Simulation, recorded video and live cameras all feed the same engine
through ``ingest(TrackFrame)``. The engine never reads a clock; callers pass
domain time in. Every change is emitted as an ``Event`` to subscribers
(persistence, websocket broadcast).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .alerts import NEXT_STEP, AlertBook
from .models import (
    OCCUPIED_STATES,
    ActionError,
    ActionType,
    Alert,
    AlertStatus,
    AlertType,
    Event,
    EventType,
    ObservationHealth,
    OperatorAction,
    Source,
    TableState,
    TrackFrame,
)
from .state_machine import TableStateMachine
from .thresholds import Thresholds
from .zones import Zone, ZoneAssigner


@dataclass
class TableInfo:
    table_id: str
    label: str
    seats: int = 4
    area: str = "Main"


@dataclass
class CameraState:
    camera_id: str
    assigner: ZoneAssigner
    source: Source
    registered_t: float
    last_frame_t: float | None = None
    offline: bool = False
    offline_since: float | None = None
    frames: int = 0
    observations: int = 0
    outages: int = 0
    last_association: dict[str, Any] = field(default_factory=dict)


class RestaurantEngine:
    def __init__(
        self,
        tables: Iterable[TableInfo],
        thresholds: Thresholds | None = None,
        wall: Callable[[float], float] | None = None,
        session_prefix: str = "",
    ) -> None:
        self.thresholds = thresholds or Thresholds()
        self.wall = wall or (lambda t: t)
        self.session_prefix = session_prefix
        self.info: dict[str, TableInfo] = {}
        self.tables: dict[str, TableStateMachine] = {}
        for info in tables:
            self.info[info.table_id] = info
            self.tables[info.table_id] = TableStateMachine(info.table_id, None, session_prefix)
        self.cameras: dict[str, CameraState] = {}
        self.alerts = AlertBook(id_prefix=session_prefix)
        self._subscribers: list[Callable[[Event], None]] = []
        self._seq = itertools.count(1)
        self._requests: dict[str, list[Event]] = {}
        self.last_t = 0.0

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def set_thresholds(self, th: Thresholds) -> None:
        self.thresholds = th
        for cam in self.cameras.values():
            cam.assigner.margin = th.ambiguity_margin

    def register_camera(
        self, camera_id: str, zones: Iterable[Zone], anchor: str, source: Source, t: float = 0.0
    ) -> None:
        zones = [z for z in zones if z.table_id in self.tables]
        assigner = ZoneAssigner(zones, anchor=anchor, ambiguity_margin=self.thresholds.ambiguity_margin)
        existing = self.cameras.get(camera_id)
        if existing:
            existing.assigner = assigner
        else:
            self.cameras[camera_id] = CameraState(camera_id, assigner, source, registered_t=t)
        for tid, sm in self.tables.items():
            if sm.proj.camera_id == camera_id and tid not in assigner.table_ids:
                sm.proj.camera_id = None
        for z in zones:
            self.tables[z.table_id].proj.camera_id = camera_id

    def remove_camera(self, camera_id: str) -> None:
        self.cameras.pop(camera_id, None)
        for sm in self.tables.values():
            if sm.proj.camera_id == camera_id:
                sm.proj.camera_id = None

    def _emit(self, events: list[Event]) -> list[Event]:
        out: list[Event] = []
        for ev in events:
            ev.wall = self.wall(ev.t)
            ev.seq = next(self._seq)
            if ev.key is None:
                ev.key = f"{ev.type.value}:{ev.id}"
            out.append(ev)
            for fn in self._subscribers:
                fn(ev)
            out.extend(self._consequences(ev))
        return out

    # ------------------------------------------------------------------
    # alerts as consequences of events
    # ------------------------------------------------------------------
    def _raise(self, type_: AlertType, key: str, t: float, msg: str, **kw: Any) -> list[Event]:
        alert = self.alerts.raise_alert(type_, key, t, msg, **kw)
        if alert is None:
            return []
        ev = Event(
            type=EventType.ALERT_RAISED,
            t=t,
            source=Source.SYSTEM,
            table_id=alert.table_id,
            camera_id=alert.camera_id,
            session_id=alert.session_id,
            payload={"alert": alert.to_dict(), "next_step": NEXT_STEP[type_]},
            key=f"alert:{key}",
        )
        return self._emit([ev])

    def _resolve(self, key: str, t: float, resolution: str) -> list[Event]:
        alert = self.alerts.resolve(key, t, resolution)
        if alert is None:
            return []
        ev = Event(
            type=EventType.ALERT_RESOLVED,
            t=t,
            source=Source.SYSTEM,
            table_id=alert.table_id,
            camera_id=alert.camera_id,
            session_id=alert.session_id,
            payload={"alert_id": alert.id, "alert_type": alert.type.value, "resolution": resolution},
            key=f"resolve:{alert.id}",
        )
        return self._emit([ev])

    def _consequences(self, ev: Event) -> list[Event]:
        t, s, tid = ev.t, ev.session_id, ev.table_id
        out: list[Event] = []
        label = self.info[tid].label if tid in self.info else tid
        if ev.type == EventType.SERVICE_CONFIRMED:
            out += self._resolve(f"UNSERVED:{s}", t, "service_confirmed")
        elif ev.type == EventType.GUESTS_DEPARTED:
            out += self._resolve(f"UNSERVED:{s}", t, "guests_departed")
            out += self._raise(
                AlertType.TABLE_NEEDS_CLEANING, f"CLEAN:{s}", t, f"{label} needs cleaning.", table_id=tid,
                camera_id=ev.camera_id, session_id=s,
            )
        elif ev.type == EventType.CLEANING_CONFIRMED:
            out += self._resolve(f"CLEAN:{s}", t, "cleaning_confirmed")
            out += self._resolve(f"OVERDUE:{s}", t, "cleaning_confirmed")
        elif ev.type == EventType.STATUS_CORRECTED:
            prev = ev.payload.get("previous_session_id")
            if ev.to_state == TableState.FREE:
                for k in ("UNSERVED", "CLEAN", "OVERDUE"):
                    out += self._resolve(f"{k}:{prev}", t, "status_corrected")
            elif ev.to_state == TableState.NEEDS_CLEANING:
                out += self._resolve(f"UNSERVED:{prev}", t, "status_corrected")
                out += self._raise(
                    AlertType.TABLE_NEEDS_CLEANING, f"CLEAN:{s}", t, f"{label} needs cleaning.", table_id=tid,
                    camera_id=ev.camera_id, session_id=s,
                )
            elif ev.from_state == TableState.NEEDS_CLEANING:
                out += self._resolve(f"CLEAN:{prev}", t, "status_corrected")
                out += self._resolve(f"OVERDUE:{prev}", t, "status_corrected")
        elif ev.type == EventType.LOW_CONFIDENCE_STARTED:
            reasons = ", ".join(r.replace("_", " ") for r in ev.payload.get("reasons", [])) or "uncertain evidence"
            out += self._raise(
                AlertType.LOW_CONFIDENCE_OBSERVATION, f"LOWQ:{tid}:{round(ev.payload['since_t'], 2)}", t,
                f"{label}: observation is uncertain ({reasons}).", table_id=tid, camera_id=ev.camera_id,
                session_id=s,
            )
        elif ev.type == EventType.LOW_CONFIDENCE_ENDED:
            out += self._resolve(f"LOWQ:{tid}:{round(ev.payload['since_t'], 2)}", t, "observation_recovered")
        elif ev.type == EventType.OBSERVATION_VERIFIED:
            for a in self.alerts.active():
                if a.type == AlertType.LOW_CONFIDENCE_OBSERVATION and a.table_id == tid:
                    out += self._resolve(a.dedup_key, t, "operator_verified")
        elif ev.type == EventType.CAMERA_OFFLINE:
            out += self._raise(
                AlertType.CAMERA_OFFLINE, f"CAM:{ev.camera_id}:{ev.payload['outage']}", t,
                f"Camera {ev.camera_id} is offline. Its tables keep their last known state.",
                camera_id=ev.camera_id,
            )
        elif ev.type == EventType.CAMERA_ONLINE:
            out += self._resolve(f"CAM:{ev.camera_id}:{ev.payload['outage']}", t, "camera_recovered")
        return out

    # ------------------------------------------------------------------
    # inputs
    # ------------------------------------------------------------------
    def ingest(self, frame: TrackFrame) -> list[Event]:
        cam = self.cameras.get(frame.camera_id)
        if cam is None:
            return []
        out: list[Event] = []
        self.last_t = max(self.last_t, frame.t)
        if cam.offline:
            out += self._camera_online(cam, frame.t)
        cam.last_frame_t = frame.t
        cam.frames += 1
        observations, assoc = cam.assigner.observations(frame)
        cam.last_association = {
            "t": frame.t,
            "per_table": {k: [z.track_id for z in v] for k, v in assoc.per_table.items() if v},
            "unassigned": assoc.unassigned,
            "ambiguous": assoc.ambiguous,
        }
        for obs in observations:
            cam.observations += 1
            sm = self.tables[obs.table_id]
            out += self._emit(sm.observe(obs, self.thresholds))
        return out

    def _camera_online(self, cam: CameraState, t: float) -> list[Event]:
        gap = t - (t if cam.offline_since is None else cam.offline_since)
        cam.offline = False
        for sm in self.tables.values():
            if sm.proj.camera_id == cam.camera_id:
                sm.set_online(t)
        ev = Event(
            type=EventType.CAMERA_ONLINE, t=t, source=Source.SYSTEM, camera_id=cam.camera_id,
            payload={"outage": cam.outages, "gap_s": round(gap, 2)},
            key=f"cam-online:{cam.camera_id}:{cam.outages}",
        )
        return self._emit([ev])

    def tick(self, t: float) -> list[Event]:
        """Time-based rules. Call regularly, even when no frames arrive."""
        th = self.thresholds
        self.last_t = max(self.last_t, t)
        out: list[Event] = []
        for cam in self.cameras.values():
            last = cam.last_frame_t if cam.last_frame_t is not None else cam.registered_t
            if not cam.offline and t - last > th.camera_offline_after_s:
                cam.offline = True
                cam.offline_since = last
                cam.outages += 1
                for sm in self.tables.values():
                    if sm.proj.camera_id == cam.camera_id:
                        sm.set_offline(t)
                out += self._emit([
                    Event(
                        type=EventType.CAMERA_OFFLINE, t=t, source=Source.SYSTEM, camera_id=cam.camera_id,
                        payload={"outage": cam.outages, "last_frame_t": cam.last_frame_t},
                        key=f"cam-offline:{cam.camera_id}:{cam.outages}",
                    )
                ])
        for tid in sorted(self.tables):
            p = self.tables[tid].proj
            label = self.info[tid].label
            if p.state == TableState.SEATED_UNSERVED and p.seated_t is not None:
                if t - p.seated_t >= th.unserved_warning_s:
                    mins = th.unserved_warning_s / 60
                    out += self._raise(
                        AlertType.UNSERVED_TOO_LONG, f"UNSERVED:{p.session_id}", t,
                        f"{label} has waited over {mins:g} min without confirmed service.",
                        table_id=tid, camera_id=p.camera_id, session_id=p.session_id,
                    )
            elif p.state == TableState.NEEDS_CLEANING and p.cleaning_required_t is not None:
                if t - p.cleaning_required_t >= th.cleaning_overdue_s:
                    mins = th.cleaning_overdue_s / 60
                    out += self._raise(
                        AlertType.CLEANING_OVERDUE, f"OVERDUE:{p.session_id}", t,
                        f"{label} has needed cleaning for over {mins:g} min.",
                        table_id=tid, camera_id=p.camera_id, session_id=p.session_id,
                    )
        return out

    def act(self, action: OperatorAction) -> list[Event]:
        """Apply an operator action. Raises ActionError when invalid."""
        if action.request_id and action.request_id in self._requests:
            return self._requests[action.request_id]
        t = action.t
        self.last_t = max(self.last_t, t)
        if action.type == ActionType.ACKNOWLEDGE_ALERT:
            if not action.alert_id:
                raise ActionError("alert_id is required.", "bad_request")
            alert = self.alerts.acknowledge(action.alert_id, t, action.actor)
            ev = Event(
                type=EventType.ALERT_ACKNOWLEDGED, t=t, source=action.source, table_id=alert.table_id,
                camera_id=alert.camera_id, session_id=alert.session_id,
                payload={"alert_id": alert.id, "alert_type": alert.type.value, "actor": action.actor},
                key=f"ack:{alert.id}",
            )
            events = self._emit([ev])
        else:
            sm = self.tables.get(action.table_id or "")
            if sm is None:
                raise ActionError(f"Unknown table '{action.table_id}'.", "not_found")
            if action.type == ActionType.CONFIRM_SERVICE:
                events = self._emit([sm.confirm_service(t, action.source, action.actor)])
            elif action.type == ActionType.CONFIRM_CLEANING:
                events = self._emit([sm.confirm_cleaning(t, action.source, action.actor)])
            elif action.type == ActionType.CORRECT_STATUS:
                if action.to_state is None:
                    raise ActionError("to_state is required for a correction.", "bad_request")
                events = self._emit([sm.correct_status(action.to_state, action.reason or "", t, action.source, action.actor)])
            elif action.type == ActionType.VERIFY_OBSERVATION:
                has_alert = any(
                    a.type == AlertType.LOW_CONFIDENCE_OBSERVATION and a.table_id == sm.table_id
                    for a in self.alerts.active()
                )
                if not has_alert and not sm.ev.lowq_reported:
                    raise ActionError("There is no uncertain observation to verify at this table.", "nothing_to_verify")
                ev = Event(
                    type=EventType.OBSERVATION_VERIFIED, t=t, source=action.source, table_id=sm.table_id,
                    camera_id=sm.proj.camera_id, session_id=sm.proj.session_id,
                    payload={"actor": action.actor, "state": sm.state.value, "note": action.reason or ""},
                )
                sm.ev.lowq_since = None
                sm.ev.lowq_reported = False
                events = self._emit([ev])
            else:
                raise ActionError(f"Unsupported action {action.type}.", "bad_request")
        if action.request_id:
            self._requests[action.request_id] = events
        return events

    # ------------------------------------------------------------------
    # replay
    # ------------------------------------------------------------------
    def restore(self, events: Iterable[Event]) -> int:
        """Rebuild durable state from an event history. Emits nothing."""
        n = 0
        for ev in sorted(events, key=lambda e: (e.seq or 0)):
            n += 1
            if ev.table_id in self.tables and ev.type in (
                EventType.GUESTS_SEATED, EventType.SERVICE_CONFIRMED, EventType.GUESTS_DEPARTED,
                EventType.CLEANING_CONFIRMED, EventType.STATUS_CORRECTED, EventType.POSSIBLE_STAFF_VISIT,
            ):
                self.tables[ev.table_id].apply(ev)
            self.alerts.apply_event(ev)
            if ev.seq:
                self._seq = itertools.count(ev.seq + 1)
            self.last_t = max(self.last_t, ev.t)
        return n

    # ------------------------------------------------------------------
    # read model
    # ------------------------------------------------------------------
    def table_view(self, tid: str, t: float) -> dict[str, Any]:
        sm = self.tables[tid]
        p, ev, th = sm.proj, sm.ev, self.thresholds
        info = self.info[tid]
        occupied = p.state in OCCUPIED_STATES
        wait_s = None
        if p.seated_t is not None and p.state != TableState.FREE:
            wait_s = (p.service_t if p.service_t is not None else (t if p.state == TableState.SEATED_UNSERVED else None))
            wait_s = None if wait_s is None else max(0.0, wait_s - p.seated_t)
        occupied_s = None
        if p.seated_t is not None:
            occupied_s = max(0.0, (p.left_t if p.left_t is not None else t) - p.seated_t)
        cleaning_s = max(0.0, t - p.cleaning_required_t) if p.state == TableState.NEEDS_CLEANING and p.cleaning_required_t is not None else None
        health = sm.health if p.camera_id else ObservationHealth.UNKNOWN
        uncertainty: list[str] = []
        if not p.camera_id:
            uncertainty.append("No camera zone covers this table. Status relies on staff actions.")
        if health == ObservationHealth.OFFLINE:
            uncertainty.append("Camera offline. State is frozen at the last known value.")
        if ev.lowq_reported or ev.lowq_since is not None:
            for r in sorted(ev.lowq_reasons):
                uncertainty.append({
                    "ambiguous_overlap": "A person stands where two table zones overlap.",
                    "low_detection_confidence": "Detections at this table are below the confidence threshold.",
                }.get(r, f"Camera reports {r.replace('_', ' ')}."))
        if p.departed_during_gap:
            uncertainty.append("Departure was confirmed after an observation gap. Exact leave time is uncertain.")
        if p.corrected:
            uncertainty.append("Status was corrected by an operator.")
        if occupied and not ev.departure_armed:
            uncertainty.append("Waiting for the camera to see the guests after a manual correction.")
        active = [a for a in self.alerts.active() if a.table_id == tid]
        cand_s = (t - ev.cand_start) if (p.state == TableState.FREE and ev.cand_start is not None) else None
        return {
            "table_id": tid,
            "label": info.label,
            "seats": info.seats,
            "area": info.area,
            "camera_id": p.camera_id,
            "state": p.state.value,
            "session_id": p.session_id,
            "seated_t": p.seated_t,
            "service_t": p.service_t,
            "service_source": p.service_source,
            "left_t": p.left_t,
            "cleaning_required_t": p.cleaning_required_t,
            "wait_s": wait_s,
            "occupied_s": occupied_s,
            "cleaning_s": cleaning_s,
            "unserved_overdue": p.state == TableState.SEATED_UNSERVED and wait_s is not None and wait_s >= th.unserved_warning_s,
            "cleaning_overdue": cleaning_s is not None and cleaning_s >= th.cleaning_overdue_s,
            "guest_count": p.guest_count,
            "present_count": ev.present_count if p.camera_id else None,
            "uncertain_count": ev.uncertain_count if p.camera_id else None,
            "seating_candidate_s": cand_s,
            "absence_s": round(ev.absent_accum, 1) if occupied else None,
            "visits": p.visits,
            "last_visit_t": p.last_visit_t,
            "reassociations": ev.reassociations,
            "health": health.value,
            "uncertainty": uncertainty,
            "alerts": [a.to_dict() for a in active],
        }

    def snapshot(self, t: float) -> dict[str, Any]:
        tables = [self.table_view(tid, t) for tid in self.tables]
        counts = {s.value: 0 for s in TableState}
        for tv in tables:
            counts[tv["state"]] += 1
        active = self.alerts.active()
        return {
            "t": t,
            "wall": self.wall(t),
            "tables": tables,
            "summary": {
                "total": len(tables),
                "free": counts["FREE"],
                "occupied": counts["SEATED_UNSERVED"] + counts["SERVED"],
                "awaiting_service": counts["SEATED_UNSERVED"],
                "served": counts["SERVED"],
                "needs_cleaning": counts["NEEDS_CLEANING"],
                "active_alerts": len([a for a in active if a.status == AlertStatus.ACTIVE]),
                "open_alerts": len(active),
                "uncertain_tables": len([tv for tv in tables if tv["health"] in ("OFFLINE", "LOW_CONFIDENCE", "DEGRADED")]),
            },
            "cameras": [
                {
                    "camera_id": c.camera_id,
                    "online": not c.offline and c.last_frame_t is not None,
                    "offline": c.offline,
                    "last_frame_t": c.last_frame_t,
                    "frames": c.frames,
                    "observations": c.observations,
                    "outages": c.outages,
                    "zones": len(c.assigner.zones),
                }
                for c in self.cameras.values()
            ],
            "alerts": [a.to_dict() for a in sorted(active, key=lambda a: a.created_t, reverse=True)],
        }

    def alert(self, alert_id: str) -> Alert | None:
        return self.alerts.get(alert_id)
