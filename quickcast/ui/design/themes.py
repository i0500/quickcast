"""Theme registry + apply_theme() — the entry point for UI styling.

Drops the legacy 5-theme set in favour of two real palettes (Graphite,
Paper) plus an Auto mode that follows OS dark mode. Legacy theme ids
fall back to Graphite with a one-shot warning so existing settings.json
files keep loading.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from quickcast.ui.design.prefs import prefs
from quickcast.ui.design.qss_template import render
from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import (
    GRAPHITE_TOKENS, PAPER_TOKENS, T, TokenSet,
)
from quickcast.utils.logger import logger


THEMES: dict[str, TokenSet] = {
    "graphite": GRAPHITE_TOKENS,
    "paper": PAPER_TOKENS,
}

# Legacy ids → fallback target
_LEGACY = {
    "dark": "graphite",
    "midnight": "graphite",
    "high_contrast": "graphite",
    "solarized": "graphite",
    "light": "paper",
}

_warned_legacy: set[str] = set()


def _detect_os_theme() -> str:
    """'graphite' on dark OS, 'paper' otherwise. Falls back to graphite."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "paper"
        return "graphite"
    except Exception:
        return "graphite"


def resolve_theme_id(theme_id: str) -> str:
    """Map any incoming id (auto / legacy / current) to a known THEMES key."""
    tid = (theme_id or "").lower().strip()
    if tid in ("", "auto"):
        return _detect_os_theme()
    if tid in THEMES:
        return tid
    if tid in _LEGACY:
        target = _LEGACY[tid]
        if tid not in _warned_legacy:
            logger.warning(f"theme '{tid}' is legacy → falling back to '{target}'")
            _warned_legacy.add(tid)
        return target
    logger.warning(f"unknown theme '{tid}' → 'graphite'")
    return "graphite"


def current_theme() -> TokenSet:
    return THEMES.get(T.name.lower(), GRAPHITE_TOKENS)


def apply_theme(app: QApplication, theme_id: str) -> TokenSet:
    """Activate a theme: swap tokens, regenerate QSS, broadcast signal."""
    resolved = resolve_theme_id(theme_id)
    tokens = THEMES[resolved]
    T.set(tokens)
    qss = render(tokens) + _prefs_overlay_qss(tokens)
    app.setStyleSheet(qss)
    bus.theme_changed.emit()
    logger.info(f"theme applied: {tokens.name} (id='{resolved}')")
    return tokens


def _prefs_overlay_qss(tokens: TokenSet) -> str:
    """Append accessibility preferences as additional QSS rules."""
    p = tokens.palette
    bits: list[str] = []
    if prefs.large_font:
        # Bump every font 40% — affects all widgets via universal selector.
        bits.append(f"* {{ font-size: {round(tokens.type.md * 1.4)}pt; }}")
    if prefs.high_contrast:
        # Stronger borders + brighter primary text.
        bits.append(
            f"QFrame#card, QFrame#sidebar, QFrame#statusBar, QFrame#titleBar,"
            f" QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,"
            f" QTimeEdit, QPushButton {{ border-width: 2px;"
            f" border-color: {p.border_strong}; }}"
            f"QLabel {{ color: {p.text_primary}; }}"
        )
    if prefs.mono_accent:
        # Replace accent with primary text colour everywhere QSS used it.
        bits.append(
            f"QPushButton[variant=\"primary\"] {{"
            f" background:{p.text_primary}; color:{p.bg_canvas}; }}"
            f"QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,"
            f" QSpinBox:focus, QDoubleSpinBox:focus,"
            f" QTimeEdit:focus {{ border-color: {p.text_primary}; }}"
        )
    return "\n".join(bits)


def reapply_current(app: QApplication) -> None:
    """Re-render the active theme's QSS — call when prefs change."""
    apply_theme(app, T.name.lower())


__all__ = [
    "THEMES",
    "apply_theme",
    "current_theme",
    "resolve_theme_id",
]
