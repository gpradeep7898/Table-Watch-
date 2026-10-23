"""Versioned settings: thresholds, simulation and privacy.

Defaults come from configs/thresholds.yaml. Saved values live in the local
database as configuration versions, so every change is kept and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from ..config.layout import project_root
from ..core.thresholds import Thresholds, validate_thresholds
from ..storage.db import Store

SIM_DEFAULTS: dict[str, Any] = {
    "default_scenario": "normal",
    "seed": None,  # None = scenario's own seed
    "staff_auto_confirm": True,
    "autoplay": True,
    "speed": 5.0,
}

PRIVACY_DEFAULTS: dict[str, Any] = {
    "event_retention_days": 90,
    "store_debug_video": False,
    "debug_video_retention_hours": 24,
}

ALLOWED_SPEEDS = (1.0, 5.0, 10.0)


def default_thresholds() -> Thresholds:
    path = project_root() / "configs" / "thresholds.yaml"
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        return Thresholds.from_dict(raw)
    return Thresholds()


def validate_sim(data: dict[str, Any], scenario_ids: set[str]) -> list[str]:
    errors = []
    for k in data:
        if k not in SIM_DEFAULTS:
            errors.append(f"Unknown simulation setting '{k}'")
    if "default_scenario" in data and data["default_scenario"] not in scenario_ids:
        errors.append("default_scenario must be one of: " + ", ".join(sorted(scenario_ids)))
    if "seed" in data and data["seed"] is not None:
        if isinstance(data["seed"], bool) or not isinstance(data["seed"], int) or not 0 <= data["seed"] <= 2**31:
            errors.append("seed must be a whole number between 0 and 2147483648, or empty")
    for k in ("staff_auto_confirm", "autoplay"):
        if k in data and not isinstance(data[k], bool):
            errors.append(f"{k} must be true or false")
    if "speed" in data and data["speed"] not in ALLOWED_SPEEDS:
        errors.append("speed must be 1, 5 or 10")
    return errors


def validate_privacy(data: dict[str, Any]) -> list[str]:
    errors = []
    for k in data:
        if k not in PRIVACY_DEFAULTS:
            errors.append(f"Unknown privacy setting '{k}'")
    d = data.get("event_retention_days")
    if d is not None and (isinstance(d, bool) or not isinstance(d, (int, float)) or not 1 <= d <= 3650):
        errors.append("event_retention_days must be between 1 and 3650")
    if "store_debug_video" in data and not isinstance(data["store_debug_video"], bool):
        errors.append("store_debug_video must be true or false")
    h = data.get("debug_video_retention_hours")
    if h is not None and (isinstance(h, bool) or not isinstance(h, (int, float)) or not 1 <= h <= 720):
        errors.append("debug_video_retention_hours must be between 1 and 720")
    return errors


@dataclass
class SettingsService:
    store: Store

    def thresholds(self) -> tuple[Thresholds, int]:
        saved, version = self.store.latest_config("thresholds")
        base = default_thresholds()
        if saved:
            try:
                return Thresholds.from_dict({**base.to_dict(), **saved}), version
            except ValueError:
                pass  # a bad saved version never blocks startup; defaults apply
        return base, version

    def save_thresholds(self, data: dict[str, Any]) -> tuple[Thresholds, int]:
        current, _ = self.thresholds()
        merged = {**current.to_dict(), **data}
        errors = validate_thresholds(data) or validate_thresholds(merged)
        if errors:
            raise ValueError(errors)
        th = Thresholds.from_dict(merged)
        version = self.store.save_config("thresholds", th.to_dict())
        return th, version

    def reset_thresholds(self) -> tuple[Thresholds, int]:
        th = default_thresholds()
        return th, self.store.save_config("thresholds", th.to_dict(), actor="reset")

    def _get(self, kind: str, defaults: dict[str, Any]) -> dict[str, Any]:
        saved, _ = self.store.latest_config(kind)
        return {**defaults, **{k: v for k, v in (saved or {}).items() if k in defaults}}

    def simulation(self) -> dict[str, Any]:
        return self._get("simulation", SIM_DEFAULTS)

    def save_simulation(self, data: dict[str, Any], scenario_ids: set[str]) -> dict[str, Any]:
        if "speed" in data and isinstance(data["speed"], int):
            data = {**data, "speed": float(data["speed"])}
        errors = validate_sim(data, scenario_ids)
        if errors:
            raise ValueError(errors)
        merged = {**self.simulation(), **data}
        self.store.save_config("simulation", merged)
        return merged

    def privacy(self) -> dict[str, Any]:
        return self._get("privacy", PRIVACY_DEFAULTS)

    def save_privacy(self, data: dict[str, Any]) -> dict[str, Any]:
        errors = validate_privacy(data)
        if errors:
            raise ValueError(errors)
        merged = {**self.privacy(), **data}
        self.store.save_config("privacy", merged)
        return merged
