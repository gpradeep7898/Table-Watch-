"""Deterministic restaurant simulator.

Simulated guests and staff move through the TableWatch Demo Bistro. Each
step produces a synthetic camera frame: a list of tracked people with
bounding boxes and confidences, generated *directly* from the simulated
positions. No neural network is involved, and the output is labelled
``Source.SIMULATOR`` everywhere.

The frames go through the exact same ``RestaurantEngine.ingest`` path as
real video. Simulated staff may also issue operator actions (for example
"service confirmed" from a simulated tablet); those are tagged with
``Source.SIMULATOR`` as well.

Everything random comes from one seeded ``random.Random`` consumed in a
fixed order, so a scenario + seed replays identically.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..config.layout import Layout, TableGeom
from ..core.models import (
    ActionType,
    ObservationHealth,
    OperatorAction,
    Source,
    TableState,
    TrackedPerson,
    TrackFrame,
)
from .scenarios import PartySpec, Scenario

Point = tuple[float, float]

PERSON_SIZE_M = 0.5
GUEST_SPEED = 1.2
STAFF_SPEED = 1.5
VISIT_DURATIONS = {"service": (24, 36), "food": (14, 22), "check": (12, 18), "clean": (65, 90)}


class EngineView(Protocol):
    def table_state(self, table_id: str) -> TableState: ...

    def active_alerts(self) -> list[tuple[str, str | None]]: ...


@dataclass
class Agent:
    pid: int
    role: str  # guest | staff | passerby
    x: float
    y: float
    speed: float
    track_id: int
    path: list[Point] = field(default_factory=list)
    party: int | None = None
    rest: Point | None = None  # where the agent idles (seat, home)
    activity: str = "walking"
    on_arrive: Callable[[], None] | None = None
    gone: bool = False
    offscreen: bool = False
    missed: int = 0
    conf: float = 0.0
    detected: bool = False


@dataclass
class PartyState:
    idx: int
    spec: PartySpec
    phase: str = "pending"  # pending, arriving, waiting, walking_in, seated, leaving, gone, abandoned
    table: str | None = None
    members: list[int] = field(default_factory=list)
    arrived_host_t: float | None = None
    seated_t: float | None = None
    leave_t: float | None = None
    service_dispatched: bool = False
    service_visited: bool = False
    cleaning_dispatched: bool = False
    cleaning_needed_t: float | None = None


@dataclass
class Task:
    due: float
    priority: int
    seq: int
    kind: str
    table: str
    party: int | None


@dataclass
class Waiter:
    agent: Agent
    home: Point
    task: Task | None = None
    busy_until: float | None = None
    visit_start: float = 0.0


@dataclass
class SimStep:
    t: float
    frame: TrackFrame | None
    actions: list[OperatorAction]
    scene: dict[str, Any]


class Simulator:
    def __init__(
        self,
        layout: Layout,
        scenario: Scenario,
        camera_id: str = "sim-cam-1",
        seed: int | None = None,
        staff_auto_confirm: bool | None = None,
        dt: float = 0.5,
    ) -> None:
        self.layout = layout
        self.sc = scenario
        self.camera_id = camera_id
        self.seed = scenario.seed if seed is None else seed
        self.rng = random.Random(self.seed)
        self.auto_confirm = scenario.staff_auto_confirm if staff_auto_confirm is None else staff_auto_confirm
        self.dt = dt
        self.t = 0.0
        self._pid = 0
        self._track = 0
        self._task_seq = 0
        self.agents: list[Agent] = []
        self.parties = [PartyState(i, p) for i, p in enumerate(scenario.parties)]
        self.reserved: dict[str, int] = {}
        self.tasks: list[Task] = []
        self.pending: list[tuple[float, ActionType, str, int]] = []
        self.truth: list[dict[str, Any]] = []
        self.disturbances = sorted(scenario.disturbances, key=lambda d: d["at_s"])
        self._dist_done: set[int] = set()
        self.walkers = sorted(scenario.walkers, key=lambda w: w["at_s"])
        self._walkers_done: set[int] = set()
        self.hidden_windows: list[tuple[int, float, float]] = []  # pid, start, end
        self.conf_windows: list[tuple[int, float, float, float]] = []  # pid, start, end, conf
        self.occluders: list[tuple[int, int]] = []  # occluder pid, occluded pid
        self._returns: list[tuple[float, int, Point | None]] = []  # due, pid, seat (None = leave)
        self.pts = layout.points
        homes = self.pts.get("staff_home") or [[16.5, 3.0]]
        self.waiters: list[Waiter] = []
        for i in range(scenario.waiters):
            hx, hy = homes[i % len(homes)]
            home = (float(hx), float(hy) + 0.6 * (i // len(homes)))
            a = self._spawn("staff", home, STAFF_SPEED)
            a.rest = home
            a.activity = "idle"
            self.waiters.append(Waiter(a, home))
        self.frames_emitted = 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _spawn(self, role: str, pos: Point, speed: float) -> Agent:
        self._pid += 1
        self._track += 1
        a = Agent(self._pid, role, pos[0], pos[1], speed, self._track)
        self.agents.append(a)
        return a

    def _new_track(self, a: Agent) -> None:
        self._track += 1
        a.track_id = self._track

    def agent(self, pid: int) -> Agent:
        for a in self.agents:
            if a.pid == pid:
                return a
        raise KeyError(pid)

    def _uniform(self, lo_hi: tuple[float, float]) -> float:
        lo, hi = lo_hi
        return lo + (hi - lo) * self.rng.random()

    def _task(self, due: float, kind: str, table: str, party: int | None, priority: int = 1) -> None:
        self._task_seq += 1
        self.tasks.append(Task(due, priority, self._task_seq, kind, table, party))

    def _aisle_for(self, table: TableGeom, target_y: float) -> float:
        a_top, a_bottom = self.pts.get("aisles_y", [4.1, 8.4])
        if table.center[1] < a_top:
            return a_top
        if table.center[1] > a_bottom:
            return a_bottom
        return a_top if target_y < table.center[1] else a_bottom

    def _route_to_table(self, start: Point, table: TableGeom, target: Point, via_x: float) -> list[Point]:
        """Aisle-following route from ``start`` (at x=via_x side) to a point at a table."""
        aisle = self._aisle_for(table, target[1])
        path = [(via_x, start[1]), (via_x, aisle)]
        x0, y0, x1, y1 = table.rect
        far_side = (target[1] - table.center[1]) * (aisle - table.center[1]) < 0 and (x0 <= target[0] <= x1)
        if far_side:
            side_x = x0 - 0.5
            path += [(side_x, aisle), (side_x, target[1])]
        else:
            path.append((target[0], aisle))
        path.append(target)
        return path

    def _route_from_table(self, table: TableGeom, pos: Point, end_x: float, end: Point) -> list[Point]:
        aisle = self._aisle_for(table, pos[1])
        x0, _, x1, _ = table.rect
        path: list[Point] = []
        if (pos[1] - table.center[1]) * (aisle - table.center[1]) < 0 and x0 <= pos[0] <= x1:
            path += [(x0 - 0.5, pos[1]), (x0 - 0.5, aisle)]
        else:
            path.append((pos[0], aisle))
        path += [(end_x, aisle), (end_x, end[1]), end]
        return path

    # ------------------------------------------------------------------
    # main step
    # ------------------------------------------------------------------
    def step(self, view: EngineView) -> SimStep:
        t = self.t
        actions: list[OperatorAction] = []
        self._arrivals(t)
        self._host(t, view)
        self._parties(t, view)
        self._alert_reactions(t, view)
        self._waiters(t, view)
        actions += self._due_actions(t, view)
        self._disturbances(t)
        self._walker_trips(t)
        self._move()
        frame = self._detect(t)
        scene = self.scene(t)
        self.t = round(t + self.dt, 6)
        return SimStep(t, frame, actions, scene)

    @property
    def finished(self) -> bool:
        return self.t >= self.sc.duration_s

    # ------------------------------------------------------------------
    def _arrivals(self, t: float) -> None:
        entrance = tuple(self.pts.get("entrance", [0.4, 11.2]))
        host = tuple(self.pts.get("host", [1.6, 10.9]))
        for p in self.parties:
            if p.phase != "pending" or p.spec.arrive_s > t:
                continue
            p.phase = "arriving"
            waiting_spots = sum(1 for q in self.parties if q.phase in ("arriving", "waiting"))
            for m in range(p.spec.size):
                a = self._spawn("guest", (entrance[0] - 0.2 * m, entrance[1] + 0.15 * (m % 2)), GUEST_SPEED)
                a.party = p.idx
                spot = (host[0] + 0.45 * m + 0.6 * (waiting_spots - 1), host[1] + 0.35 * (m % 2))
                a.path = [spot]
                a.rest = spot
                a.activity = "arriving"
                p.members.append(a.pid)

    def _pick_table(self, p: PartyState, view: EngineView) -> str | None:
        def available(tid: str) -> bool:
            return tid not in self.reserved and view.table_state(tid) == TableState.FREE

        if p.spec.table:
            return p.spec.table if available(p.spec.table) else None
        options = [t for t in self.layout.tables if t.seats >= p.spec.size and available(t.id)]
        if not options:
            return None
        return min(options, key=lambda t: (t.seats, t.id)).id

    def _host(self, t: float, view: EngineView) -> None:
        for p in self.parties:
            if p.phase == "arriving" and all(not self.agent(m).path for m in p.members):
                p.phase = "waiting"
                p.arrived_host_t = t
            if p.phase != "waiting":
                continue
            if t - (t if p.arrived_host_t is None else p.arrived_host_t) > 900:
                p.phase = "abandoned"
                self._leave_building(p)
                continue
            tid = self._pick_table(p, view)
            if tid is None:
                continue
            p.table = tid
            self.reserved[tid] = p.idx
            p.phase = "walking_in"
            table = self.layout.table(tid)
            seats = table.seat_positions()
            for i, pid in enumerate(p.members):
                a = self.agent(pid)
                seat = seats[i % len(seats)]
                if i >= len(seats):  # extra chair at the end
                    seat = (table.rect[2] + 0.45, table.center[1] + 0.3 * (i - len(seats)))
                a.rest = seat
                a.path = self._route_to_table((a.x, a.y), table, seat, via_x=1.6 + 0.12 * i)
                a.activity = "walking_in"

    def _leave_building(self, p: PartyState) -> None:
        entrance = tuple(self.pts.get("entrance", [0.4, 11.2]))
        host = tuple(self.pts.get("host", [1.6, 10.9]))
        for pid in p.members:
            a = self.agent(pid)
            if a.gone:
                continue
            if p.table:
                a.path = self._route_from_table(self.layout.table(p.table), (a.x, a.y), 1.6, host)
            else:
                a.path = [host]
            a.path.append((entrance[0] - 0.6, entrance[1]))
            a.activity = "leaving"
            a.rest = None

            def gone(a=a):
                a.gone = True

            a.on_arrive = gone

    def _parties(self, t: float, view: EngineView) -> None:
        for p in self.parties:
            if p.phase == "walking_in":
                if all(not self.agent(m).path for m in p.members):
                    p.phase = "seated"
                    p.seated_t = t
                    for m in p.members:
                        self.agent(m).activity = "seated"
                    self.truth.append({"type": "seated", "table": p.table, "t": t, "party_size": p.spec.size})
                    if p.spec.service_delay_s is not None:
                        self._task(t + p.spec.service_delay_s, "service", p.table, p.idx)
                        p.service_dispatched = True
            elif p.phase == "seated":
                if t >= (t if p.seated_t is None else p.seated_t) + p.spec.dine_s and self._all_home(p):
                    p.phase = "leaving"
                    p.leave_t = t
                    self.truth.append({"type": "departed", "table": p.table, "t": t})
                    self._leave_building(p)
            elif p.phase == "leaving" and all(self.agent(m).gone for m in p.members):
                p.phase = "gone"
            if p.phase in ("leaving", "gone") and p.table:
                state = view.table_state(p.table)
                if state == TableState.NEEDS_CLEANING and p.cleaning_needed_t is None:
                    p.cleaning_needed_t = t
                    if p.spec.cleaning_delay_s is not None:
                        self._task(t + p.spec.cleaning_delay_s, "clean", p.table, p.idx)
                        p.cleaning_dispatched = True
                if state == TableState.FREE and self.reserved.get(p.table) == p.idx:
                    del self.reserved[p.table]
        # finished parties that never got a table release nothing
        for tid, idx in list(self.reserved.items()):
            if self.parties[idx].phase == "abandoned":
                del self.reserved[tid]

    def _all_home(self, p: PartyState) -> bool:
        """Members are at their seats (not on a restroom trip or wandering)."""
        return all((not self.agent(m).path) and self.agent(m).activity == "seated" for m in p.members)

    def _alert_reactions(self, t: float, view: EngineView) -> None:
        alerts = view.active_alerts()
        for type_, tid in alerts:
            if tid is None or tid not in self.reserved:
                continue
            p = self.parties[self.reserved[tid]]
            if type_ == "UNSERVED_TOO_LONG" and p.phase == "seated" and not p.service_dispatched:
                p.service_dispatched = True
                self._task(t + self._uniform(self.sc.alert_response_lag_s), "service", tid, p.idx, priority=0)
            elif type_ == "CLEANING_OVERDUE" and p.phase in ("leaving", "gone") and not p.cleaning_dispatched:
                p.cleaning_dispatched = True
                self._task(t + self._uniform(self.sc.alert_response_lag_s), "clean", tid, p.idx, priority=0)

    def _waiters(self, t: float, view: EngineView) -> None:
        kitchen_x = float(self.pts.get("service_aisle_x", 15.1))
        for w in self.waiters:
            a = w.agent
            if w.task is None:
                due = [k for k in self.tasks if k.due <= t]
                if not due:
                    continue
                task = min(due, key=lambda k: (k.priority, k.due, k.seq))
                self.tasks.remove(task)
                if not self._task_still_valid(task, view):
                    continue
                w.task = task
                table = self.layout.table(task.table)
                target = table.staff_point()
                a.path = self._route_to_table((a.x, a.y), table, target, via_x=kitchen_x)
                a.activity = f"to_{task.kind}"
                w.busy_until = None
            elif not a.path and w.busy_until is None and a.activity.startswith("to_"):
                lo, hi = VISIT_DURATIONS[w.task.kind]
                w.busy_until = t + lo + (hi - lo) * self.rng.random()
                a.activity = w.task.kind
                w.visit_start = t
            elif w.busy_until is not None and t >= w.busy_until:
                self._finish_task(w, t)
                table = self.layout.table(w.task.table)
                a.path = self._route_from_table(table, (a.x, a.y), kitchen_x, w.home)
                a.activity = "returning"
                w.task = None
                w.busy_until = None
            elif w.task is None and not a.path:
                a.activity = "idle"
        for w in self.waiters:
            if w.task is None and not w.agent.path and w.agent.activity == "returning":
                w.agent.activity = "idle"

    def _task_still_valid(self, task: Task, view: EngineView) -> bool:
        p = self.parties[task.party] if task.party is not None else None
        if task.kind == "clean":
            return view.table_state(task.table) == TableState.NEEDS_CLEANING
        return p is not None and p.phase == "seated"

    def _finish_task(self, w: Waiter, t: float) -> None:
        task = w.task
        assert task is not None
        p = self.parties[task.party] if task.party is not None else None
        self.truth.append({
            "type": "staff_visit", "table": task.table, "kind": task.kind,
            "start": w.visit_start, "end": t,
        })
        if task.kind == "service" and p is not None:
            p.service_visited = True
            self._task(t + 150 + 120 * self.rng.random(), "food", task.table, p.idx, priority=2)
            check_t = (t if p.seated_t is None else p.seated_t) + p.spec.dine_s - 90
            if check_t > t + 200:
                self._task(check_t, "check", task.table, p.idx, priority=2)
            if self.auto_confirm:
                lag = (self.sc.operator_confirm_lag_s if "service" in p.spec.operator_confirms
                       else self._uniform(self.sc.confirm_lag_s))
                self.pending.append((t + lag, ActionType.CONFIRM_SERVICE, task.table, p.idx))
        elif task.kind == "clean" and self.auto_confirm:
            lag = (self.sc.operator_confirm_lag_s if p is not None and "cleaning" in p.spec.operator_confirms
                   else self._uniform(self.sc.confirm_lag_s))
            self.pending.append((t + lag, ActionType.CONFIRM_CLEANING, task.table, task.party or -1))

    def _due_actions(self, t: float, view: EngineView) -> list[OperatorAction]:
        out: list[OperatorAction] = []
        keep = []
        for due, kind, tid, pidx in self.pending:
            if due > t:
                keep.append((due, kind, tid, pidx))
                continue
            state = view.table_state(tid)
            ok = (kind == ActionType.CONFIRM_SERVICE and state == TableState.SEATED_UNSERVED) or (
                kind == ActionType.CONFIRM_CLEANING and state == TableState.NEEDS_CLEANING
            )
            if ok:
                out.append(OperatorAction(kind, t, source=Source.SIMULATOR, table_id=tid, actor="simulated staff tablet"))
        self.pending = keep
        return out

    # ------------------------------------------------------------------
    # disturbances
    # ------------------------------------------------------------------
    def _member(self, d: dict[str, Any]) -> Agent | None:
        p = self.parties[int(d["party"])]
        idx = int(d.get("member", 0))
        if idx >= len(p.members):
            return None
        a = self.agent(p.members[idx])
        return None if a.gone else a

    def _disturbances(self, t: float) -> None:
        for i, d in enumerate(self.disturbances):
            if i in self._dist_done or d["at_s"] > t:
                continue
            self._dist_done.add(i)
            kind = d["type"]
            end = d["at_s"] + float(d.get("duration_s", 0))
            if kind == "dropout":
                a = self._member(d)
                if a:
                    self.hidden_windows.append((a.pid, t, end))
            elif kind == "id_switch":
                a = self._member(d)
                if a:
                    self._new_track(a)
            elif kind == "low_confidence":
                p = self.parties[int(d["party"])]
                for pid in p.members:
                    self.conf_windows.append((pid, t, end, float(d.get("confidence", 0.25))))
            elif kind == "occlusion":
                a = self._member(d)
                if a:
                    self._visitor_stand((a.x + 0.12, a.y + 0.05), float(d.get("duration_s", 15)), occludes=a.pid)
            elif kind == "ambiguous_stand":
                t1, t2 = (self.layout.table(x) for x in d["tables"])
                z1, z2 = self.layout.default_zone(t1, "_"), self.layout.default_zone(t2, "_")
                xs1 = [p[0] * self.layout.width for p in z1.polygon]
                xs2 = [p[0] * self.layout.width for p in z2.polygon]
                mid_x = (max(min(xs1), min(xs2)) + min(max(xs1), max(xs2))) / 2
                mid_y = (t1.center[1] + t2.center[1]) / 2
                self._visitor_stand((mid_x, mid_y), float(d.get("duration_s", 30)), role="staff")
            elif kind == "wander":
                a = self._member(d)
                if a and a.activity == "seated":
                    target_table = self.layout.table(d["to_table"])
                    tx, ty = target_table.staff_point()
                    seat = a.rest
                    a.path = [(a.x, self._aisle_for(target_table, ty)), (tx, self._aisle_for(target_table, ty)), (tx, ty)]
                    a.activity = "wandering"
                    stay = float(d.get("duration_s", 10))

                    def at_other(a=a, seat=seat, stay=stay, start=t):
                        a.rest = (a.x, a.y)
                        self._schedule_return(a, seat, start, stay)

                    a.on_arrive = at_other
            # camera_outage is checked in _detect

    def _schedule_return(self, a: Agent, seat: Point | None, start: float, stay: float) -> None:
        self._returns.append((self.t + stay, a.pid, seat))

    def _visitor_stand(self, spot: Point, duration: float, occludes: int | None = None, role: str = "passerby") -> None:
        kitchen_x = float(self.pts.get("service_aisle_x", 15.1))
        home = (kitchen_x + 1.0, 3.6) if role == "staff" else tuple(self.pts.get("host", [1.6, 10.9]))
        a = self._spawn(role, home, STAFF_SPEED if role == "staff" else GUEST_SPEED)
        # Walk via the nearest aisle.
        a_top, a_bottom = self.pts.get("aisles_y", [4.1, 8.4])
        aisle = a_top if abs(spot[1] - a_top) < abs(spot[1] - a_bottom) else a_bottom
        a.path = [(home[0], aisle), (spot[0], aisle), spot]
        a.activity = "visiting"

        def arrived(a=a):
            a.rest = (a.x, a.y)
            if occludes is not None:
                self.occluders.append((a.pid, occludes))
            self._returns.append((self.t + duration, a.pid, None))
            a.on_arrive = None

        a.on_arrive = arrived

    def _walker_trips(self, t: float) -> None:
        for i, w in enumerate(self.walkers):
            if i in self._walkers_done or w["at_s"] > t:
                continue
            self._walkers_done.add(i)
            if w.get("route") != "restroom":
                continue
            seated = [p for p in self.parties if p.phase == "seated" and len(p.members) > 1]
            if not seated:
                continue
            p = seated[self.rng.randrange(len(seated))]
            a = self.agent(p.members[-1])
            if a.activity != "seated":
                continue
            table = self.layout.table(p.table) if p.table else None
            restroom = tuple(self.pts.get("restroom", [14.3, 12.2]))
            seat = a.rest
            if table is None:
                continue
            a.path = self._route_from_table(table, (a.x, a.y), restroom[0], restroom)
            a.activity = "restroom"

            def at_restroom(a=a, seat=seat, table=table):
                a.offscreen = True
                a.on_arrive = None
                self._returns.append((self.t + 60 + 60 * self.rng.random(), a.pid, seat))

            a.on_arrive = at_restroom

    # ------------------------------------------------------------------
    # movement and detection
    # ------------------------------------------------------------------
    def _move(self) -> None:
        due = [r for r in self._returns if r[0] <= self.t]
        self._returns = [r for r in self._returns if r[0] > self.t]
        for _, pid, seat in due:
            a = self.agent(pid)
            a.offscreen = False
            self.occluders = [(o, v) for o, v in self.occluders if o != pid]
            if seat is None:  # visitor leaves the room
                home = (16.6, 3.6) if a.role == "staff" else tuple(self.pts.get("host", [1.6, 10.9]))
                a_top, a_bottom = self.pts.get("aisles_y", [4.1, 8.4])
                aisle = a_top if abs(a.y - a_top) < abs(a.y - a_bottom) else a_bottom
                a.path = [(a.x, aisle), (home[0], aisle), home]
                a.rest = None
                a.activity = "leaving"

                def gone(a=a):
                    a.gone = True

                a.on_arrive = gone
            else:
                party = self.parties[a.party] if a.party is not None else None
                table = self.layout.table(party.table) if party and party.table else None
                if table:
                    a.path = self._route_to_table((a.x, a.y), table, seat, via_x=a.x)
                else:
                    a.path = [seat]
                a.rest = seat
                a.activity = "returning_to_seat"

                def back(a=a):
                    a.activity = "seated"
                    a.on_arrive = None

                a.on_arrive = back
        for a in self.agents:
            if a.gone:
                continue
            step = a.speed * self.dt
            while a.path and step > 0:
                tx, ty = a.path[0]
                d = math.hypot(tx - a.x, ty - a.y)
                if d <= step:
                    a.x, a.y = tx, ty
                    a.path.pop(0)
                    step -= d
                else:
                    a.x += (tx - a.x) / d * step
                    a.y += (ty - a.y) / d * step
                    step = 0
            if not a.path and a.on_arrive is not None:
                cb, a.on_arrive = a.on_arrive, None
                cb()

    def _detect(self, t: float) -> TrackFrame | None:
        outage = any(
            d["type"] == "camera_outage" and d["at_s"] <= t < d["at_s"] + float(d.get("duration_s", 0))
            for d in self.disturbances
        )
        tracks: list[TrackedPerson] = []
        occluded = set()
        for occ_pid, victim in self.occluders:
            occ = self.agent(occ_pid)
            vic = self.agent(victim)
            if math.hypot(occ.x - vic.x, occ.y - vic.y) < 0.4:
                occluded.add(victim)
        w, h = self.layout.width, self.layout.height
        for a in self.agents:
            # Random draws happen for every agent every step to keep the stream stable.
            miss_roll = self.rng.random()
            switch_roll = self.rng.random()
            conf_noise = self.rng.gauss(0, 0.04)
            jx, jy = self.rng.gauss(0, 0.035), self.rng.gauss(0, 0.035)
            a.detected = False
            if a.gone or a.offscreen:
                continue
            x, y = a.x, a.y
            if not a.path and a.activity in ("seated", "idle"):
                x, y = x + jx, y + jy  # fidgeting
            if outage:
                continue
            hidden = any(pid == a.pid and s <= t < e for pid, s, e in self.hidden_windows) or a.pid in occluded
            p_miss = 0.025 if a.path else 0.012
            if hidden or miss_roll < p_miss:
                a.missed += 1
                continue
            if a.missed >= 4 and switch_roll < 0.35:
                self._new_track(a)  # tracker lost the person and re-identified them as new
            a.missed = 0
            base = 0.85 if a.role == "staff" else 0.87
            conf = max(0.45, min(0.99, base + conf_noise))
            for pid, s, e, c in self.conf_windows:
                if pid == a.pid and s <= t < e:
                    conf = c
            if any(o == a.pid for o, _ in self.occluders):
                conf = min(conf, 0.35)
            a.conf = conf
            a.detected = True
            half_w, half_h = PERSON_SIZE_M / 2 / w, PERSON_SIZE_M / 2 / h
            nx, ny = x / w, y / h
            tracks.append(TrackedPerson(a.track_id, (nx - half_w, ny - half_h, nx + half_w, ny + half_h), round(conf, 3)))
        if outage:
            return None
        self.frames_emitted += 1
        return TrackFrame(self.camera_id, t, tuple(tracks), Source.SIMULATOR, ObservationHealth.OK)

    # ------------------------------------------------------------------
    def scene(self, t: float) -> dict[str, Any]:
        w, h = self.layout.width, self.layout.height
        return {
            "t": t,
            "people": [
                {
                    "id": a.pid,
                    "x": round(a.x / w, 4),
                    "y": round(a.y / h, 4),
                    "role": a.role,
                    "activity": a.activity,
                    "party": a.party,
                    "track_id": a.track_id if a.detected else None,
                    "detected": a.detected,
                    "conf": round(a.conf, 2) if a.detected else None,
                }
                for a in self.agents
                if not a.gone and not a.offscreen
            ],
            "waiting_parties": sum(1 for p in self.parties if p.phase in ("arriving", "waiting")),
            "camera_outage": any(
                d["type"] == "camera_outage" and d["at_s"] <= t < d["at_s"] + float(d.get("duration_s", 0))
                for d in self.disturbances
            ),
        }

    def ground_truth(self) -> dict[str, Any]:
        """Simulator truth in the evaluation annotation format (docs/pilot-readiness.md)."""
        return {
            "format": "tablewatch-groundtruth/v1",
            "source": "simulator (synthetic, not real footage)",
            "scenario": self.sc.id,
            "seed": self.seed,
            "events": [dict(e) for e in self.truth],
        }
