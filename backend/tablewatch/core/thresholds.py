"""Configurable thresholds with validation.

Every timing rule in the state machine reads from here. Nothing is hardcoded
in the transition logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

# (min, max, description) for each numeric field.
LIMITS: dict[str, tuple[float, float, str]] = {
    "occupancy_confirm_s": (1, 600, "Seconds of stable presence before a table counts as seated"),
    "departure_confirm_s": (5, 1800, "Seconds of healthy, confirmed absence before guests count as departed"),
    "lost_track_grace_s": (0.5, 120, "Gap in presence tolerated before a candidate or track is dropped"),
    "unserved_warning_s": (30, 7200, "Seconds seated without confirmed service before an alert"),
    "cleaning_overdue_s": (30, 7200, "Seconds in NEEDS_CLEANING before an escalation alert"),
    "min_visit_s": (1, 300, "Minimum stay of a non-guest track to log a possible staff visit"),
    "max_visit_s": (5, 1800, "A non-guest track staying longer than this joins the party"),
    "detection_confidence": (0.05, 0.95, "Minimum detection confidence counted as presence"),
    "camera_offline_after_s": (2, 300, "Seconds without frames before a camera is marked offline"),
    "low_confidence_alert_s": (5, 900, "Seconds of uncertain evidence before a low-confidence alert"),
    "low_confidence_clear_s": (2, 300, "Seconds of clean evidence before low confidence clears"),
    "max_observation_gap_s": (0.5, 60, "Longer gaps between observations do not count as observed absence"),
    "reassociation_window_s": (0.5, 120, "How long a lost guest track can be re-linked to a new track ID"),
    "reassociation_distance": (0.005, 0.5, "Max normalized distance for re-linking a lost guest track"),
    "ambiguity_margin": (0.0, 0.2, "Depth difference needed to resolve a point inside two zones"),
}


@dataclass(frozen=True)
class Thresholds:
    occupancy_confirm_s: float = 30.0
    departure_confirm_s: float = 90.0
    lost_track_grace_s: float = 8.0
    unserved_warning_s: float = 300.0
    cleaning_overdue_s: float = 300.0
    min_visit_s: float = 8.0
    max_visit_s: float = 180.0
    detection_confidence: float = 0.4
    camera_offline_after_s: float = 10.0
    low_confidence_alert_s: float = 30.0
    low_confidence_clear_s: float = 15.0
    max_observation_gap_s: float = 5.0
    reassociation_window_s: float = 15.0
    reassociation_distance: float = 0.08
    ambiguity_margin: float = 0.015

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Thresholds":
        errors = validate_thresholds(data)
        if errors:
            raise ValueError("; ".join(errors))
        known = {f.name for f in fields(cls)}
        merged = {**cls().to_dict(), **{k: float(v) for k, v in data.items() if k in known}}
        return cls(**merged)


def validate_thresholds(data: dict[str, Any]) -> list[str]:
    """Return a list of human readable problems. Empty means valid."""
    errors: list[str] = []
    known = {f.name for f in fields(Thresholds)}
    for key in data:
        if key not in known:
            errors.append(f"Unknown setting '{key}'")
    merged: dict[str, float] = Thresholds().to_dict()
    for key, value in data.items():
        if key not in known:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"'{key}' must be a number")
            continue
        if value != value or value in (float("inf"), float("-inf")):
            errors.append(f"'{key}' must be a finite number")
            continue
        lo, hi, _ = LIMITS[key]
        if not lo <= value <= hi:
            errors.append(f"'{key}' must be between {lo:g} and {hi:g}")
            continue
        merged[key] = float(value)
    if errors:
        return errors
    if merged["lost_track_grace_s"] >= merged["departure_confirm_s"]:
        errors.append("lost_track_grace_s must be shorter than departure_confirm_s")
    if merged["lost_track_grace_s"] >= merged["occupancy_confirm_s"] * 2:
        errors.append("lost_track_grace_s must be less than twice occupancy_confirm_s")
    if merged["min_visit_s"] >= merged["max_visit_s"]:
        errors.append("min_visit_s must be shorter than max_visit_s")
    if merged["max_observation_gap_s"] >= merged["departure_confirm_s"]:
        errors.append("max_observation_gap_s must be shorter than departure_confirm_s")
    return errors
