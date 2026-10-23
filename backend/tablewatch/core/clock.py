"""Pluggable clocks.

Every timer in TableWatch runs on *domain time*: a float number of seconds
since the clock started. Core logic never reads the system clock itself; it
receives domain time from one of these clocks. That keeps the state machine
deterministic and lets simulation time pause, resume and run faster than
real time without any timer drifting.

``wall(t)`` converts domain time into a unix timestamp for persisted events.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    def wall(self, t: float | None = None) -> float: ...


class RealClock:
    """Real elapsed time. Domain time is monotonic seconds since creation."""

    def __init__(self) -> None:
        self._mono0 = time.monotonic()
        self._wall0 = time.time()

    def now(self) -> float:
        return time.monotonic() - self._mono0

    def wall(self, t: float | None = None) -> float:
        return self._wall0 + (self.now() if t is None else t)


class VirtualClock:
    """Simulation clock.

    Time only moves when :meth:`advance` is called with a real elapsed
    duration. Pausing freezes time. Speed multiplies elapsed real time.
    ``epoch`` is the fictional wall-clock moment that corresponds to t=0.
    """

    SPEEDS = (0.0, 1.0, 5.0, 10.0)

    def __init__(self, epoch: datetime | float, speed: float = 1.0) -> None:
        self._epoch = epoch.timestamp() if isinstance(epoch, datetime) else float(epoch)
        self._t = 0.0
        self._speed = 1.0
        self._paused = True
        self.set_speed(speed)

    # -- controls -------------------------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def epoch(self) -> float:
        return self._epoch

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_speed(self, speed: float) -> None:
        if speed < 0 or speed > 100:
            raise ValueError("speed must be between 0 and 100")
        if speed == 0:
            self._paused = True
            return
        self._speed = float(speed)

    def reset(self, epoch: datetime | float | None = None) -> None:
        if epoch is not None:
            self._epoch = epoch.timestamp() if isinstance(epoch, datetime) else float(epoch)
        self._t = 0.0
        self._paused = True

    # -- time -----------------------------------------------------------
    def advance(self, real_dt: float) -> float:
        """Advance by ``real_dt`` real seconds. Returns simulated seconds added."""
        if self._paused or real_dt <= 0:
            return 0.0
        dt = real_dt * self._speed
        self._t += dt
        return dt

    def advance_sim(self, sim_dt: float) -> None:
        """Advance by exact simulated seconds regardless of pause (headless runs)."""
        if sim_dt < 0:
            raise ValueError("cannot move time backwards")
        self._t += sim_dt

    def now(self) -> float:
        return self._t

    def wall(self, t: float | None = None) -> float:
        return self._epoch + (self._t if t is None else t)


class ManualClock:
    """Explicitly driven clock for tests and offline video analysis."""

    def __init__(self, epoch: float = 1_700_000_000.0) -> None:
        self._epoch = epoch
        self._t = 0.0

    def set(self, t: float) -> None:
        if t < self._t:
            raise ValueError("cannot move time backwards")
        self._t = t

    def now(self) -> float:
        return self._t

    def wall(self, t: float | None = None) -> float:
        return self._epoch + (self._t if t is None else t)
