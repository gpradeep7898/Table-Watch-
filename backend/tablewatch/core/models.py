"""Plain domain types. No framework imports allowed in this package."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TableState(str, Enum):
    FREE = "FREE"
    SEATED_UNSERVED = "SEATED_UNSERVED"
    SERVED = "SERVED"
    NEEDS_CLEANING = "NEEDS_CLEANING"


OCCUPIED_STATES = (TableState.SEATED_UNSERVED, TableState.SERVED)


class ObservationHealth(str, Enum):
    """Health of the evidence for a table. Never a claim that a table is empty."""

    UNKNOWN = "UNKNOWN"  # no observation received yet
    OK = "OK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


class Source(str, Enum):
    SIMULATOR = "simulator"
    COMPUTER_VISION = "computer_vision"
    OPERATOR = "operator"
    SYSTEM = "system"  # timers and timeouts


class Dataset(str, Enum):
    SIMULATION = "simulation"
    RECORDED = "recorded"
    LIVE = "live"


class EventType(str, Enum):
    GUESTS_SEATED = "GUESTS_SEATED"
    POSSIBLE_STAFF_VISIT = "POSSIBLE_STAFF_VISIT"
    SERVICE_CONFIRMED = "SERVICE_CONFIRMED"
    GUESTS_DEPARTED = "GUESTS_DEPARTED"
    CLEANING_CONFIRMED = "CLEANING_CONFIRMED"
    STATUS_CORRECTED = "STATUS_CORRECTED"
    OBSERVATION_VERIFIED = "OBSERVATION_VERIFIED"
    LOW_CONFIDENCE_STARTED = "LOW_CONFIDENCE_STARTED"
    LOW_CONFIDENCE_ENDED = "LOW_CONFIDENCE_ENDED"
    CAMERA_OFFLINE = "CAMERA_OFFLINE"
    CAMERA_ONLINE = "CAMERA_ONLINE"
    ALERT_RAISED = "ALERT_RAISED"
    ALERT_ACKNOWLEDGED = "ALERT_ACKNOWLEDGED"
    ALERT_RESOLVED = "ALERT_RESOLVED"
    RUN_STARTED = "RUN_STARTED"
    RUN_ENDED = "RUN_ENDED"


class AlertType(str, Enum):
    UNSERVED_TOO_LONG = "UNSERVED_TOO_LONG"
    TABLE_NEEDS_CLEANING = "TABLE_NEEDS_CLEANING"
    CLEANING_OVERDUE = "CLEANING_OVERDUE"
    CAMERA_OFFLINE = "CAMERA_OFFLINE"
    LOW_CONFIDENCE_OBSERVATION = "LOW_CONFIDENCE_OBSERVATION"


class AlertStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ActionType(str, Enum):
    CONFIRM_SERVICE = "CONFIRM_SERVICE"
    CONFIRM_CLEANING = "CONFIRM_CLEANING"
    CORRECT_STATUS = "CORRECT_STATUS"
    ACKNOWLEDGE_ALERT = "ACKNOWLEDGE_ALERT"
    VERIFY_OBSERVATION = "VERIFY_OBSERVATION"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class TrackedPerson:
    """One tracked person in one frame. Coordinates normalized to 0..1."""

    track_id: int
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    confidence: float

    def anchor(self, mode: str) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        if mode == "centroid":
            return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        return ((x1 + x2) / 2.0, y2)  # bottom_center


@dataclass(frozen=True)
class TrackFrame:
    """Everything one camera saw at one instant."""

    camera_id: str
    t: float
    tracks: tuple[TrackedPerson, ...]
    source: Source
    health: ObservationHealth = ObservationHealth.OK


@dataclass(frozen=True)
class ZoneTrack:
    """A track whose anchor fell inside a table's seating zone."""

    track_id: int
    position: tuple[float, float]
    confidence: float
    ambiguous: bool = False


@dataclass(frozen=True)
class TableObservation:
    table_id: str
    camera_id: str
    t: float
    tracks: tuple[ZoneTrack, ...]
    source: Source
    health: ObservationHealth = ObservationHealth.OK


@dataclass
class Event:
    type: EventType
    t: float
    source: Source
    table_id: str | None = None
    camera_id: str | None = None
    session_id: str | None = None
    from_state: TableState | None = None
    to_state: TableState | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    key: str | None = None  # idempotency key, unique per dataset/run
    id: str = field(default_factory=new_id)
    wall: float | None = None
    seq: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("type", "source", "from_state", "to_state"):
            v = getattr(self, k)
            d[k] = v.value if isinstance(v, Enum) else v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            type=EventType(d["type"]),
            t=float(d["t"]),
            source=Source(d["source"]),
            table_id=d.get("table_id"),
            camera_id=d.get("camera_id"),
            session_id=d.get("session_id"),
            from_state=TableState(d["from_state"]) if d.get("from_state") else None,
            to_state=TableState(d["to_state"]) if d.get("to_state") else None,
            payload=dict(d.get("payload") or {}),
            key=d.get("key"),
            id=d.get("id") or new_id(),
            wall=d.get("wall"),
            seq=d.get("seq"),
        )


@dataclass
class Alert:
    id: str
    type: AlertType
    severity: Severity
    dedup_key: str
    created_t: float
    message: str
    table_id: str | None = None
    camera_id: str | None = None
    session_id: str | None = None
    status: AlertStatus = AlertStatus.ACTIVE
    acknowledged_t: float | None = None
    acknowledged_by: str | None = None
    resolved_t: float | None = None
    resolution: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["severity"] = self.severity.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Alert":
        return cls(
            id=d["id"],
            type=AlertType(d["type"]),
            severity=Severity(d["severity"]),
            dedup_key=d["dedup_key"],
            created_t=float(d["created_t"]),
            message=d.get("message", ""),
            table_id=d.get("table_id"),
            camera_id=d.get("camera_id"),
            session_id=d.get("session_id"),
            status=AlertStatus(d.get("status", "ACTIVE")),
            acknowledged_t=d.get("acknowledged_t"),
            acknowledged_by=d.get("acknowledged_by"),
            resolved_t=d.get("resolved_t"),
            resolution=d.get("resolution"),
        )


@dataclass(frozen=True)
class OperatorAction:
    type: ActionType
    t: float
    source: Source = Source.OPERATOR
    table_id: str | None = None
    alert_id: str | None = None
    to_state: TableState | None = None
    reason: str | None = None
    actor: str = "operator"
    request_id: str | None = None  # client-supplied idempotency key


class ActionError(Exception):
    """An operator action that is not valid in the current state."""

    def __init__(self, message: str, code: str = "invalid_action") -> None:
        super().__init__(message)
        self.code = code
