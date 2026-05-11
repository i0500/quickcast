"""Meter — HP/MP-style horizontal progress bar with percentage label.

Custom-paint so we get tight control over height, fill colour and label
positioning without QProgressBar's quirks. Subscribes to the design-system
SignalBus so theme swaps repaint correctly.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


class Meter(QWidget):
    def __init__(
        self,
        label: str = "HP",
        *,
        fill_token: str = "hp_fill",
        height: int = 22,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._fill_token = fill_token
        self._value = 0
        self._maximum = 100
        self.setMinimumHeight(height)
        self.setMaximumHeight(height + 6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bus.theme_changed.connect(self.update)

    def set_value(self, v: int) -> None:
        v = max(0, min(self._maximum, int(v)))
        if v != self._value:
            self._value = v
            self.update()

    def set_label(self, text: str) -> None:
        self._label = text
        self.update()

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = T.palette
        radius = 5

        # Track
        p.setPen(QPen(QColor(pal.border_subtle), 1))
        p.setBrush(QBrush(QColor(pal.bg_input)))
        rect = QRectF(0, 0, self.width(), self.height())
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # Fill
        if self._maximum > 0:
            ratio = self._value / self._maximum
            fill_w = max(0.0, rect.width() * ratio - 2)
            fill_color = getattr(pal, self._fill_token, pal.accent_default)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(fill_color)))
            p.drawRoundedRect(
                QRectF(1, 1, fill_w, rect.height() - 2),
                radius - 1, radius - 1,
            )

        # Label + value (centred)
        p.setPen(QColor(pal.text_primary))
        f = QFont(T.type.mono); f.setPointSize(10); f.setBold(True); p.setFont(f)
        text = f"{self._label}  {self._value}%"
        p.drawText(rect, Qt.AlignCenter, text)


__all__ = ["Meter"]
