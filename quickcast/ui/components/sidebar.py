"""Sidebar — left panel showing the active section's list/tree.

Layout:
  ┌──────────────────────┐
  │ HEADER (title + add) │
  ├──────────────────────┤
  │                      │
  │   scrollable content │
  │   (list / tree /     │
  │    form fields)      │
  │                      │
  └──────────────────────┘
"""
from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


WIDTH = 260


class Sidebar(QFrame):
    def __init__(
        self,
        title: str = "",
        actions: Optional[Iterable[QWidget]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # Header
        self._header = QFrame()
        h = QHBoxLayout(self._header); h.setContentsMargins(14, 10, 8, 10); h.setSpacing(8)
        self._title = QLabel(title)
        f = QFont(); f.setBold(True); f.setPointSize(11); self._title.setFont(f)
        h.addWidget(self._title); h.addStretch(1)
        for a in (actions or []):
            h.addWidget(a)
        outer.addWidget(self._header)

        # Body
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._inner = QWidget()
        self._body = QVBoxLayout(self._inner)
        self._body.setContentsMargins(8, 4, 8, 8); self._body.setSpacing(4)
        self._body.addStretch(1)
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)

    # ───────── public API ─────────
    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_actions(self, actions: Iterable[QWidget]) -> None:
        layout = self._header.layout()
        # Remove existing trailing widgets (keep title + stretch)
        while layout.count() > 2:
            it = layout.takeAt(2)
            if it and it.widget():
                it.widget().deleteLater()
        for a in actions:
            layout.addWidget(a)

    def set_content(self, widget: QWidget) -> None:
        # Replace inner widget with the section's content.
        self._scroll.takeWidget()
        self._inner = widget
        self._scroll.setWidget(self._inner)


__all__ = ["Sidebar", "WIDTH"]
