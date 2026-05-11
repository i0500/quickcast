"""Floating master switch — small iOS toggle + drag handle.

Layout:
  ┌──────────────────────┐
  │  ON   [▣ ]           │  ← left text doubles as drag handle
  └──────────────────────┘   right side is the iOS toggle
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from quickcast.ui.ios_toggle import IOSToggle
from quickcast.utils.window_finder import (
    get_client_rect_screen, get_window_rect, is_window_alive,
)

# Half the size of the previous floater
TOGGLE_W = 36
TOGGLE_H = 20
PAD_X = 8
PAD_Y = 6
LABEL_MIN_W = 32

TRACK_INTERVAL_MS = 200
DRAG_THRESHOLD_PX = 3


class _DragHandle(QLabel):
    """The OFF/ON text. Click-drag to move the parent floater."""

    drag_start = Signal(QPoint)
    drag_move = Signal(QPoint)
    drag_end = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._press_global: Optional[QPoint] = None
        self._is_dragging = False
        self.setCursor(Qt.SizeAllCursor)
        self.setMinimumWidth(LABEL_MIN_W)
        self.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setBold(True); f.setPointSize(9); self.setFont(f)
        self.setText("OFF")
        self.setStyleSheet("color:#ff5252; padding:0 4px;")

    def set_state(self, on: bool) -> None:
        self.setText("ON" if on else "OFF")
        self.setStyleSheet(
            f"color:{'#34c759' if on else '#ff5252'}; padding:0 4px;"
        )

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._is_dragging = False
            self.drag_start.emit(self._press_global)
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._press_global is None:
            return
        cur = e.globalPosition().toPoint()
        if not self._is_dragging:
            d = cur - self._press_global
            if abs(d.x()) + abs(d.y()) >= DRAG_THRESHOLD_PX:
                self._is_dragging = True
        if self._is_dragging:
            self.drag_move.emit(cur)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._press_global = None
            self._is_dragging = False
            self.drag_end.emit()
            e.accept()


class FloatingSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._target_hwnd: Optional[int] = None
        self._user_offset: Optional[QPoint] = None  # offset from window top-right
        self._drag_origin: Optional[QPoint] = None
        self._dragging: bool = False

        self.handle = _DragHandle(self)
        self.handle.drag_start.connect(self._on_drag_start)
        self.handle.drag_move.connect(self._on_drag_move)
        self.handle.drag_end.connect(self._on_drag_end)

        self.toggle = IOSToggle(width=TOGGLE_W, height=TOGGLE_H, parent=self)
        self.toggle.toggled.connect(self._on_toggle)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_X, PAD_Y, PAD_X, PAD_Y)
        lay.setSpacing(6)
        lay.addWidget(self.handle)
        lay.addWidget(self.toggle)
        self.setLayout(lay)
        self.adjustSize()

        self._tracker = QTimer(self)
        self._tracker.setInterval(TRACK_INTERVAL_MS)
        self._tracker.timeout.connect(self._track)

        self.hide()

    # ───────── public API ─────────
    def attach_to(self, hwnd: int) -> None:
        if not hwnd or not is_window_alive(hwnd):
            self.detach()
            return
        self._target_hwnd = hwnd
        self._user_offset = None  # snap to default top-right corner
        self._tracker.start()
        self._track()
        self.show()

    def detach(self) -> None:
        self._target_hwnd = None
        self._tracker.stop()
        self.hide()

    def set_state(self, on: bool) -> None:
        self.toggle.set_state(on)
        self.handle.set_state(on)

    def set_theme(self, _theme_id: str) -> None:
        # iOS colours are fixed; no recolour needed.
        pass

    def _on_toggle(self, on: bool) -> None:
        self.handle.set_state(on)
        self.toggled.emit(on)

    # ───────── window tracking ─────────
    def _track(self) -> None:
        if self._target_hwnd is None:
            return
        # While the user is dragging, the tracker MUST NOT yank the floater
        # back to its anchor — that's what produced the flicker. Wait until
        # mouse release, then re-anchor from the new position.
        if self._dragging:
            return
        if not is_window_alive(self._target_hwnd):
            # Window died (game closed / restarted). Don't detach() —
            # that stops the tracker and forgets we're supposed to be
            # ON. Just hide and keep polling so the AppWindow auto-find
            # signal (game_window_found) can re-attach us seamlessly.
            self._target_hwnd = None
            self.hide()
            return
        rect = get_client_rect_screen(self._target_hwnd) or get_window_rect(self._target_hwnd)
        if rect is None:
            return
        if self._user_offset is None:
            # Default anchor: top-right of the client area, but pushed
            # down by ONE floater height so we don't overlap the in-game
            # menu strip (which sits at the very top of the client).
            tx = rect.right - self.width() - 8
            ty = rect.top + 8 + self.height()
        else:
            tx = rect.right - self.width() - self._user_offset.x()
            ty = rect.top + self._user_offset.y()
        if (self.x(), self.y()) != (tx, ty):
            self.move(tx, ty)

    # ───────── drag handlers (driven by _DragHandle) ─────────
    def _on_drag_start(self, _global_pos: QPoint) -> None:
        self._drag_origin = self.pos()
        self._dragging = True

    def _on_drag_move(self, global_pos: QPoint) -> None:
        # Move the floater so the handle stays under the cursor
        new_x = global_pos.x() - self.handle.x() - self.handle.width() // 2
        new_y = global_pos.y() - self.handle.y() - self.handle.height() // 2
        self.move(new_x, new_y)

    def _on_drag_end(self) -> None:
        self._dragging = False
        if self._target_hwnd is None:
            return
        rect = get_client_rect_screen(self._target_hwnd) or get_window_rect(self._target_hwnd)
        if rect is None:
            return
        self._user_offset = QPoint(
            rect.right - self.x() - self.width(),
            self.y() - rect.top,
        )

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(20, 25, 40, 220)))
        radius = self.height() / 2.0
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)


__all__ = ["FloatingSwitch"]
