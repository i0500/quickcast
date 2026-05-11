"""Runtime state shared between controller threads and the UI."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from quickcast.core.recognition import FrameAnalysis


@dataclass
class RuntimeState:
    """Mutable state observed by the UI; controller writes, UI reads."""
    capture_connected: bool = False
    arduino_connected: bool = False
    telegram_connected: bool = False

    last_analysis: Optional[FrameAnalysis] = None
    last_frame_ts: float = 0.0

    # The 3-second grace period after master switch ON; controlLoop waits.
    master_switch_activated_at: float = 0.0
    # Town-return recovery state — flag is True while the click sequence
    # is mid-execution. Slot fires are suppressed in that window. The
    # `recovery_stop` event is set by the controller when master flips
    # OFF mid-sequence so the runner thread aborts immediately.
    recovery_in_progress: bool = False
    _last_recovery_at: float = 0.0
    recovery_stop: threading.Event = field(
        default_factory=threading.Event, repr=False,
    )
    # Edge-triggered latch — keys ("potion" / "pk" / "hp_zero" /
    # "slot:N") added when recovery fires from that source. Cleared
    # only when the condition releases (e.g. potion_empty=False),
    # forcing a fresh re-arm before recovery fires again.
    recovery_handled: set = field(default_factory=set, repr=False)
    # Auto-pause when the game window is minimised. Updated each
    # control tick from `IsIconic(hwnd)`; when True, slot evaluation
    # is suppressed (the captured frame is stale anyway because the
    # game stops rendering while iconified).
    window_minimized: bool = False

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update_analysis(self, analysis: FrameAnalysis) -> None:
        with self._lock:
            self.last_analysis = analysis
            self.last_frame_ts = time.monotonic()

    def begin_master_grace(self, seconds: float = 3.0) -> None:
        self.master_switch_activated_at = time.monotonic() + seconds

    def end_master_grace(self) -> None:
        self.master_switch_activated_at = 0.0

    def in_grace_period(self) -> bool:
        return time.monotonic() < self.master_switch_activated_at


__all__ = ["RuntimeState"]
