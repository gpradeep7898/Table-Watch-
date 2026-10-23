"""Generate a synthetic week of restaurant history for the analytics page.

Each lunch and dinner service is its own simulation run, driven through the
same simulator and engine as the live demo, and stored in the
``simulation`` dataset with kind ``history``. It is labelled synthetic
everywhere. It exists to exercise the analytics, not to predict anything.
"""

from __future__ import annotations

import math
import random
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..config.layout import Layout
from ..core.thresholds import Thresholds
from ..core.zones import Zone
from ..storage.db import Store
from .scenarios import PartySpec, Scenario
from .session import SimSession

SERVICES = [("Lunch", (11, 30), 3.0, 2.2), ("Dinner", (17, 30), 4.5, 3.4)]  # name, start, hours, peak parties/10min
WEEKDAY_FACTOR = [0.6, 0.65, 0.75, 0.85, 1.15, 1.3, 0.95]
SIZE_WEIGHTS = [(2, 45), (3, 15), (4, 25), (5, 8), (6, 7)]


def build_service(day: date, name: str, start: tuple[int, int], hours: float, peak: float, rng: random.Random) -> Scenario:
    weekday = day.weekday()
    factor = WEEKDAY_FACTOR[weekday]
    parties: list[PartySpec] = []
    t = 0.0
    last_seating = hours * 3600 - 1800
    while t < last_seating:
        # Arrival intensity peaks one hour into the service.
        phase = t / 3600.0
        shape = math.exp(-((phase - 1.0) ** 2) / 0.9)
        rate_per_s = max(0.0002, peak * factor * shape / 600.0)
        t += rng.expovariate(rate_per_s)
        if t >= last_seating:
            break
        size = rng.choices([s for s, _ in SIZE_WEIGHTS], [w for _, w in SIZE_WEIGHTS])[0]
        forgotten = rng.random() < 0.04
        slow_clean = rng.random() < 0.06
        dine_min = rng.gauss(45, 10) if name == "Lunch" else rng.gauss(70, 15)
        parties.append(PartySpec(
            table=None, size=size, arrive_s=round(t, 1),
            service_delay_s=None if forgotten else round(min(400, max(20, rng.gauss(70, 35))), 1),
            dine_s=round(max(15, dine_min) * 60, 1),
            cleaning_delay_s=None if slow_clean else round(min(600, rng.expovariate(1 / 110)), 1),
        ))
    waiters = 2 if name == "Lunch" else (4 if weekday == 5 else 3)
    return Scenario(
        id="history", name=f"Synthetic history: {day:%a %d %b} {name.lower()}", summary="", description="",
        seed=rng.randrange(1 << 30), start_time=datetime(day.year, day.month, day.day, *start),
        duration_s=hours * 3600, waiters=waiters, parties=tuple(parties),
        confirm_lag_s=(6.0, 30.0), alert_response_lag_s=(30.0, 90.0),
    )


@dataclass
class HistoryJob:
    days: int
    total: int = 0
    done: int = 0
    status: str = "idle"  # idle, running, done, error
    error: str | None = None
    runs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"days": self.days, "total": self.total, "done": self.done, "status": self.status,
                "error": self.error, "runs": len(self.runs)}


def generate_history(store: Store, layout: Layout, thresholds: Thresholds, zones: list[Zone], anchor: str,
                     days: int = 7, end_day: date | None = None, seed: int = 2026, job: HistoryJob | None = None,
                     dt: float = 1.0) -> HistoryJob:
    job = job or HistoryJob(days)
    rng = random.Random(seed)
    end_day = end_day or (date.today() - timedelta(days=1))
    plan = []
    for i in range(days):
        day = end_day - timedelta(days=days - 1 - i)
        for name, start, hours, peak in SERVICES:
            plan.append(build_service(day, name, start, hours, peak, rng))
    job.total = len(plan)
    job.status = "running"
    try:
        for sc in plan:
            run_id = f"hist-{sc.start_time:%Y%m%d-%H%M}-{seed}"
            if store.run(run_id):
                store.delete_dataset("simulation", run_id)  # regenerate the same synthetic service
            store.create_run(run_id, "simulation", "history", epoch_wall=sc.start_time.timestamp(), label=sc.name,
                             scenario="history", seed=sc.seed, meta={"synthetic": True, "thresholds": thresholds.to_dict()})
            s = SimSession(layout, sc, thresholds, zones, anchor, run_id, store=store)
            s.sim.dt = dt
            end = sc.duration_s
            while s.sim.t < end:
                with store.batch():
                    s.run_headless(until=min(end, s.sim.t + 600))
            store.touch_run(run_id, s.t, s.clock.wall(s.t))
            store.end_run(run_id, "completed")
            job.runs.append(run_id)
            job.done += 1
        job.status = "done"
    except Exception as exc:  # pragma: no cover - surfaced to the UI
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
    return job


def start_history_thread(db_path: str, layout: Layout, thresholds: Thresholds, zones: list[Zone], anchor: str,
                         days: int) -> HistoryJob:
    job = HistoryJob(days, status="running")

    def work() -> None:
        store = Store(db_path)  # own connection so the live loop is never blocked for long
        try:
            generate_history(store, layout, thresholds, zones, anchor, days=days, job=job)
        finally:
            store.close()

    threading.Thread(target=work, name="tw-history", daemon=True).start()
    return job
