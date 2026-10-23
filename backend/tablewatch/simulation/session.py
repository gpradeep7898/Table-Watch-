"""One simulation run: simulator + engine + virtual clock (+ optional store)."""

from __future__ import annotations

import logging
from typing import Callable, Iterable

from ..config.layout import Layout
from ..core.clock import VirtualClock
from ..core.engine import RestaurantEngine
from ..core.models import ActionError, Event, Source, TableState
from ..core.thresholds import Thresholds
from ..core.zones import Zone
from ..storage.db import Store
from .scenarios import Scenario
from .simulator import SimStep, Simulator

log = logging.getLogger("tablewatch.sim")

SIM_CAMERA_ID = "sim-cam-1"


class EngineView:
    """Read-only window the simulator uses to react to the dashboard."""

    def __init__(self, engine: RestaurantEngine) -> None:
        self.engine = engine

    def table_state(self, table_id: str) -> TableState:
        return self.engine.tables[table_id].state

    def active_alerts(self) -> list[tuple[str, str | None]]:
        return [(a.type.value, a.table_id) for a in self.engine.alerts.active()]


class SimSession:
    def __init__(
        self,
        layout: Layout,
        scenario: Scenario,
        thresholds: Thresholds,
        zones: Iterable[Zone],
        anchor: str,
        run_id: str,
        seed: int | None = None,
        staff_auto_confirm: bool | None = None,
        store: Store | None = None,
        dataset: str = "simulation",
        on_event: Callable[[Event], None] | None = None,
    ) -> None:
        self.layout = layout
        self.scenario = scenario
        self.run_id = run_id
        self.dataset = dataset
        self.store = store
        self.clock = VirtualClock(epoch=scenario.start_time)
        self.engine = RestaurantEngine(
            layout.table_infos(), thresholds, wall=self.clock.wall, session_prefix=f"{run_id}:"
        )
        self.engine.register_camera(SIM_CAMERA_ID, list(zones), anchor, Source.SIMULATOR, t=0.0)
        self.sim = Simulator(layout, scenario, SIM_CAMERA_ID, seed=seed, staff_auto_confirm=staff_auto_confirm)
        self.view = EngineView(self.engine)
        self.last: SimStep | None = None
        self.sim_actions = 0
        self.rejected_sim_actions = 0
        if store is not None:
            self.engine.subscribe(lambda ev: store.append_event(ev, dataset, run_id))
        if on_event is not None:
            self.engine.subscribe(on_event)

    @property
    def t(self) -> float:
        """Simulated time of the most recent completed step."""
        return self.last.t if self.last else 0.0

    @property
    def finished(self) -> bool:
        return self.sim.finished

    def step(self) -> SimStep:
        st = self.sim.step(self.view)
        if st.frame is not None:
            self.engine.ingest(st.frame)
        for action in st.actions:
            try:
                self.engine.act(action)
                self.sim_actions += 1
            except ActionError as exc:  # the operator got there first
                self.rejected_sim_actions += 1
                log.debug("simulated action skipped: %s", exc)
        self.engine.tick(st.t)
        self.last = st
        return st

    def catch_up(self) -> int:
        """Run steps until the simulator reaches the clock. Returns steps run."""
        n = 0
        while self.sim.t + 1e-9 <= self.clock.now() and not self.finished:
            self.step()
            n += 1
        return n

    def run_headless(self, until: float | None = None) -> int:
        """Run as fast as possible (tests, history generation)."""
        end = self.scenario.duration_s if until is None else until
        n = 0
        while self.sim.t < end - 1e-9:
            self.step()
            n += 1
        self.clock.advance_sim(max(0.0, self.sim.t - self.clock.now()))
        return n
