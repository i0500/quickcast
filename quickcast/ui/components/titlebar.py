"""Frameless title bar with drag, window controls and Master toggle.

Sits at the top of the AppShell. Master state is mirrored from the rest
of the app via `set_master(on)`; users toggle it here as well.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from quickcast.ui.components.icon_button import IconOnlyButton
from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T


class TitleBar(QFrame):
    minimize_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    master_toggled = Signal(bool)
    floater_toggled = Signal(bool)

    HEIGHT = 44

    def __init__(self, app_name: str = "QuickCast", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(self.HEIGHT)

        self._drag_origin: Optional[QPoint] = None
        self._win_origin: Optional[QPoint] = None
        self._master = False

        h = QHBoxLayout(self); h.setContentsMargins(12, 0, 4, 0); h.setSpacing(10)

        # App icon + name
        self.icon_lbl = QLabel(); self.icon_lbl.setPixmap(
            Icon.get("crosshair", 16, T.palette.accent_default).pixmap(16, 16)
        )
        self.name_lbl = QLabel(app_name); self.name_lbl.setObjectName("appName")
        f = QFont(); f.setBold(True); f.setPointSize(11); self.name_lbl.setFont(f)
        h.addWidget(self.icon_lbl); h.addWidget(self.name_lbl)
        h.addStretch(1)

        # Master + Floating toggles — both styled identically (label + iOS toggle)
        from quickcast.ui.ios_toggle import IOSToggle

        # Floating switch toggle — same iPhone-style as Master.
        self.floater_label = QLabel("Floating")
        reactive(self.floater_label, lambda: f"color:{T.palette.text_secondary};")
        self.floater_toggle = IOSToggle(width=44, height=24)
        self.floater_toggle.toggled.connect(self.floater_toggled.emit)
        # Reference kept for backwards compatibility (preview_shell uses it)
        self.floater_btn = self.floater_toggle
        h.addWidget(self.floater_label); h.addWidget(self.floater_toggle)
        h.addSpacing(16)

        # Master toggle.
        self.master_toggle = IOSToggle(width=44, height=24)
        self.master_toggle.toggled.connect(self._on_master_toggled)
        self.master_label = QLabel("Master")
        reactive(self.master_label, lambda: f"color:{T.palette.text_secondary};")
        h.addWidget(self.master_label); h.addWidget(self.master_toggle)
        h.addSpacing(12)

        # Window controls
        self.btn_min = IconOnlyButton("minus", size="sm", tooltip="최소화")
        self.btn_max = IconOnlyButton("square", size="sm", tooltip="최대화/복원")
        self.btn_close = IconOnlyButton("x", size="sm", tooltip="닫기")
        self.btn_close.setStyleSheet(
            "QToolButton:hover { background:#ef4444; color:#fff; }"
        )
        self.btn_min.clicked.connect(self.minimize_clicked.emit)
        self.btn_max.clicked.connect(self.maximize_clicked.emit)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        h.addWidget(self.btn_min); h.addWidget(self.btn_max); h.addWidget(self.btn_close)

    # ───────── public API ─────────
    def set_master(self, on: bool) -> None:
        self._master = on
        self.master_toggle.set_state(on, animate=True)

    def _on_master_toggled(self, on: bool) -> None:
        self._master = on
        self.master_toggled.emit(on)

    # ───────── drag ─────────
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and self.window():
            self._drag_origin = e.globalPosition().toPoint()
            self._win_origin = self.window().pos()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_origin is None or self.window() is None:
            return
        delta = e.globalPosition().toPoint() - self._drag_origin
        win = self.window()
        # Dragging a maximized window used to call `win.move()` directly,
        # which silently broke the maximized-state bookkeeping and left
        # the restore-to-normal geometry stale — the user then couldn't
        # un-maximize back to the previous "작은 사이즈". Conventional
        # behaviour: restore normal, place the window under the cursor,
        # then continue dragging.
        if win.isMaximized():
            cursor_global = e.globalPosition().toPoint()
            win.showNormal()
            # After showNormal the window is at its previous normal
            # geometry. Re-anchor so the cursor stays on the title bar.
            nw = win.width()
            self._win_origin = QPoint(cursor_global.x() - nw // 2, 0)
            self._drag_origin = cursor_global
            win.move(self._win_origin)
            e.accept()
            return
        win.move(self._win_origin + delta)
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_origin = None
        e.accept()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self.maximize_clicked.emit()
            e.accept()


__all__ = ["TitleBar"]
