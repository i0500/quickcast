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
        # Override Qt's content-based minimum width so the card can be
        # shrunk by its container. Without this, any fixed-width child
        # widget (e.g. Stepper, fixed-width score label, long unwrapped
        # subtitle) forces the card — and through it the enclosing
        # scroll area's inner widget — to be at least content-wide.
        # That silently clipped right-side widgets on the capture tab
        # whenever the user shrank the window. The combat section was
        # working around this manually per-card; doing it once here
        # makes every Card behave consistently.
        self.setMinimumWidth(0)

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
                # No wrap inline — sub stays a single-line muted note next
                # to the title. If the inline mode is given a long subtitle
                # the card author should switch to inline_subtitle=False.
                h.addWidget(s)
            h.addStretch(1)
        else:
            text_box = QVBoxLayout()
            text_box.setContentsMargins(0, 0, 0, 0); text_box.setSpacing(0)
            if title:
                t = QLabel(title); t.setObjectName("sectionHeading")
                f = QFont(); f.setBold(True); f.setPointSize(13); t.setFont(f)
                text_box.addWidget(t)
            if subtitle:
                s = QLabel(subtitle); s.setProperty("role", "dim")
                # Word-wrap the multi-line subtitle so long help text
                # doesn't force the card (and the whole scroll area's
                # inner widget) wider than the viewport — that was
                # silently clipping the right-side widgets on the
                # capture tab whenever a verbose subtitle was set.
                s.setWordWrap(True)
                # Ignore horizontal width preference so QHBoxLayout can
                # shrink us to whatever space is left.
                s.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                text_box.addWidget(s)
            # stretch=1 so the text block claims all leftover width
            # (no extra addStretch — actions sit on the right at their
            # natural sizes).
            h.addLayout(text_box, stretch=1)
        for w in actions:
            h.addWidget(w)


__all__ = ["Card"]
