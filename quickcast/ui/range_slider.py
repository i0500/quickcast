"""Dual-thumb range slider — matches the original HTML's HP/MP look.

  ━━●━━━━━━━━━━━━●━━━
       (filled track between the two thumbs)

Drag either thumb to adjust the corresponding endpoint. Emits
rangeChanged(min, max) on every change. Designed to be paired with two
small number-only entry boxes for precise typing.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class RangeSlider(QWidget):
    rangeChanged = Signal(int, int)   # (min_val, max_val)

    THUMB_R = 7         # thumb radius (px)
    TRACK_H = 4         # inactive track height
    PAD = 10            # left/right padding

    def __init__(
        self,
        minimum: int = 0,
        maximum: int = 100,
        lo: int = 0,
        hi: int = 100,
        fill_color: str = "#ef5350",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._lo = max(minimum, min(maximum, lo))
        self._hi = max(self._lo, min(maximum, hi))
        self._fill = QColor(fill_color)
        self._track_color = QColor("#374151")
        self._thumb_color = QColor("#ffffff")

        self._dragging: str | None = None  # "lo" / "hi" / None
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    # ───────── public API ─────────
    def values(self) -> tuple[int, int]:
        return self._lo, self._hi

    def set_values(self, lo: int, hi: int) -> None:
        lo = max(self._min, min(self._max, lo))
        hi = max(lo, min(self._max, hi))
        if (lo, hi) == (self._lo, self._hi):
            return
        self._lo, self._hi = lo, hi
        self.update()
        self.rangeChanged.emit(self._lo, self._hi)

    def set_fill_color(self, color: str) -> None:
        self._fill = QColor(color)
        self.update()

    # ───────── geometry ─────────
    def _track_rect(self) -> QRect:
        h = self.height()
        return QRect(
            self.PAD,
            (h - self.TRACK_H) // 2,
            max(0, self.width() - self.PAD * 2),
            self.TRACK_H,
        )

    def _value_to_x(self, v: int) -> int:
        tr = self._track_rect()
        if self._max == self._min:
            return tr.left()
        ratio = (v - self._min) / (self._max - self._min)
        return tr.left() + int(round(ratio * tr.width()))

    def _x_to_value(self, x: int) -> int:
        tr = self._track_rect()
        if tr.width() <= 0:
            return self._min
        ratio = (x - tr.left()) / tr.width()
        ratio = max(0.0, min(1.0, ratio))
        return int(round(self._min + ratio * (self._max - self._min)))

    # ───────── mouse handling ─────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.LeftButton:
            return
        x = int(e.position().x())
        lo_x = self._value_to_x(self._lo)
        hi_x = self._value_to_x(self._hi)
        # Pick the closer thumb
        if abs(x - lo_x) <= abs(x - hi_x):
            self._dragging = "lo"
        else:
            self._dragging = "hi"
        self._update_from_x(x)
        e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._dragging is None:
            # Hover cursor change
            x = int(e.position().x())
            lo_x = self._value_to_x(self._lo)
            hi_x = self._value_to_x(self._hi)
            near = (abs(x - lo_x) <= self.THUMB_R + 2) or (abs(x - hi_x) <= self.THUMB_R + 2)
            self.setCursor(Qt.PointingHandCursor if near else Qt.ArrowCursor)
            return
        self._update_from_x(int(e.position().x()))
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = None
            e.accept()

    def _update_from_x(self, x: int) -> None:
        v = self._x_to_value(x)
        if self._dragging == "lo":
            self.set_values(min(v, self._hi), self._hi)
        else:
            self.set_values(self._lo, max(v, self._lo))

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        tr = self._track_rect()
        # Inactive track
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(self._track_color))
        p.drawRoundedRect(tr, self.TRACK_H / 2, self.TRACK_H / 2)

        # Active fill
        lo_x = self._value_to_x(self._lo)
        hi_x = self._value_to_x(self._hi)
        fill_rect = QRect(lo_x, tr.top(), max(0, hi_x - lo_x), tr.height())
        p.setBrush(QBrush(self._fill))
        p.drawRoundedRect(fill_rect, self.TRACK_H / 2, self.TRACK_H / 2)

        # Thumbs
        for x in (lo_x, hi_x):
            cx = x; cy = self.height() // 2
            # White knob with subtle ring
            p.setBrush(QBrush(self._thumb_color))
            p.setPen(QPen(QColor(0, 0, 0, 80), 1))
            p.drawEllipse(cx - self.THUMB_R, cy - self.THUMB_R,
                          self.THUMB_R * 2, self.THUMB_R * 2)


__all__ = ["RangeSlider"]
