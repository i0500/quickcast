"""Time-based alarm scheduler.

Original macro polled `checkAlarms` every second; we keep the same
cadence but isolate it to its own thread so capture/control aren't
disturbed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from quickcast.config import Alarm, Settings
from quickcast.utils.logger import logger


@dataclass
class AlarmEvent:
    alarm: Alarm
    fired_at: datetime


class AlarmScheduler:
    """Background poller that fires `on_alarm` once per minute boundary."""

    def __init__(
        self,
        settings: Settings,
        on_alarm: Callable[[AlarmEvent], None],
    ) -> None:
        self.settings = settings
        self.on_alarm = on_alarm
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # (alarm_id, "HH:MM") that we've already fired this minute
        self._last_fired_minute: dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="AlarmScheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        # Python's weekday: Mon=0..Sun=6 — original HTML uses Sun=0..Sat=6.
        # We normalise to original convention so user-stored data round-trips.
        while not self._stop.wait(1.0):
            now = datetime.now()
            current_min = now.strftime("%H:%M")
            current_dow = (now.weekday() + 1) % 7   # Mon=0 → 1; Sun=6 → 0
            for alarm in list(self.settings.alarms):
                if not alarm.enabled:
                    continue
                target = f"{alarm.hour:02d}:{alarm.minute:02d}"
                if current_min != target:
                    continue
                if alarm.days and current_dow not in alarm.days:
                    continue
                day_key = f"{alarm.id}:{now.strftime('%Y-%m-%d')}"
                if alarm.mode == "once" and self._last_fired_minute.get(day_key) == "fired":
                    continue
                if self._last_fired_minute.get(alarm.id) == current_min:
                    continue

                self._last_fired_minute[alarm.id] = current_min
                if alarm.mode == "once":
                    self._last_fired_minute[day_key] = "fired"
                try:
                    self.on_alarm(AlarmEvent(alarm=alarm, fired_at=now))
                except Exception:
                    logger.exception("alarm callback failed")


__all__ = ["AlarmScheduler", "AlarmEvent"]
