"""Deterministic per-table state machine.

Two layers live here:

* ``TableProjection`` is the durable state (FREE, SEATED_UNSERVED, ...). It is
  only ever changed by ``reduce(projection, event)``. Replaying the event log
  through ``reduce`` rebuilds it exactly, which is how restarts work.
* ``Evidence`` is short-lived temporal memory: candidate timers, absence
  accumulators, which track IDs belong to the party. It is never persisted.
  After a restart it starts empty, which can only delay transitions, never
  invent them.

Rules that matter (see docs/state-machine.md):

* Seating needs ``occupancy_confirm_s`` of presence with gaps under
  ``lost_track_grace_s``.
* Departure needs ``departure_confirm_s`` of *healthy, observed* absence.
  Offline cameras, low-confidence tracks, ambiguous tracks and long
  observation gaps all freeze the absence timer.
* A new person at an occupied table is only ever a POSSIBLE_STAFF_VISIT.
  Service is confirmed by an operator action, nothing else.
* Cleaning is confirmed by an operator action, never by a timeout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .models import (
    OCCUPIED_STATES,
    ActionError,
    Event,
    EventType,
    ObservationHealth,
    Source,
    TableObservation,
    TableState,
)
from .thresholds import Thresholds


@dataclass
class TableProjection:
    table_id: str
    camera_id: str | None = None
    state: TableState = TableState.FREE
    session_id: str | None = None
    session_count: int = 0
    seated_t: float | None = None
    seated_source: str | None = None
    service_t: float | None = None
    service_source: str | None = None
    left_t: float | None = None
    cleaning_required_t: float | None = None
    guest_count: int = 0
    visits: int = 0
    last_visit_t: float | None = None
    departed_during_gap: bool = False
    corrected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "camera_id": self.camera_id,
            "state": self.state.value,
            "session_id": self.session_id,
            "session_count": self.session_count,
            "seated_t": self.seated_t,
            "seated_source": self.seated_source,
            "service_t": self.service_t,
            "service_source": self.service_source,
            "left_t": self.left_t,
            "cleaning_required_t": self.cleaning_required_t,
            "guest_count": self.guest_count,
            "visits": self.visits,
            "last_visit_t": self.last_visit_t,
            "departed_during_gap": self.departed_during_gap,
            "corrected": self.corrected,
        }


def _clear_session(p: TableProjection) -> None:
    p.session_id = None
    p.seated_t = p.service_t = p.left_t = p.cleaning_required_t = None
    p.seated_source = p.service_source = None
    p.guest_count = 0
    p.visits = 0
    p.last_visit_t = None
    p.departed_during_gap = False
    p.corrected = False


def reduce(p: TableProjection, ev: Event) -> None:
    """Apply one event to a projection. Pure function of (projection, event)."""
    et = ev.type
    if et == EventType.GUESTS_SEATED:
        _clear_session(p)
        p.state = TableState.SEATED_UNSERVED
        p.session_id = ev.session_id
        p.session_count += 1
        p.seated_t = float(ev.payload.get("seated_t", ev.t))
        p.seated_source = ev.source.value
        p.guest_count = int(ev.payload.get("guest_count", 1))
    elif et == EventType.SERVICE_CONFIRMED:
        p.state = TableState.SERVED
        p.service_t = ev.t
        p.service_source = ev.source.value
    elif et == EventType.GUESTS_DEPARTED:
        p.state = TableState.NEEDS_CLEANING
        p.left_t = float(ev.payload.get("left_t", ev.t))
        p.cleaning_required_t = ev.t
        p.departed_during_gap = bool(ev.payload.get("during_observation_gap", False))
        p.guest_count = 0
    elif et == EventType.CLEANING_CONFIRMED:
        p.state = TableState.FREE
        _clear_session(p)
    elif et == EventType.POSSIBLE_STAFF_VISIT:
        p.visits += 1
        p.last_visit_t = ev.t
    elif et == EventType.STATUS_CORRECTED:
        frm, to = ev.from_state, ev.to_state
        assert to is not None
        if to == TableState.FREE:
            _clear_session(p)
        elif frm == TableState.FREE:  # FREE -> occupied
            _clear_session(p)
            p.session_id = ev.session_id
            p.session_count += 1
            p.seated_t = ev.t
            p.seated_source = ev.source.value
            p.guest_count = int(ev.payload.get("guest_count", 1))
            if to == TableState.SERVED:
                p.service_t = ev.t
                p.service_source = ev.source.value
        elif to == TableState.NEEDS_CLEANING:  # occupied -> needs cleaning
            p.left_t = ev.t
            p.cleaning_required_t = ev.t
        else:  # NEEDS_CLEANING -> occupied (false departure)
            p.left_t = None
            p.cleaning_required_t = None
            p.departed_during_gap = False
        p.state = to
        if to != TableState.FREE:
            p.corrected = True


@dataclass
class _Track:
    first_seen: float
    last_seen: float
    pos: tuple[float, float]


@dataclass
class Evidence:
    last_obs_t: float | None = None
    obs_health: ObservationHealth = ObservationHealth.UNKNOWN
    offline: bool = False
    offline_since: float | None = None
    # seating candidate
    cand_start: float | None = None
    cand_obs: int = 0
    cand_present_obs: int = 0
    cand_max_count: int = 0
    cand_tracks: set[int] = field(default_factory=set)
    # presence
    last_presence_t: float | None = None
    present_count: int = 0
    uncertain_count: int = 0
    absent_accum: float = 0.0
    absence_had_gap: bool = False
    departure_armed: bool = True
    # party membership
    guests: dict[int, _Track] = field(default_factory=dict)
    visitors: dict[int, _Track] = field(default_factory=dict)
    reassociations: int = 0
    # low confidence episodes
    lowq_since: float | None = None
    last_lowq_t: float | None = None
    lowq_reported: bool = False
    lowq_reasons: set[str] = field(default_factory=set)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class TableStateMachine:
    def __init__(self, table_id: str, camera_id: str | None = None, session_prefix: str = "") -> None:
        self.proj = TableProjection(table_id=table_id, camera_id=camera_id)
        self.ev = Evidence()
        self.session_prefix = session_prefix

    # ------------------------------------------------------------------
    @property
    def table_id(self) -> str:
        return self.proj.table_id

    @property
    def state(self) -> TableState:
        return self.proj.state

    def _event(self, type_: EventType, t: float, source: Source, **kw: Any) -> Event:
        return Event(type=type_, t=t, source=source, table_id=self.table_id, camera_id=self.proj.camera_id, **kw)

    def apply(self, event: Event) -> None:
        """Reduce the projection and reset whatever evidence the event invalidates."""
        reduce(self.proj, event)
        ev = self.ev
        et = event.type
        if et == EventType.GUESTS_SEATED:
            ev.absent_accum = 0.0
            ev.absence_had_gap = False
            ev.departure_armed = True
            ev.visitors.clear()
            ev.cand_start = None
        elif et in (EventType.GUESTS_DEPARTED, EventType.CLEANING_CONFIRMED):
            ev.guests.clear()
            ev.visitors.clear()
            ev.cand_start = None
            ev.absent_accum = 0.0
        elif et == EventType.STATUS_CORRECTED:
            ev.cand_start = None
            ev.absent_accum = 0.0
            ev.absence_had_gap = False
            if event.to_state in (TableState.FREE, TableState.NEEDS_CLEANING):
                ev.guests.clear()
                ev.visitors.clear()
            else:
                # Operator says guests are here. Only start counting absence
                # after the camera has actually seen someone, so a blind spot
                # never overrides the operator.
                ev.departure_armed = False

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------
    def observe(self, obs: TableObservation, th: Thresholds) -> list[Event]:
        ev = self.ev
        t = obs.t
        events: list[Event] = []
        dt = (t - ev.last_obs_t) if ev.last_obs_t is not None else 0.0
        gap = dt > th.max_observation_gap_s
        ev.last_obs_t = t
        ev.obs_health = obs.health
        if obs.health == ObservationHealth.OFFLINE:
            return events

        confident = [z for z in obs.tracks if not z.ambiguous and z.confidence >= th.detection_confidence]
        uncertain = [z for z in obs.tracks if z.ambiguous or z.confidence < th.detection_confidence]
        present = bool(confident)
        ev.present_count = len(confident)
        ev.uncertain_count = len(uncertain)
        lowq = bool(uncertain) or obs.health in (ObservationHealth.DEGRADED, ObservationHealth.LOW_CONFIDENCE)
        self._update_low_quality(t, lowq, uncertain, obs.health, th, events)

        self._update_membership(confident, t, th, events, obs.source)

        state = self.proj.state
        if state == TableState.FREE:
            events += self._seating(t, confident, present, obs, th)
        elif state in OCCUPIED_STATES:
            if present:
                ev.last_presence_t = t
                ev.absent_accum = 0.0
                ev.absence_had_gap = False
                ev.departure_armed = True
            elif not ev.departure_armed or uncertain or obs.health != ObservationHealth.OK:
                pass  # uncertain evidence freezes the absence timer
            elif gap:
                ev.absence_had_gap = True  # unobserved time is not absence
            else:
                ev.absent_accum += dt
                if ev.absent_accum >= th.departure_confirm_s:
                    events.append(self._departure(t, obs.source))
        return events

    def _seating(self, t, confident, present, obs, th) -> list[Event]:
        ev = self.ev
        if present:
            if ev.cand_start is None or ev.last_presence_t is None or t - ev.last_presence_t > th.lost_track_grace_s:
                ev.cand_start = t
                ev.cand_obs = ev.cand_present_obs = ev.cand_max_count = 0
                ev.cand_tracks = set()
            ev.last_presence_t = t
            ev.cand_tracks |= {z.track_id for z in confident}
            ev.cand_max_count = max(ev.cand_max_count, len(confident))
        elif ev.cand_start is not None and ev.last_presence_t is not None and t - ev.last_presence_t > th.lost_track_grace_s:
            ev.cand_start = None
        if ev.cand_start is None:
            return []
        ev.cand_obs += 1
        ev.cand_present_obs += int(present)
        if present and t - ev.cand_start >= th.occupancy_confirm_s:
            n = self.proj.session_count + 1
            session_id = f"{self.session_prefix}{self.table_id}-{n}"
            quality = ev.cand_present_obs / max(1, ev.cand_obs)
            mean_conf = sum(z.confidence for z in confident) / len(confident)
            seated = self._event(
                EventType.GUESTS_SEATED,
                t,
                obs.source,
                session_id=session_id,
                from_state=TableState.FREE,
                to_state=TableState.SEATED_UNSERVED,
                payload={
                    "seated_t": ev.cand_start,
                    "guest_count": ev.cand_max_count,
                    "initial_tracks": sorted(ev.cand_tracks),
                    "evidence_quality": round(quality, 3),
                    "mean_confidence": round(mean_conf, 3),
                    "observation_source": obs.source.value,
                },
                key=f"seated:{session_id}",
            )
            # The tracks seen while the candidate formed are the party.
            for tid in list(ev.visitors):
                ev.guests[tid] = ev.visitors.pop(tid)
            self.apply(seated)
            return [seated]
        return []

    def _departure(self, t: float, source: Source) -> Event:
        p, ev = self.proj, self.ev
        left_t = ev.last_presence_t if ev.last_presence_t is not None else t
        event = self._event(
            EventType.GUESTS_DEPARTED,
            t,
            source,
            session_id=p.session_id,
            from_state=p.state,
            to_state=TableState.NEEDS_CLEANING,
            payload={
                "left_t": left_t,
                "occupied_s": round(left_t - (p.seated_t or left_t), 3),
                "served": p.service_t is not None,
                "during_observation_gap": ev.absence_had_gap,
                "observed_absence_s": round(ev.absent_accum, 3),
            },
            key=f"departed:{p.session_id}",
        )
        self.apply(event)
        return event

    def _update_membership(self, confident, t: float, th: Thresholds, events: list[Event], source: Source) -> None:
        """Keep track of who belongs to the party and who is just visiting."""
        ev, p = self.ev, self.proj
        occupied = p.state in OCCUPIED_STATES
        seen_now = {z.track_id for z in confident}
        for z in confident:
            if z.track_id in ev.guests:
                g = ev.guests[z.track_id]
                g.last_seen, g.pos = t, z.position
                continue
            if z.track_id in ev.visitors:
                v = ev.visitors[z.track_id]
                v.last_seen, v.pos = t, z.position
                continue
            # A new track ID. Is it a guest whose track was briefly lost?
            lost = [
                (tid, g)
                for tid, g in ev.guests.items()
                if tid not in seen_now
                and t - g.last_seen <= th.reassociation_window_s
                and _dist(g.pos, z.position) <= th.reassociation_distance
            ]
            if lost:
                tid, g = min(lost, key=lambda item: _dist(item[1].pos, z.position))
                del ev.guests[tid]
                ev.guests[z.track_id] = _Track(g.first_seen, t, z.position)
                ev.reassociations += 1
            else:
                # Occupied: a possible staff visit or late guest. FREE: part of
                # a seating candidate. NEEDS_CLEANING: possibly the busser.
                ev.visitors[z.track_id] = _Track(t, t, z.position)

        # Expire visitors that left, promote ones that stayed.
        for tid in list(ev.visitors):
            v = ev.visitors[tid]
            stay = v.last_seen - v.first_seen
            if occupied and stay > th.max_visit_s:
                ev.guests[tid] = ev.visitors.pop(tid)  # joined the party
                continue
            if t - v.last_seen > th.lost_track_grace_s:
                del ev.visitors[tid]
                if stay >= th.min_visit_s and p.state != TableState.FREE:
                    guests_present = any(t - g.last_seen <= th.lost_track_grace_s for g in ev.guests.values())
                    if p.state == TableState.NEEDS_CLEANING or guests_present:
                        kind = "possible_cleaning_visit" if p.state == TableState.NEEDS_CLEANING else "possible_service_visit"
                        visit = self._event(
                            EventType.POSSIBLE_STAFF_VISIT,
                            t,
                            source,
                            session_id=p.session_id,
                            payload={
                                "kind": kind,
                                "track_id": tid,
                                "arrived_t": v.first_seen,
                                "left_t": v.last_seen,
                                "duration_s": round(stay, 2),
                                "note": "Unverified. A visit is not proof of service or cleaning.",
                            },
                            key=f"visit:{p.session_id}:{tid}:{round(v.first_seen, 2)}",
                        )
                        self.apply(visit)
                        events.append(visit)
        # Forget guests not seen for a long time (they may come back with a new ID).
        horizon = max(th.reassociation_window_s, th.lost_track_grace_s) * 4
        for tid in [tid for tid, g in ev.guests.items() if t - g.last_seen > horizon]:
            del ev.guests[tid]

    def _update_low_quality(self, t, lowq, uncertain, health, th: Thresholds, events: list[Event]) -> None:
        ev = self.ev
        if lowq:
            if ev.lowq_since is None:
                ev.lowq_since = t
                ev.lowq_reasons = set()
            ev.last_lowq_t = t
            if any(z.ambiguous for z in uncertain):
                ev.lowq_reasons.add("ambiguous_overlap")
            if any((not z.ambiguous) and z.confidence < th.detection_confidence for z in uncertain):
                ev.lowq_reasons.add("low_detection_confidence")
            if health in (ObservationHealth.DEGRADED, ObservationHealth.LOW_CONFIDENCE):
                ev.lowq_reasons.add(f"camera_{health.value.lower()}")
            if not ev.lowq_reported and t - ev.lowq_since >= th.low_confidence_alert_s:
                ev.lowq_reported = True
                events.append(
                    self._event(
                        EventType.LOW_CONFIDENCE_STARTED,
                        t,
                        Source.SYSTEM,
                        session_id=self.proj.session_id,
                        payload={"since_t": ev.lowq_since, "reasons": sorted(ev.lowq_reasons)},
                        key=f"lowq:{self.table_id}:{round(ev.lowq_since, 2)}",
                    )
                )
        elif ev.lowq_since is not None and ev.last_lowq_t is not None and t - ev.last_lowq_t >= th.low_confidence_clear_s:
            if ev.lowq_reported:
                events.append(
                    self._event(
                        EventType.LOW_CONFIDENCE_ENDED,
                        t,
                        Source.SYSTEM,
                        session_id=self.proj.session_id,
                        payload={"since_t": ev.lowq_since, "duration_s": round(ev.last_lowq_t - ev.lowq_since, 2)},
                        key=f"lowq-end:{self.table_id}:{round(ev.lowq_since, 2)}",
                    )
                )
            ev.lowq_since = None
            ev.last_lowq_t = None
            ev.lowq_reported = False
            ev.lowq_reasons = set()

    # ------------------------------------------------------------------
    # Camera health
    # ------------------------------------------------------------------
    def set_offline(self, t: float) -> None:
        self.ev.offline = True
        self.ev.offline_since = t
        self.ev.absence_had_gap = True

    def set_online(self, t: float) -> None:
        self.ev.offline = False
        self.ev.offline_since = None
        self.ev.last_obs_t = None  # the gap is unobserved, never absence
        # Party members were last seen before the outage. Treat them as lost
        # "just now" so returning tracks can be re-linked by position.
        for g in self.ev.guests.values():
            g.last_seen = t

    @property
    def health(self) -> ObservationHealth:
        ev = self.ev
        if ev.offline:
            return ObservationHealth.OFFLINE
        if ev.lowq_reported:
            return ObservationHealth.LOW_CONFIDENCE
        return ev.obs_health

    # ------------------------------------------------------------------
    # Operator actions
    # ------------------------------------------------------------------
    def confirm_service(self, t: float, source: Source, actor: str) -> Event:
        p = self.proj
        if p.state == TableState.SERVED:
            raise ActionError(f"Service at table {self.table_id} was already confirmed.", "already_confirmed")
        if p.state != TableState.SEATED_UNSERVED:
            raise ActionError(
                f"Table {self.table_id} is {p.state.value}; service can only be confirmed for seated guests.",
                "invalid_state",
            )
        wait = t - (p.seated_t if p.seated_t is not None else t)
        event = self._event(
            EventType.SERVICE_CONFIRMED,
            t,
            source,
            session_id=p.session_id,
            from_state=p.state,
            to_state=TableState.SERVED,
            payload={"wait_s": round(wait, 3), "actor": actor},
            key=f"served:{p.session_id}",
        )
        self.apply(event)
        return event

    def confirm_cleaning(self, t: float, source: Source, actor: str) -> Event:
        p = self.proj
        if p.state != TableState.NEEDS_CLEANING:
            raise ActionError(
                f"Table {self.table_id} is {p.state.value}; only a table that needs cleaning can be marked clean.",
                "invalid_state",
            )
        required = p.cleaning_required_t if p.cleaning_required_t is not None else t
        left = p.left_t if p.left_t is not None else required
        event = self._event(
            EventType.CLEANING_CONFIRMED,
            t,
            source,
            session_id=p.session_id,
            from_state=p.state,
            to_state=TableState.FREE,
            payload={
                "cleaning_s": round(t - required, 3),
                "turnover_s": round(t - left, 3),
                "actor": actor,
            },
            key=f"cleaned:{p.session_id}",
        )
        self.apply(event)
        return event

    def correct_status(self, to_state: TableState, reason: str, t: float, source: Source, actor: str) -> Event:
        p = self.proj
        reason = (reason or "").strip()
        if len(reason) < 3:
            raise ActionError("A status correction needs a reason (at least 3 characters).", "reason_required")
        if len(reason) > 500:
            raise ActionError("Reason is too long (max 500 characters).", "reason_too_long")
        frm = p.state
        if to_state == frm:
            raise ActionError(f"Table {self.table_id} is already {frm.value}.", "no_change")
        if frm == TableState.SEATED_UNSERVED and to_state == TableState.SERVED:
            raise ActionError("Use Confirm service to record service; it keeps the wait time honest.", "use_confirm_service")
        if frm == TableState.SERVED and to_state == TableState.SEATED_UNSERVED:
            raise ActionError("A confirmed service cannot be un-confirmed. Add a new correction reason instead.", "cannot_unserve")
        if frm == TableState.NEEDS_CLEANING and to_state != TableState.FREE:
            if p.service_t is not None and to_state == TableState.SEATED_UNSERVED:
                raise ActionError("This party's service was confirmed; reopen it as SERVED.", "cannot_unserve")
            if p.service_t is None and to_state == TableState.SERVED:
                raise ActionError("Reopen the party as SEATED_UNSERVED, then confirm service.", "use_confirm_service")
        session_id = p.session_id
        payload: dict[str, Any] = {"reason": reason, "actor": actor, "previous_session_id": p.session_id}
        if frm == TableState.FREE:
            session_id = f"{self.session_prefix}{self.table_id}-{p.session_count + 1}"
            payload["guest_count"] = max(1, self.ev.present_count)
        if frm in OCCUPIED_STATES and to_state == TableState.FREE:
            payload["closed_as"] = "false_occupancy_or_missed_departure"
        if frm == TableState.NEEDS_CLEANING and to_state == TableState.FREE:
            payload["closed_as"] = "freed_without_cleaning_confirmation"
        event = self._event(
            EventType.STATUS_CORRECTED,
            t,
            source,
            session_id=session_id,
            from_state=frm,
            to_state=to_state,
            payload=payload,
        )
        self.apply(event)
        return event
