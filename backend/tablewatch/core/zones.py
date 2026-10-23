"""Table seating zones: validation and per-frame track-to-table association."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import geometry
from .models import ObservationHealth, TableObservation, TrackFrame, ZoneTrack

ANCHOR_MODES = ("bottom_center", "centroid")
MAX_OVERLAP_FRACTION = 0.30  # more than this is "excessively ambiguous"


@dataclass(frozen=True)
class Zone:
    table_id: str
    camera_id: str
    polygon: tuple[tuple[float, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"table_id": self.table_id, "camera_id": self.camera_id, "polygon": [list(p) for p in self.polygon]}

    @classmethod
    def from_dict(cls, d: dict[str, Any], camera_id: str | None = None) -> "Zone":
        return cls(
            table_id=str(d["table_id"]),
            camera_id=str(camera_id or d["camera_id"]),
            polygon=tuple((float(p[0]), float(p[1])) for p in d["polygon"]),
        )


@dataclass
class ZoneValidation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_zones(
    zones: Iterable[dict[str, Any]],
    camera_id: str,
    known_cameras: Iterable[str],
    known_tables: Iterable[str] | None = None,
) -> ZoneValidation:
    """Validate raw zone dicts (as sent by the zone editor)."""
    result = ZoneValidation()
    if camera_id not in set(known_cameras):
        result.errors.append(f"Unknown camera '{camera_id}'")
    tables = set(known_tables) if known_tables is not None else None
    parsed: list[Zone] = []
    seen: set[str] = set()
    for i, raw in enumerate(zones):
        label = f"Zone #{i + 1}"
        try:
            table_id = str(raw["table_id"]).strip()
            pts = raw["polygon"]
        except (KeyError, TypeError):
            result.errors.append(f"{label}: needs table_id and polygon")
            continue
        if not table_id:
            result.errors.append(f"{label}: table_id is empty")
            continue
        label = f"Table {table_id}"
        if table_id in seen:
            result.errors.append(f"{label}: duplicate table ID on this camera")
        seen.add(table_id)
        if tables is not None and table_id not in tables:
            result.errors.append(f"{label}: no such table in the restaurant layout")
        if not isinstance(pts, (list, tuple)) or len(pts) < 3:
            result.errors.append(f"{label}: a polygon needs at least 3 points")
            continue
        if len(pts) > 64:
            result.errors.append(f"{label}: too many points (max 64)")
            continue
        try:
            poly = tuple((float(p[0]), float(p[1])) for p in pts)
        except (TypeError, ValueError, IndexError):
            result.errors.append(f"{label}: points must be [x, y] numbers")
            continue
        if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in poly):
            result.errors.append(f"{label}: points must be normalized between 0 and 1")
            continue
        if geometry.is_self_intersecting(poly):
            result.errors.append(f"{label}: polygon edges cross each other")
            continue
        if geometry.area(poly) < 1e-4:
            result.errors.append(f"{label}: polygon is too small or degenerate")
            continue
        parsed.append(Zone(table_id, camera_id, poly))
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            frac = geometry.overlap_fraction(parsed[i].polygon, parsed[j].polygon)
            pair = f"Tables {parsed[i].table_id} and {parsed[j].table_id}"
            if frac > MAX_OVERLAP_FRACTION:
                result.errors.append(f"{pair} overlap by {frac:.0%}; too ambiguous to tell guests apart")
            elif frac > 0.0:
                result.warnings.append(
                    f"{pair} overlap by {frac:.0%}; people in the overlap are marked ambiguous"
                )
    return result


@dataclass
class Association:
    per_table: dict[str, list[ZoneTrack]]
    unassigned: list[int]
    ambiguous: list[int]


class ZoneAssigner:
    """Maps tracked people to at most one table zone per frame.

    A point inside two zones goes to the zone it is clearly deeper inside.
    If depths are too close it is marked ambiguous for every containing zone,
    which blocks departure there but never counts as presence.
    """

    def __init__(self, zones: Iterable[Zone], anchor: str = "bottom_center", ambiguity_margin: float = 0.015):
        if anchor not in ANCHOR_MODES:
            raise ValueError(f"anchor must be one of {ANCHOR_MODES}")
        self.zones = list(zones)
        self.anchor = anchor
        self.margin = ambiguity_margin

    @property
    def table_ids(self) -> list[str]:
        return [z.table_id for z in self.zones]

    def associate(self, frame: TrackFrame) -> Association:
        per_table: dict[str, list[ZoneTrack]] = {z.table_id: [] for z in self.zones}
        unassigned: list[int] = []
        ambiguous: list[int] = []
        for tr in frame.tracks:
            p = tr.anchor(self.anchor)
            hits = [(geometry.depth(p, z.polygon), z) for z in self.zones if geometry.point_in_polygon(p, z.polygon)]
            if not hits:
                unassigned.append(tr.track_id)
                continue
            hits.sort(key=lambda h: -h[0])
            if len(hits) == 1 or hits[0][0] - hits[1][0] > self.margin:
                per_table[hits[0][1].table_id].append(ZoneTrack(tr.track_id, p, tr.confidence))
            else:
                ambiguous.append(tr.track_id)
                for _, z in hits:
                    per_table[z.table_id].append(ZoneTrack(tr.track_id, p, tr.confidence, ambiguous=True))
        return Association(per_table, unassigned, ambiguous)

    def observations(self, frame: TrackFrame) -> tuple[list[TableObservation], Association]:
        assoc = self.associate(frame)
        obs = [
            TableObservation(
                table_id=tid,
                camera_id=frame.camera_id,
                t=frame.t,
                tracks=tuple(tracks),
                source=frame.source,
                health=frame.health if frame.health != ObservationHealth.UNKNOWN else ObservationHealth.OK,
            )
            for tid, tracks in sorted(assoc.per_table.items())
        ]
        return obs, assoc
