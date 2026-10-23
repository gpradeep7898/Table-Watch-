"""Deduplicated alert book.

Each alert has a dedup key tied to one lifecycle (a seating session, a
cleaning cycle, a camera outage). Raising the same key twice is a no-op,
even after the first alert was resolved. That is what stops alerts from
firing every frame.
"""

from __future__ import annotations

import hashlib

from .models import ActionError, Alert, AlertStatus, AlertType, Event, EventType, Severity

SEVERITY = {
    AlertType.UNSERVED_TOO_LONG: Severity.WARNING,
    AlertType.TABLE_NEEDS_CLEANING: Severity.INFO,
    AlertType.CLEANING_OVERDUE: Severity.WARNING,
    AlertType.CAMERA_OFFLINE: Severity.CRITICAL,
    AlertType.LOW_CONFIDENCE_OBSERVATION: Severity.WARNING,
}

NEXT_STEP = {
    AlertType.UNSERVED_TOO_LONG: "Send a server to the table, then tap Confirm service.",
    AlertType.TABLE_NEEDS_CLEANING: "Bus and reset the table, then tap Confirm cleaning.",
    AlertType.CLEANING_OVERDUE: "Cleaning is taking longer than planned. Check the table and confirm cleaning when done.",
    AlertType.CAMERA_OFFLINE: "Check camera power and network. Table states are frozen, not freed, until it returns.",
    AlertType.LOW_CONFIDENCE_OBSERVATION: "Glance at the table and verify its status, or correct it with a reason.",
}


class AlertBook:
    def __init__(self, id_prefix: str = "") -> None:
        # Alert IDs derive from the dedup key so identical runs give identical IDs.
        self.id_prefix = id_prefix
        self.alerts: dict[str, Alert] = {}
        self.by_key: dict[str, str] = {}

    def get(self, alert_id: str) -> Alert | None:
        return self.alerts.get(alert_id)

    def by_dedup(self, key: str) -> Alert | None:
        aid = self.by_key.get(key)
        return self.alerts.get(aid) if aid else None

    def active(self) -> list[Alert]:
        return [a for a in self.alerts.values() if a.status != AlertStatus.RESOLVED]

    def raise_alert(
        self,
        type_: AlertType,
        key: str,
        t: float,
        message: str,
        table_id: str | None = None,
        camera_id: str | None = None,
        session_id: str | None = None,
    ) -> Alert | None:
        if key in self.by_key:
            return None
        alert = Alert(
            id=hashlib.sha1(f"{self.id_prefix}{key}".encode()).hexdigest()[:20],
            type=type_,
            severity=SEVERITY[type_],
            dedup_key=key,
            created_t=t,
            message=message,
            table_id=table_id,
            camera_id=camera_id,
            session_id=session_id,
        )
        self.alerts[alert.id] = alert
        self.by_key[key] = alert.id
        return alert

    def acknowledge(self, alert_id: str, t: float, actor: str) -> Alert:
        alert = self.alerts.get(alert_id)
        if alert is None:
            raise ActionError("Alert not found.", "not_found")
        if alert.status == AlertStatus.RESOLVED:
            raise ActionError("This alert is already resolved.", "already_resolved")
        if alert.status == AlertStatus.ACKNOWLEDGED:
            raise ActionError("This alert was already acknowledged.", "already_acknowledged")
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_t = t
        alert.acknowledged_by = actor
        return alert

    def resolve(self, key: str, t: float, resolution: str) -> Alert | None:
        alert = self.by_dedup(key)
        if alert is None or alert.status == AlertStatus.RESOLVED:
            return None
        alert.status = AlertStatus.RESOLVED
        alert.resolved_t = t
        alert.resolution = resolution
        return alert

    # -- replay -----------------------------------------------------------
    def apply_event(self, ev: Event) -> None:
        if ev.type == EventType.ALERT_RAISED:
            alert = Alert.from_dict(ev.payload["alert"])
            self.alerts[alert.id] = alert
            self.by_key[alert.dedup_key] = alert.id
        elif ev.type == EventType.ALERT_ACKNOWLEDGED:
            alert = self.alerts.get(ev.payload["alert_id"])
            if alert and alert.status == AlertStatus.ACTIVE:
                alert.status = AlertStatus.ACKNOWLEDGED
                alert.acknowledged_t = ev.t
                alert.acknowledged_by = ev.payload.get("actor")
        elif ev.type == EventType.ALERT_RESOLVED:
            alert = self.alerts.get(ev.payload["alert_id"])
            if alert and alert.status != AlertStatus.RESOLVED:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_t = ev.t
                alert.resolution = ev.payload.get("resolution")
