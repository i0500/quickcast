"""Vertical Activity Bar (VS Code style) — section switcher.

Selected item shows a 3px accent bar on its left edge. Sections register
themselves by adding (id, icon, tooltip) entries; the bar emits
`section_changed(id)` when the user clicks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T

WIDTH = 56
ITEM_H = 44
ICON_SIZE = 22


@dataclass
class ActivityItem:
    id: str
    icon: str           # Lucide icon name
    tooltip: str
    bottom: bool = False


class _ActivityButton(QToolButton):
    """Single activity icon. Paints the 3px accent indicator when checked."""

    def __init__(self, item: ActivityItem) -> None:
        super().__init__()
        self.item = item
        self.setCheckable(True)
        self.setFixedSize(WIDTH, ITEM_H)
        self.setIcon(Icon.get(item.icon, ICON_SIZE))
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setToolTip(item.tooltip)
        self.setCursor(Qt.PointingHandCursor)
        # Red-dot badge for notifications (update available, etc.). Drawn
        # in the top-right corner by paintEvent when ``_badge`` is True.
        self._badge: bool = False
        # Use bg_hover token so the hover tint works on both light and dark.
        def _qss() -> str:
            return (
                "QToolButton { border:none; background:transparent; }"
                f"QToolButton:hover {{ background:{T.palette.bg_hover}; }}"
            )
        self.setStyleSheet(_qss())
        bus.theme_changed.connect(lambda: self.setStyleSheet(_qss()))
        bus.theme_changed.connect(self._refresh_icon)

    def _refresh_icon(self) -> None:
        col = T.palette.text_primary if self.isChecked() else T.palette.text_tertiary
        self.setIcon(Icon.get(self.item.icon, ICON_SIZE, col))

    def setChecked(self, on: bool) -> None:
        super().setChecked(on)
        self._refresh_icon()

    def set_badge(self, show: bool) -> None:
        new = bool(show)
        if new == self._badge:
            return
        self._badge = new
        self.update()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            if self.isChecked():
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(T.palette.accent_default))
                p.drawRect(0, 6, 3, self.height() - 12)
            if self._badge:
                # 8px red dot in the top-right of the icon — small enough
                # to read as a "notification" badge, not a state colour.
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#E5484D"))
                cx = self.width() - 14
                cy = 10
                p.drawEllipse(cx - 4, cy - 4, 8, 8)
        finally:
            p.end()


class ActivityBar(QFrame):
    section_changed = Signal(str)

    def __init__(self, items: list[ActivityItem], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("activityBar")
        self.setFixedWidth(WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        v = QVBoxLayout(self); v.setContentsMargins(0, 8, 0, 8); v.setSpacing(2)

        self._group = QButtonGroup(self); self._group.setExclusive(True)
        self._buttons: dict[str, _ActivityButton] = {}

        top_items = [it for it in items if not it.bottom]
        bot_items = [it for it in items if it.bottom]

        for it in top_items:
            btn = _ActivityButton(it)
            btn.clicked.connect(lambda _checked=False, _id=it.id: self.set_active(_id))
            self._group.addButton(btn)
            self._buttons[it.id] = btn
            v.addWidget(btn)

        v.addStretch(1)

        for it in bot_items:
            btn = _ActivityButton(it)
            btn.clicked.connect(lambda _checked=False, _id=it.id: self.set_active(_id))
            self._group.addButton(btn)
            self._buttons[it.id] = btn
            v.addWidget(btn)

    def set_active(self, item_id: str) -> None:
        for sid, btn in self._buttons.items():
            btn.setChecked(sid == item_id)
        self.section_changed.emit(item_id)

    def set_badge(self, item_id: str, show: bool) -> None:
        """Toggle the red-dot notification badge on a specific item."""
        btn = self._buttons.get(item_id)
        if btn is not None:
            btn.set_badge(show)


__all__ = ["ActivityBar", "ActivityItem"]
