"""Scenario definitions (YAML in demo/scenarios) and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..config.layout import project_root

DISTURBANCE_TYPES = {"dropout", "id_switch", "occlusion", "wander", "camera_outage", "low_confidence", "ambiguous_stand"}


@dataclass(frozen=True)
class PartySpec:
    table: str | None
    size: int
    arrive_s: float
    service_delay_s: float | None
    dine_s: float
    cleaning_delay_s: float | None
    operator_confirms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    summary: str
    description: str
    seed: int
    start_time: datetime
    duration_s: float
    waiters: int
    parties: tuple[PartySpec, ...]
    disturbances: tuple[dict[str, Any], ...] = ()
    walkers: tuple[dict[str, Any], ...] = ()
    staff_auto_confirm: bool = True
    confirm_lag_s: tuple[float, float] = (8.0, 15.0)
    operator_confirm_lag_s: float = 150.0
    alert_response_lag_s: tuple[float, float] = (30.0, 50.0)
    order: int = 99
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "summary": self.summary,
            "description": self.description,
            "seed": self.seed,
            "start_time": self.start_time.isoformat(),
            "duration_s": self.duration_s,
            "waiters": self.waiters,
            "parties": len(self.parties),
            "disturbances": [d["type"] for d in self.disturbances],
        }


def _pair(v: Any, default: tuple[float, float]) -> tuple[float, float]:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    lo, hi = float(v[0]), float(v[1])
    if lo > hi or lo < 0:
        raise ValueError("lag ranges must be [low, high] with 0 <= low <= high")
    return (lo, hi)


def parse_scenario(raw: dict[str, Any], table_ids: set[str] | None = None) -> Scenario:
    parties = []
    for i, p in enumerate(raw.get("parties", [])):
        table = p.get("table")
        if table is not None and table_ids is not None and table not in table_ids:
            raise ValueError(f"party {i}: unknown table {table}")
        size = int(p["size"])
        if not 1 <= size <= 12:
            raise ValueError(f"party {i}: size must be 1..12")
        parties.append(
            PartySpec(
                table=table,
                size=size,
                arrive_s=float(p["arrive_s"]),
                service_delay_s=None if p.get("service_delay_s") is None else float(p["service_delay_s"]),
                dine_s=float(p["dine_s"]),
                cleaning_delay_s=None if p.get("cleaning_delay_s") is None else float(p["cleaning_delay_s"]),
                operator_confirms=tuple(p.get("operator_confirms") or ()),
            )
        )
    for d in raw.get("disturbances", []) or []:
        if d.get("type") not in DISTURBANCE_TYPES:
            raise ValueError(f"unknown disturbance type {d.get('type')}")
        if "party" in d and not 0 <= int(d["party"]) < len(parties):
            raise ValueError(f"disturbance refers to missing party {d['party']}")
    start = raw.get("start_time", "2026-10-23T12:00:00")
    return Scenario(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        summary=str(raw.get("summary", "")),
        description=str(raw.get("description", "")).strip(),
        seed=int(raw.get("seed", 1)),
        start_time=datetime.fromisoformat(start) if isinstance(start, str) else start,
        duration_s=float(raw.get("duration_s", 1200)),
        waiters=max(1, int(raw.get("waiters", 3))),
        parties=tuple(parties),
        disturbances=tuple(raw.get("disturbances", []) or []),
        walkers=tuple(raw.get("walkers", []) or []),
        staff_auto_confirm=bool(raw.get("staff_auto_confirm", True)),
        confirm_lag_s=_pair(raw.get("confirm_lag_s"), (8.0, 15.0)),
        operator_confirm_lag_s=float(raw.get("operator_confirm_lag_s", 150.0)),
        alert_response_lag_s=_pair(raw.get("alert_response_lag_s"), (30.0, 50.0)),
        order=int(raw.get("order", 99)),
    )


def scenario_dir() -> Path:
    return project_root() / "demo" / "scenarios"


def load_scenarios(table_ids: set[str] | None = None, directory: Path | None = None) -> dict[str, Scenario]:
    out: dict[str, Scenario] = {}
    for path in sorted((directory or scenario_dir()).glob("*.yaml")):
        sc = parse_scenario(yaml.safe_load(path.read_text()), table_ids)
        out[sc.id] = sc
    return dict(sorted(out.items(), key=lambda kv: kv[1].order))
