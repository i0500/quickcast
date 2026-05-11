"""Accessibility / display preferences.

Live togglable knobs that affect rendering globally:
  - high_contrast : stronger borders + more saturated text
  - large_font    : 140% font scaling
  - mono_accent   : neutralise the brand accent (use text colour)
  - reduce_motion : disable QPropertyAnimation / transition timers

When any pref changes, `bus.theme_changed` re-fires so the QSS regenerates
and custom-paint widgets refresh.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _Prefs(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.high_contrast: bool = False
        self.large_font: bool = False
        self.mono_accent: bool = False
        self.reduce_motion: bool = False

    def set(self, **kw) -> None:
        dirty = False
        for k, v in kw.items():
            if hasattr(self, k) and getattr(self, k) != v:
                setattr(self, k, v)
                dirty = True
        if dirty:
            self.changed.emit()


prefs = _Prefs()


__all__ = ["prefs"]
