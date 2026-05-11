"""Shared mock state for the design preview.

Lets the Dashboard's quick toggles stay in sync with the Slots section's
list rows (and the same for alarms). Real app uses Settings + signals;
this is just enough to make the preview behave like the app will.

Also exposes a single shared `mock_settings` Settings instance so the
Dashboard recognizer + Combat threshold sliders edit the same object.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from quickcast.config import Settings


# Single Settings instance shared between Dashboard preview's recognizer
# and Combat section's threshold/level sliders.
#
# We deliberately use *fresh defaults* — not Settings.load() — because the
# user's saved config can hold ROI sizes (e.g. pk.cap_w=24) that don't
# match the bundled template sizes (25×25), which makes _template_score
# bail out with 0.0 and breaks the live demo. The preview is purely a
# design playground; real values come from the production controller.
mock_settings: Settings = Settings()


def percent_to_threshold(percent: int, kind: str = "pk") -> int:
    """Map LevelSlider percent (20/40/60/80/100) → matchTemplate threshold.

    5-step ranges (lower percent = more sensitive, higher = stricter):
      • PK     — 1M / 2M / 3M / 4M / 5M
      • Potion — 50K / 100K / 150K / 200K / 250K
    PK template peaks at ~3.86M → 80 % (4M) and 100 % (5M) sit above peak,
    so the user can dial in zero-noise detection. Potion peaks at ~132K
    so 60 % (150K) is the inflection point.
    """
    if kind == "potion":
        table = {20: 50_000, 40: 100_000, 60: 150_000, 80: 200_000, 100: 250_000}
    else:
        table = {20: 1_000_000, 40: 2_000_000, 60: 3_000_000, 80: 4_000_000, 100: 5_000_000}
    return table.get(percent, table[60])


# Shared LEVELSLIDER definitions for combat sensitivity sliders.
# 5 evenly-spaced stops. Labels mirror the perceptual progression
# from "very sensitive" → "very strict".
COMBAT_LEVELS = [
    (20,  "매우 민감"),
    (40,  "민감"),
    (60,  "보통"),
    (80,  "엄격"),
    (100, "매우 엄격"),
]


class _SlotState(QObject):
    slot_toggled = Signal(str, bool)   # (slot_id, on)

    def __init__(self) -> None:
        super().__init__()
        self._on: dict[str, bool] = {
            "1": True, "2": True, "3": True, "4": True, "5": True,
            "6": True, "7": False, "8": False, "9": False, "0": False,
        }
        self._label: dict[str, str] = {
            "1": "베르", "2": "그힐", "3": "익힐", "4": "MP회복",
            "5": "생존의외침", "6": "변신마법", "7": "스킨마법",
            "8": "SLOT-8", "9": "SLOT-9", "0": "SLOT-0",
        }
        self._key: dict[str, str] = {
            "1": "0", "2": "`", "3": "1", "4": "F8",
            "5": "F9", "6": "F7", "7": "f",
            "8": "0", "9": "0", "0": "0",
        }

    def order(self) -> list[str]:
        return list(self._on.keys())

    def is_on(self, sid: str) -> bool:
        return self._on.get(sid, False)

    def label(self, sid: str) -> str:
        return self._label.get(sid, sid)

    def key(self, sid: str) -> str:
        return self._key.get(sid, "0")

    def set_on(self, sid: str, on: bool, *, source: object | None = None) -> None:
        if self._on.get(sid) == on:
            return
        self._on[sid] = on
        self.slot_toggled.emit(sid, on)


slot_state = _SlotState()


class _AlarmState(QObject):
    alarm_toggled = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self._on: dict[str, bool] = {
            "혈던": True, "격전": True,
            "어질리티 대회": True, "마족신전": False,
        }

    def is_on(self, name: str) -> bool:
        return self._on.get(name, False)

    def set_on(self, name: str, on: bool) -> None:
        if self._on.get(name) == on:
            return
        self._on[name] = on
        self.alarm_toggled.emit(name, on)

    def order(self) -> list[str]:
        return list(self._on.keys())


alarm_state = _AlarmState()


__all__ = ["slot_state", "alarm_state"]
