"""iOS-style toggle switch — pill background + sliding white knob.

Animates between the off (red) and on (green) states like the iOS UI.
Used as the floating master switch and any other place we need a clean
two-state toggle.
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, Signal,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class IOSToggle(QWidget):
    """Square-cornered pill switch. Click to toggle, animates the knob."""

    toggled = Signal(bool)

    def __init__(
        self,
        width: int = 60,
        height: int = 32,
        on_color: str = "#34c759",   # iOS green
        off_color: str = "#ff3b30",  # iOS red
        knob_color: str = "#ffffff",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._w = width
        self._h = height
        self._on_color = QColor(on_color)
        self._off_color = QColor(off_color)
        self._knob_color = QColor(knob_color)
        self._is_on = False
        # _pos goes 0..1 representing knob travel
        self._pos = 0.0
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"position", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    # ───────── animated property ─────────
    def get_position(self) -> float:
        return self._pos

    def set_position(self, value: float) -> None:
        self._pos = float(max(0.0, min(1.0, value)))
        self.update()

    position = Property(float, get_position, set_position)

    # ───────── public API ─────────
    def is_on(self) -> bool:
        return self._is_on

    def set_state(self, on: bool, animate: bool = True) -> None:
        if on == self._is_on:
            return
        self._is_on = on
        target = 1.0 if on else 0.0
        # Honour the global "reduce motion" accessibility preference.
        try:
            from quickcast.ui.design.prefs import prefs
            if prefs.reduce_motion:
                animate = False
        except Exception:
            pass
        if animate:
            self._anim.stop()
            self._anim.setStartValue(self._pos)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._pos = target
            self.update()

    # ───────── interaction ─────────
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self.set_state(not self._is_on)
            self.toggled.emit(self._is_on)
            e.accept()
        else:
            super().mousePressEvent(e)

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        radius = self._h / 2.0

        # Interpolate background colour between off and on
        bg = QColor(
            int(self._off_color.red()   + (self._on_color.red()   - self._off_color.red())   * self._pos),
            int(self._off_color.green() + (self._on_color.green() - self._off_color.green()) * self._pos),
            int(self._off_color.blue()  + (self._on_color.blue()  - self._off_color.blue())  * self._pos),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(0, 0, self._w, self._h), radius, radius)

        # Knob
        knob_d = self._h - 4
        knob_x = 2 + (self._w - knob_d - 4) * self._pos
        p.setBrush(QBrush(self._knob_color))
        p.drawEllipse(QRectF(knob_x, 2, knob_d, knob_d))

        # Subtle shadow ring on the knob
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(knob_x, 2, knob_d, knob_d))


__all__ = ["IOSToggle"]
