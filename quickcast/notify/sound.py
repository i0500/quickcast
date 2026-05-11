"""Cross-thread-safe Windows alarm sounds.

Built-in sounds use `winsound.Beep` patterns (no extra files required —
works inside the PyInstaller bundle) plus a few `MessageBeep` system
sounds. Custom .wav paths route through QSoundEffect so the user can
drop in their own files.
"""
from __future__ import annotations

import threading
import time

try:
    import winsound
    _HAS_WINSOUND = True
except ImportError:  # non-Windows dev environment
    _HAS_WINSOUND = False


# ── Public catalogue (id, label) — referenced by the alerts UI combobox ──
SOUND_PRESETS: list[tuple[str, str]] = [
    ("off",          "끔"),
    ("default",      "기본 (시스템 알림)"),
    ("chime",        "차임 (3음 상승)"),
    ("double",       "두 번 비프"),
    ("triple",       "세 번 비프"),
    ("bell",         "벨 (장음)"),
    ("siren",        "사이렌 (교대)"),
    ("pulse",        "펄스 (빠른 연타)"),
    ("warning",      "경고 (시스템)"),
    ("question",     "확인음 (시스템)"),
    ("classic",      "클래식 (800-1000)"),
]


def preset_ids() -> list[str]:
    return [sid for sid, _ in SOUND_PRESETS]


def preset_label(sid: str) -> str:
    for k, v in SOUND_PRESETS:
        if k == sid:
            return v
    return sid


# ── Pattern players (each runs on a worker thread) ─────────────────────

def _play_pattern(sid: str) -> None:
    """Play a single shot of the named built-in pattern."""
    if not _HAS_WINSOUND:
        return
    try:
        if sid == "default":
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif sid == "warning":
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif sid == "question":
            winsound.MessageBeep(winsound.MB_ICONQUESTION)
        elif sid == "chime":
            for hz in (800, 1000, 1200):
                winsound.Beep(hz, 180)
                time.sleep(0.04)
        elif sid == "double":
            for _ in range(2):
                winsound.Beep(1200, 120)
                time.sleep(0.08)
        elif sid == "triple":
            for _ in range(3):
                winsound.Beep(1100, 100)
                time.sleep(0.08)
        elif sid == "bell":
            winsound.Beep(900, 700)
        elif sid == "siren":
            for _ in range(3):
                winsound.Beep(600, 200)
                winsound.Beep(1200, 200)
        elif sid == "pulse":
            for _ in range(5):
                winsound.Beep(900, 80)
                time.sleep(0.05)
        elif sid == "classic":
            winsound.Beep(800, 500)
            time.sleep(0.1)
            winsound.Beep(1000, 500)
        else:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except RuntimeError:
        pass


def play_alarm(times: int = 5, interval_s: float = 2.0,
                sound_id: str = "classic") -> None:
    """Fire-and-forget alarm sequence — repeats `times` at `interval_s`."""
    threading.Thread(
        target=_repeat_pattern,
        args=(times, interval_s, sound_id),
        daemon=True,
    ).start()


def _repeat_pattern(times: int, interval_s: float, sid: str) -> None:
    if sid == "off":
        return
    for i in range(max(1, times)):
        _play_pattern(sid)
        if i < times - 1:
            time.sleep(max(0.0, interval_s))


_QT_EFFECT = None    # lazily-created QSoundEffect for custom .wav paths


def play_once(sound_id: str = "default", volume: int = 80) -> None:
    """Play one alarm sound — used by the settings test button.

    `sound_id` ∈ {built-in id from SOUND_PRESETS, absolute .wav path}.
    Runs on the Qt event-loop thread for custom files (QSoundEffect),
    or on a background thread for winsound patterns.
    """
    from quickcast.utils.logger import logger
    sid = (sound_id or "default").strip()
    logger.info(f"sound: play_once requested sid={sid!r} volume={volume} winsound={_HAS_WINSOUND}")
    if sid == "off":
        logger.info("sound: skipped (off)")
        return

    # Built-in preset?
    if sid in preset_ids():
        if not _HAS_WINSOUND:
            logger.warning("sound: winsound unavailable on this platform")
            return
        threading.Thread(target=_play_pattern, args=(sid,), daemon=True).start()
        return

    # Else assume custom file path.
    from pathlib import Path
    p = Path(sid)
    if not (p.exists() and p.is_file()):
        # Bad path → fall back to system beep so the user hears *something*.
        threading.Thread(target=_play_pattern, args=("default",), daemon=True).start()
        return
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect
    except Exception:
        return
    global _QT_EFFECT
    if _QT_EFFECT is None:
        _QT_EFFECT = QSoundEffect()
    _QT_EFFECT.setSource(QUrl.fromLocalFile(str(p)))
    _QT_EFFECT.setVolume(max(0.0, min(1.0, volume / 100.0)))
    _QT_EFFECT.play()


__all__ = ["play_alarm", "play_once", "SOUND_PRESETS",
            "preset_ids", "preset_label"]
