"""Settings: Pydantic models, JSON persistence, localStorage migration.

Mirrors the structure of the original `userDataV2` blob so existing user
configs can be migrated 1:1.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

DATA_DIR = Path(__file__).resolve().parent / "data"
def _resolve_config_path() -> Path:
    """Pick a writable path for `userdata.json`.

    PyInstaller --onefile extracts the bundle into a temp directory that
    is read-only at runtime, so writing back to `quickcast/data/...`
    silently fails (or worse, is wiped on next launch). When frozen we
    use `%LOCALAPPDATA%\\QuickCast\\userdata.json`; in development we
    keep the in-tree path so the dev loop is convenient.
    """
    import os, sys
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "QuickCast" / "userdata.json"
    return DATA_DIR / "userdata.json"


CONFIG_PATH = _resolve_config_path()


class Range(BaseModel):
    """Inclusive HP/MP percentage range."""
    min: int = Field(0, ge=0, le=100)
    max: int = Field(100, ge=0, le=100)

    @field_validator("max")
    @classmethod
    def _max_ge_min(cls, v: int, info) -> int:
        if v < info.data.get("min", 0):
            raise ValueError("max must be >= min")
        return v


class Point(BaseModel):
    x: int = 0
    y: int = 0


class Slot(BaseModel):
    """Generic action slot (1~9, 0, and dynamically added 11+)."""
    label: str
    use: bool = False
    hp: Range = Field(default_factory=Range)
    mp: Range = Field(default_factory=Range)
    key: str = "0"
    count: int = 1
    delay: float = 0.2          # seconds between key repeats
    cooltime: float = 5.0       # seconds before slot can fire again
    repeat: bool = True
    tele_use: bool = False      # send Telegram on fire


class PkSlot(BaseModel):
    """PK detection — fires when PK template matches above threshold."""
    use: bool = False
    # HP-range gate: only fire if HP is within. 0..50 = "PK 떴는데 HP
    # 50% 이하면 회피/패시브 반응" 기본값 (사용자 보정값).
    hp: Range = Field(default_factory=lambda: Range(min=0, max=50))
    key: str = "0"
    count: int = 3
    delay: float = 0.2
    cooltime: float = 3.0
    repeat: bool = True
    cap: Point = Field(default_factory=lambda: Point(x=1089, y=561))
    cap_w: int = 25
    cap_h: int = 25
    threshold: int = 3_050_000


class PotionSlot(BaseModel):
    """Potion-empty detection — fires once when potion icon matches."""
    use: bool = False
    # 0..30 = "물약 비었음 + HP 30% 이하"에서만 응답 (사용자 보정값).
    hp: Range = Field(default_factory=lambda: Range(min=0, max=30))
    key: str = "0"
    count: int = 3
    delay: float = 0.2
    cap: Point = Field(default_factory=lambda: Point(x=503, y=646))
    cap_w: int = 13
    cap_h: int = 13
    # NORMED template-match (recognition.py) returns 0..250_000 for
    # potion. 110_000 ≈ 44% NORMED — calibrated against the real game
    # icon at 1280×720 client resolution.
    threshold: int = 110_000


class Alarm(BaseModel):
    id: str
    label: str
    hour: int = 0
    minute: int = 0
    enabled: bool = True
    repeat_minutes: int = 0     # 0 = one-shot
    # Day selection (0=Sun..6=Sat). Empty list = every day.
    days: list[int] = Field(default_factory=list)
    # "once" fires only the first matching weekday occurrence; "repeat"
    # fires every matching occurrence.
    mode: str = "repeat"


class RecoveryStep(BaseModel):
    """One step in the town-return recovery sequence — either a click
    at (x, y) or a key press if `key` is non-empty."""
    label: str = ""
    x: int = 0                  # game-window-relative pixel coords (click)
    y: int = 0
    key: str = ""               # if set, send this key instead of clicking
    delay_after_ms: int = 500   # wait this long before the next step


class RecoverySettings(BaseModel):
    """Auto-return-to-hunt sequence runner.

    When the configured trigger condition fires (potion empty / PK / HP 0)
    the controller waits `start_delay_seconds` for the town-return
    animation to settle, then spawns a thread that clicks each
    `RecoveryStep` in order against the captured game window. While
    running, normal slot fires are suppressed so the sequence isn't
    interrupted.
    """
    enabled: bool = False
    trigger_potion: bool = True
    trigger_pk: bool = False
    trigger_hp_zero: bool = False
    # Slot IDs whose firing also kicks off the recovery sequence. Lets
    # the user wire any slot (e.g. a manually-cast town-return skill, or
    # a specific death-recovery hotkey) into the recovery flow.
    trigger_slot_ids: list[str] = Field(default_factory=list)
    # How long to wait AFTER a trigger before starting the click sequence.
    # The original macro paused 2-5 minutes here so the player's character
    # could finish auto-returning to town and the loading screen / safe-
    # zone buff timer would clear.
    start_delay_seconds: int = 120
    # cooldown_seconds removed — recovery is edge-triggered now (one
    # fire per trigger event until the source condition releases).
    # Field kept here as alias for backward-compat JSON loads.
    cooldown_seconds: int = 0
    steps: list[RecoveryStep] = Field(default_factory=list)


class Settings(BaseModel):
    """Top-level configuration mirroring the original `param` object."""
    # Connection meta
    arduino_port: str = ""
    arduino_baud: int = 9600
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Master switch always starts OFF after reload (safety)
    master_switch: bool = False
    sura_mode: bool = False     # offsets HP/MP capture y-coordinates

    # HP/MP capture regions — calibrated defaults from real-game testing
    # at 1280×720 client resolution. Users can re-drag in the dashboard
    # if their UI scale differs.
    hp_cap: Point = Field(default_factory=lambda: Point(x=78, y=24))
    hp_cap_w: int = 160
    hp_cap_h: int = 5
    mp_cap: Point = Field(default_factory=lambda: Point(x=76, y=35))
    mp_cap_w: int = 157
    mp_cap_h: int = 6

    # Slots — keyed by string id ("1".."9", "0", "11"+)
    slots: dict[str, Slot] = Field(default_factory=dict)
    pk: PkSlot = Field(default_factory=PkSlot)
    potion: PotionSlot = Field(default_factory=PotionSlot)

    # Alarms
    alarms: list[Alarm] = Field(default_factory=list)
    alarm_popup_enabled: bool = True
    alarm_auto_close_minutes: int = 10
    alarm_repeat_minutes: int = 1
    # Sound: "default" (Windows beep), "off" (silent), or absolute path to .wav
    alarm_sound: str = "default"
    alarm_sound_volume: int = 80    # 0..100, applied to QSoundEffect output

    # Misc
    capture_fps: int = 10           # 5..30 — UI ComboBox enforces the cap
    theme: str = "dark"           # see quickcast.ui.design.themes.THEMES
    game_window_patterns: list[str] = Field(
        default_factory=lambda: ["리니지W", "Lineage W", "LINEAGE", "퍼플", "PURPLE"]
    )

    # Capture target — when capture_window_title is set, use WindowCapture
    # against the matching HWND; otherwise fall back to monitor capture.
    capture_window_title: str = ""
    capture_monitor_index: int = 1
    # When True, ROI overlays in the dashboard preview can't be moved or
    # resized by dragging. Lets users park calibrated coordinates and
    # avoid accidental edits during gameplay.
    roi_locked: bool = False

    # Town-return recovery sequence — clicks a preset list of points to
    # navigate back to hunting after a forced return event.
    recovery: "RecoverySettings" = Field(default_factory=lambda: RecoverySettings())
    notify_on_alarm_toast: bool = True   # Windows tray toast on alarm
    notify_on_action_toast: bool = False  # Optional toast when slot fires

    # Input backend: arduino | postmessage | attachinput
    # arduino:     hardware HID via serial (focus required, safest from anti-cheat)
    # postmessage: Win32 PostMessage (no focus, may be silently blocked)
    # attachinput: AttachThreadInput trick (no focus, brief focus flash, bypasses PM filter)
    # PostMessage is the most universally usable backend (no hardware
    # required, works against background/hidden windows). Users with
    # an Arduino can switch in Settings.
    input_backend: str = "postmessage"
    # Floating switch shown by default — most users want one-click
    # master toggle next to the game window. Saved per-user.
    floater_enabled: bool = True
    # First-run guided tutorial — flips to True after the user
    # completes or skips the overlay walkthrough. Help menu can
    # re-launch it.
    tutorial_completed: bool = False

    # ───────── persistence ─────────
    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Settings":
        if not path.exists():
            settings = cls(slots=_default_slots())
            settings.save(path)
            return settings
        s = cls.model_validate_json(path.read_text(encoding="utf-8"))
        # NORMED migration — recognition.py now caps potion score at
        # 250_000 and pk score at 5_000_000. Saved values from the old
        # raw-TM_CCOEFF era could be way over those ceilings, in which
        # case the threshold can never be reached and the auto-fire
        # silently never happens. Clamp once on load.
        if s.potion.threshold > 250_000:
            s.potion.threshold = 150_000
        if s.pk.threshold > 5_000_000:
            s.pk.threshold = 3_000_000
        return s

    @classmethod
    def from_legacy(cls, legacy_json: str | dict[str, Any]) -> "Settings":
        """Convert browser localStorage `userDataV2` -> Settings."""
        data = json.loads(legacy_json) if isinstance(legacy_json, str) else legacy_json
        state = data.get("state", data)  # tweakpane wraps under .state

        slots: dict[str, Slot] = {}
        for sid in [*"1234567890"] + [str(i) for i in range(11, 100)]:
            key = f"slot{sid}"
            if f"{key}Use" not in state:
                continue
            slots[sid] = Slot(
                label=state.get(f"{key}Label", f"SLOT-{sid}"),
                use=state.get(f"{key}Use", False),
                hp=Range(**state.get(f"{key}Hp", {"min": 0, "max": 100})),
                mp=Range(**state.get(f"{key}Mp", {"min": 0, "max": 100})),
                key=str(state.get(f"{key}Key", "0")),
                count=int(state.get(f"{key}Count", 1)),
                delay=float(state.get(f"{key}Delay", 0.2)),
                cooltime=float(state.get(f"{key}Cooltime", 5)),
                repeat=bool(state.get(f"{key}Repeat", True)),
                tele_use=bool(state.get(f"{key}TeleUse", False)),
            )

        return cls(
            telegram_token=state.get("teleToken", ""),
            sura_mode=state.get("sura", False),
            hp_cap=Point(**state.get("hpCap", {"x": 90, "y": 32})),
            hp_cap_w=state.get("hpCapW", 190),
            hp_cap_h=state.get("hpCapH", 6),
            mp_cap=Point(**state.get("mpCap", {"x": 90, "y": 45})),
            mp_cap_w=state.get("mpCapW", 190),
            mp_cap_h=state.get("mpCapH", 6),
            slots=slots,
            pk=PkSlot(
                use=state.get("pkUse", False),
                hp=Range(**state.get("pkHp", {"min": 0, "max": 100})),
                key=str(state.get("pkKey", "8")),
                count=int(state.get("pkCount", 2)),
                delay=float(state.get("pkDelay", 0.2)),
                cooltime=float(state.get("pkCooltime", 5)),
                repeat=bool(state.get("pkRepeat", True)),
                cap=Point(**state.get("pkCap", {"x": 1057, "y": 533})),
                cap_w=state.get("pkCapW", 25),
                cap_h=state.get("pkCapH", 25),
                threshold=int(state.get("pkThres", 700_000)),
            ),
            potion=PotionSlot(
                use=state.get("potionUse", False),
                hp=Range(**state.get("potionHp", {"min": 0, "max": 100})),
                key=str(state.get("potionKey", "8")),
                count=int(state.get("potionCount", 2)),
                delay=float(state.get("potionDelay", 0.2)),
                cap=Point(**state.get("potionCap", {"x": 472, "y": 635})),
                cap_w=state.get("potionCapW", 13),
                cap_h=state.get("potionCapH", 13),
                threshold=int(state.get("potionThres", 700_000)),
            ),
            alarms=[_legacy_alarm(a) for a in state.get("alarms", [])],
            alarm_popup_enabled=state.get("alarmPopupEnabled", True),
            alarm_auto_close_minutes=state.get("alarmAutoCloseTime", 10),
            alarm_repeat_minutes=state.get("alarmRepeatInterval", 1),
        )


def _legacy_alarm(a: dict) -> Alarm:
    """Convert one HTML-stored alarm to our Alarm model.

    HTML schema: { id, title, time:'HH:MM', days:[0..6], repeat:bool, triggered:bool }
    Ours:        { id, label, hour, minute, days:[0..6], mode:'once'|'repeat', repeat_minutes }
    """
    if isinstance(a, Alarm):
        return a
    if not isinstance(a, dict):
        return Alarm(id="", label="(invalid)")
    raw_time = a.get("time") or "00:00"
    try:
        hh, mm = raw_time.split(":", 1)
        hour, minute = int(hh), int(mm)
    except Exception:
        hour, minute = 0, 0
    return Alarm(
        id=str(a.get("id", "")),
        label=str(a.get("label") or a.get("title") or "알림"),
        hour=hour, minute=minute,
        enabled=bool(a.get("enabled", True)),
        repeat_minutes=int(a.get("repeat_minutes", 0)),
        days=list(a.get("days", [])),
        mode="repeat" if a.get("repeat", a.get("mode") == "repeat") else "once",
    )


def _default_slots() -> dict[str, Slot]:
    """Match the original default slot layout."""
    defaults = {
        "1": Slot(label="SLOT-1", hp=Range(min=0, max=30), mp=Range(min=0, max=100),
                  key="8", count=4, cooltime=5),
        "2": Slot(label="SLOT-2", hp=Range(min=0, max=100), mp=Range(min=0, max=20),
                  key="8", count=4, cooltime=5),
        "3": Slot(label="SLOT-3", hp=Range(min=0, max=80), mp=Range(min=0, max=20),
                  key="4", count=1, cooltime=5),
        "4": Slot(label="SLOT-4", key="f", count=1, cooltime=0.2),
        "5": Slot(label="SLOT-5", key="f", count=1, cooltime=0.2),
        "6": Slot(label="SLOT-6", key="0", count=1, cooltime=0.2),
        "7": Slot(label="SLOT-7", key="0", count=1, cooltime=0.2),
        "8": Slot(label="SLOT-8", key="0", count=1, cooltime=0.2),
        "9": Slot(label="SLOT-9", key="0", count=1, cooltime=0.2),
        "0": Slot(label="SLOT-0", key="0", count=1, cooltime=0.2),
    }
    return defaults


__all__ = [
    "Range", "Point", "Slot", "PkSlot", "PotionSlot",
    "Alarm", "Settings", "CONFIG_PATH", "DATA_DIR",
]
