"""NotificationCenter — bottom-right toast stack.

Lightweight, in-app feedback for short-lived events (settings saved,
slot fired, alarm armed, telegram failed, …). Different from the
Windows tray balloon (`AppWindow.show_toast`) which is for system-level
events the user might miss with the app hidden.

Usage:
    NotificationCenter.attach(window)
    NotificationCenter.toast("저장됨", level="success", duration_ms=1500)
"""
from __future__ import annotations

from typing import ClassVar, Optional

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


_LEVEL_ICON = {
    "info":    "info",
    "success": "circle-check",
    "warning": "alert-triangle",
    "danger":  "alert-triangle",
}


class _Toast(QFrame):
    """One toast — icon + message, slides in from the right, auto-dismisses."""

    def __init__(self, message: str, level: str, parent: QWidget,
                 duration_ms: int) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._level = level

        h = QHBoxLayout(self); h.setContentsMargins(12, 10, 14, 10); h.setSpacing(10)
        self.icon_lbl = QLabel()
        h.addWidget(self.icon_lbl)
        self.msg_lbl = QLabel(message)
        h.addWidget(self.msg_lbl, stretch=1)
        bus.theme_changed.connect(self._restyle)
        self._restyle()
        self.adjustSize()

        QTimer.singleShot(duration_ms, self._fade_out)

    def _restyle(self) -> None:
        p = T.palette
        accent = {
            "info":    p.state_info,
            "success": p.state_success,
            "warning": p.state_warning,
            "danger":  p.state_danger,
        }.get(self._level, p.text_secondary)
        self.setStyleSheet(
            f"QFrame#toast {{ background:{p.bg_elevated};"
            f" border:1px solid {p.border_default}; border-left:3px solid {accent};"
            f" border-radius:6px; }}"
            f"QLabel {{ color:{p.text_primary}; font-size:12px; }}"
        )
        icon_name = _LEVEL_ICON.get(self._level, "info")
        self.icon_lbl.setPixmap(Icon.get(icon_name, 16, accent).pixmap(16, 16))

    def _fade_out(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(200); anim.setStartValue(1.0); anim.setEndValue(0.0)
        anim.finished.connect(self.deleteLater)
        anim.start()
        self._fade_anim = anim   # keep reference


class NotificationCenter:
    """Static facade — attach once per window, then `toast(...)` from anywhere."""
    _host: ClassVar[Optional[QWidget]] = None
    _toasts: ClassVar[list[_Toast]] = []

    @classmethod
    def attach(cls, window: QWidget) -> None:
        cls._host = window

    @classmethod
    def toast(cls, message: str, *, level: str = "info",
              duration_ms: int = 1800) -> None:
        if cls._host is None or not cls._host.isVisible():
            return
        t = _Toast(message, level, cls._host, duration_ms)
        cls._toasts.append(t)
        cls._reflow()
        t.show()

        def _on_destroy(_=None) -> None:
            try:
                cls._toasts.remove(t)
            except ValueError:
                pass
            cls._reflow()
        t.destroyed.connect(_on_destroy)

    @classmethod
    def _reflow(cls) -> None:
        if cls._host is None:
            return
        margin = 12
        spacing = 8
        host = cls._host
        # Stack from the bottom-right corner upward.
        x_anchor = host.width() - margin
        y = host.height() - margin
        for t in reversed(cls._toasts):
            t.adjustSize()
            y -= t.height()
            t.move(QPoint(x_anchor - t.width(), y))
            y -= spacing


__all__ = ["NotificationCenter"]
