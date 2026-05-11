"""LevelSlider — 4-step horizontal selector for sensitivity / strictness.

  매우 민감 ───●─── 민감 ─── 엄격 ─── 매우 엄격
       25%        50%       75%        100%

Click on a stop or drag the thumb to choose. Emits `level_changed(percent)`
where percent is one of the configured discrete steps.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


DEFAULT_LEVELS = [
    (25,  "매우 민감"),
    (50,  "민감"),
    (75,  "엄격"),
    (100, "매우 엄격"),
]


class LevelSlider(QWidget):
    level_changed = Signal(int)

    def __init__(
        self,
        initial: int = 75,
        levels: list[tuple[int, str]] | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._levels = levels or DEFAULT_LEVELS
        self._percents = [p for p, _ in self._levels]
        self._labels = {p: l for p, l in self._levels}
        self._value = self._snap(initial)

        self.setMinimumHeight(56)
        self.setMaximumHeight(60)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        bus.theme_changed.connect(self.update)

    # ───────── public ─────────
    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        v = self._snap(v)
        if v != self._value:
            self._value = v
            self.update()
            self.level_changed.emit(v)

    def label(self) -> str:
        return self._labels.get(self._value, "")

    # ───────── internals ─────────
    def _snap(self, v: int) -> int:
        return min(self._percents, key=lambda p: abs(p - v))

    def _track_rect(self) -> QRectF:
        h = self.height()
        return QRectF(20, h - 28, max(0, self.width() - 40), 4)

    def _x_for(self, percent: int) -> float:
        tr = self._track_rect()
        if not self._percents:
            return tr.left()
        # Map percent (which is one of self._percents) to position 0..1 across stops
        idx = self._percents.index(percent)
        denom = max(1, len(self._percents) - 1)
        return tr.left() + (idx / denom) * tr.width()

    def _percent_for_x(self, x: float) -> int:
        # Find closest stop based on x
        return min(self._percents, key=lambda p: abs(self._x_for(p) - x))

    # ───────── mouse ─────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.set_value(self._percent_for_x(e.position().x()))
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if e.buttons() & Qt.LeftButton:
            self.set_value(self._percent_for_x(e.position().x()))
            e.accept()

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = T.palette
        tr = self._track_rect()

        # Track
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(pal.bg_input)))
        p.drawRoundedRect(tr, 2, 2)

        # Active fill (left of selected thumb)
        thumb_x = self._x_for(self._value)
        active = QRectF(tr.left(), tr.top(), thumb_x - tr.left(), tr.height())
        p.setBrush(QBrush(QColor(pal.accent_default)))
        p.drawRoundedRect(active, 2, 2)

        # Stops + labels above each stop
        f_lbl = QFont(T.type.sans); f_lbl.setPointSize(9)
        p.setFont(f_lbl)
        for percent, label in self._levels:
            x = self._x_for(percent)
            # Stop dot
            r = 4
            sel = (percent == self._value)
            color = pal.accent_default if percent <= self._value else pal.text_tertiary
            p.setBrush(QBrush(QColor(color)))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(x - r, tr.top() - r // 2 + 2, r * 2, r * 2))

            # Label above
            text_w = 80
            p.setPen(QColor(pal.text_primary if sel else pal.text_tertiary))
            p.drawText(QRectF(x - text_w / 2, 4, text_w, 16),
                       Qt.AlignCenter, label)
            # Percent below
            f_pct = QFont(T.type.mono); f_pct.setPointSize(9)
            f_pct.setBold(sel)
            p.setFont(f_pct)
            p.drawText(QRectF(x - 22, tr.bottom() + 4, 44, 14),
                       Qt.AlignCenter, f"{percent}%")
            p.setFont(f_lbl)

        # Thumb
        thumb_r = 8
        p.setBrush(QBrush(QColor("#FFFFFF")))
        p.setPen(QPen(QColor(pal.accent_default), 2))
        p.drawEllipse(QRectF(thumb_x - thumb_r, tr.center().y() - thumb_r,
                             thumb_r * 2, thumb_r * 2))


__all__ = ["LevelSlider"]
