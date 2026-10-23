"""Test-only builders for synthetic observations. Never imported by app code."""

from __future__ import annotations

from tablewatch.core.engine import RestaurantEngine, TableInfo
from tablewatch.core.models import (
    ObservationHealth,
    Source,
    TableObservation,
    TrackedPerson,
    TrackFrame,
    ZoneTrack,
)
from tablewatch.core.thresholds import Thresholds
from tablewatch.core.zones import Zone

TH = Thresholds()


def zt(track_id: int, x: float = 0.5, y: float = 0.5, conf: float = 0.9, ambiguous: bool = False) -> ZoneTrack:
    return ZoneTrack(track_id, (x, y), conf, ambiguous)


def obs(t: float, tracks=(), health=ObservationHealth.OK, table="T1") -> TableObservation:
    return TableObservation(table, "cam", t, tuple(tracks), Source.SIMULATOR, health)


def feed(sm, start: float, end: float, tracks_at, dt: float = 0.5, th: Thresholds = TH):
    """Feed observations from start (inclusive) to end (exclusive)."""
    events = []
    t = start
    while t < end - 1e-9:
        events += sm.observe(obs(t, tracks_at(t), table=sm.table_id), th)
        t = round(t + dt, 6)
    return events


def types(events):
    return [e.type.value for e in events]


# Two side-by-side square zones on one camera, plus a deliberately
# overlapping strip in the middle (0.45..0.55).
ZONE_T1 = ((0.05, 0.1), (0.55, 0.1), (0.55, 0.9), (0.05, 0.9))
ZONE_T2 = ((0.45, 0.1), (0.95, 0.1), (0.95, 0.9), (0.45, 0.9))


def person(track_id: int, x: float, y: float, conf: float = 0.9, size: float = 0.04) -> TrackedPerson:
    return TrackedPerson(track_id, (x - size / 2, y - size / 2, x + size / 2, y + size / 2), conf)


def make_engine(th: Thresholds = TH, events=None) -> RestaurantEngine:
    eng = RestaurantEngine(
        [TableInfo("T1", "Table 1"), TableInfo("T2", "Table 2")],
        thresholds=th,
        wall=lambda t: 1_700_000_000 + t,
        session_prefix="test:",
    )
    eng.register_camera("cam", [Zone("T1", "cam", ZONE_T1), Zone("T2", "cam", ZONE_T2)], "centroid", Source.SIMULATOR)
    if events is not None:
        eng.subscribe(events.append)
    return eng


def run_engine(eng: RestaurantEngine, start: float, end: float, people_at, dt: float = 0.5, camera_up=lambda t: True):
    """Drive the engine with frames + ticks. people_at(t) -> list[TrackedPerson]."""
    out = []
    t = start
    while t < end - 1e-9:
        if camera_up(t):
            out += eng.ingest(TrackFrame("cam", t, tuple(people_at(t)), Source.SIMULATOR, ObservationHealth.OK))
        out += eng.tick(t)
        t = round(t + dt, 6)
    return out
