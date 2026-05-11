"""Card primitive — surface container with consistent header + body.

Usage:
    card = Card("스킬 슬롯", subtitle="활성 5개", actions=[btn_add])
    card.add(slot_list)
"""
from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)


class Card(QFrame):
    def __init__(
        self,
        title: str = "",
        *,
        subtitle: str = "",
        actions: Optional[Iterable[QWidget]] = None,
        expanding: bool = False,
        inline_subtitle: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        v_policy = QSizePolicy.Expanding if expanding else QSizePolicy.Maximum
        self.setSizePolicy(QSizePolicy.Expanding, v_policy)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(16, 14, 16, 14)
        self._root.setSpacing(10)

        if title or actions:
            self._root.addWidget(
                _CardHeader(title, subtitle, actions or [], inline_subtitle)
            )

    def add(self, *widgets_or_layouts) -> None:
        for w in widgets_or_layouts:
            if hasattr(w, "addWidget"):    # is layout
                self._root.addLayout(w)
            else:
                self._root.addWidget(w)

    @property
    def body(self) -> QVBoxLayout:
        return self._root


class _CardHeader(QFrame):
    def __init__(self, title: str, subtitle: str, actions: Iterable[QWidget],
                  inline_subtitle: bool = True) -> None:
        super().__init__()
        self.setObjectName("cardHeader")
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)

        if inline_subtitle:
            # Title + subtitle on a single horizontal line — title left,
            # subtitle right next to it as muted comment.
            if title:
                t = QLabel(title); t.setObjectName("sectionHeading")
                f = QFont(); f.setBold(True); f.setPointSize(13); t.setFont(f)
                h.addWidget(t)
            if subtitle:
                s = QLabel(subtitle); s.setProperty("role", "dim")
                h.addWidget(s)
        else:
            text_box = QVBoxLayout()
            text_box.setContentsMargins(0, 0, 0, 0); text_box.setSpacing(0)
            if title:
                t = QLabel(title); t.setObjectName("sectionHeading")
                f = QFont(); f.setBold(True); f.setPointSize(13); t.setFont(f)
                text_box.addWidget(t)
            if subtitle:
                s = QLabel(subtitle); s.setProperty("role", "dim")
                text_box.addWidget(s)
            h.addLayout(text_box)
        h.addStretch(1)
        for w in actions:
            h.addWidget(w)


__all__ = ["Card"]
