"""Restaurant layout (floor plan) and project paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.engine import TableInfo
from ..core.zones import Zone


def project_root() -> Path:
    env = os.environ.get("TABLEWATCH_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    d = Path(os.environ.get("TABLEWATCH_DATA_DIR") or project_root() / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class TableGeom:
    id: str
    label: str
    area: str
    seats: int
    center: tuple[float, float]
    size: tuple[float, float]
    shape: str = "rect"
    staff_side: str | None = None

    @property
    def rect(self) -> tuple[float, float, float, float]:
        (cx, cy), (w, h) = self.center, self.size
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def seat_positions(self) -> list[tuple[float, float]]:
        """Chair positions in meters, 0.45 m outside the table edge."""
        x0, y0, x1, y1 = self.rect
        cx, cy = self.center
        off = 0.45
        n = self.seats
        if n == 2:
            return [(x0 - off, cy), (x1 + off, cy)]
        w, h = self.size
        if n == 4 and abs(w - h) < 0.05:
            return [(cx, y0 - off), (x1 + off, cy), (cx, y1 + off), (x0 - off, cy)]
        top = (n + 1) // 2
        bottom = n - top
        seats = [(x0 + w * (i + 0.5) / top, y0 - off) for i in range(top)]
        seats += [(x0 + w * (i + 0.5) / bottom, y1 + off) for i in range(bottom)]
        return seats

    def staff_point(self) -> tuple[float, float]:
        """Where a server stands to serve: inside the zone, beside the table."""
        x0, y0, x1, y1 = self.rect
        cx, cy = self.center
        side = self.staff_side
        if side is None:
            w, h = self.size
            side = "bottom" if self.seats == 2 else ("corner" if abs(w - h) < 0.05 else "right")
        return {
            "left": (x0 - 0.5, cy),
            "right": (x1 + 0.5, cy),
            "top": (cx, y0 - 0.5),
            "bottom": (cx, y1 + 0.5),
            "corner": (x1 + 0.35, y1 + 0.35),
        }[side]


@dataclass
class Layout:
    id: str
    name: str
    width: float
    height: float
    zone_margin: float
    tables: list[TableGeom]
    areas: list[dict[str, Any]]
    fixtures: list[dict[str, Any]]
    points: dict[str, Any] = field(default_factory=dict)

    def table(self, tid: str) -> TableGeom:
        for t in self.tables:
            if t.id == tid:
                return t
        raise KeyError(tid)

    def table_infos(self) -> list[TableInfo]:
        return [TableInfo(t.id, t.label, t.seats, t.area) for t in self.tables]

    def norm(self, p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] / self.width, p[1] / self.height)

    def default_zone(self, t: TableGeom, camera_id: str) -> Zone:
        x0, y0, x1, y1 = t.rect
        m = self.zone_margin
        pts = [(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)]
        return Zone(
            t.id,
            camera_id,
            tuple((round(max(0, min(1, x / self.width)), 4), round(max(0, min(1, y / self.height)), 4)) for x, y in pts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "width_m": self.width,
            "height_m": self.height,
            "areas": self.areas,
            "fixtures": self.fixtures,
            "tables": [
                {
                    "id": t.id,
                    "label": t.label,
                    "area": t.area,
                    "seats": t.seats,
                    "center": list(t.center),
                    "size": list(t.size),
                    "shape": t.shape,
                    "chairs": [list(p) for p in t.seat_positions()],
                }
                for t in self.tables
            ],
        }


def load_layout(path: Path | None = None) -> Layout:
    path = path or project_root() / "configs" / "restaurant.yaml"
    raw = yaml.safe_load(path.read_text())["restaurant"]
    tables = [
        TableGeom(
            id=str(t["id"]),
            label=str(t.get("label", t["id"])),
            area=str(t.get("area", "Main")),
            seats=int(t.get("seats", 4)),
            center=(float(t["center"][0]), float(t["center"][1])),
            size=(float(t["size"][0]), float(t["size"][1])),
            shape=str(t.get("shape", "rect")),
            staff_side=t.get("staff_side"),
        )
        for t in raw["tables"]
    ]
    return Layout(
        id=str(raw["id"]),
        name=str(raw["name"]),
        width=float(raw["floor"]["width_m"]),
        height=float(raw["floor"]["height_m"]),
        zone_margin=float(raw.get("zone_margin_m", 0.9)),
        tables=tables,
        areas=list(raw.get("areas", [])),
        fixtures=list(raw.get("fixtures", [])),
        points=dict(raw.get("points", {})),
    )


def load_zone_file(path: Path, camera_id: str | None = None) -> tuple[str, str, list[Zone]]:
    """Read a zones JSON file. Returns (camera_id, anchor, zones)."""
    data = json.loads(Path(path).read_text())
    cam = camera_id or data["camera_id"]
    return cam, data.get("anchor", "bottom_center"), [Zone.from_dict(z, cam) for z in data["zones"]]
