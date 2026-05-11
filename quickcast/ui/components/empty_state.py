"""EmptyState — placeholder shown when a list/section has no items.

A small icon + headline + hint + optional CTA. Used by Slots / Alerts
when the user has deleted everything, and by sections that depend on a
not-yet-configured backend (e.g. Telegram before token is set).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from quickcast.ui.components.icon_button import IconButton
from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


class EmptyState(QWidget):
    def __init__(
        self,
        icon: str,
        title: str,
        hint: str = "",
        *,
        cta_text: str = "",
        cta_icon: str = "plus",
        on_cta=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon

        v = QVBoxLayout(self); v.setContentsMargins(24, 32, 24, 32); v.setSpacing(8)
        v.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        self.icon_lbl = QLabel(); self.icon_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.icon_lbl)

        self.title_lbl = QLabel(title); self.title_lbl.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setBold(True); f.setPointSize(14); self.title_lbl.setFont(f)
        v.addWidget(self.title_lbl)

        if hint:
            self.hint_lbl = QLabel(hint); self.hint_lbl.setAlignment(Qt.AlignCenter)
            self.hint_lbl.setWordWrap(True)
            v.addWidget(self.hint_lbl)
        else:
            self.hint_lbl = None

        if cta_text and on_cta is not None:
            cta_row = QHBoxLayout()
            cta_row.setAlignment(Qt.AlignHCenter)
            self.cta_btn = IconButton(cta_text, cta_icon, variant="primary")
            self.cta_btn.clicked.connect(on_cta)
            cta_row.addWidget(self.cta_btn)
            v.addLayout(cta_row)
        else:
            self.cta_btn = None

        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        self.icon_lbl.setPixmap(
            Icon.get(self._icon_name, 32, p.text_tertiary).pixmap(32, 32)
        )
        self.title_lbl.setStyleSheet(f"color:{p.text_primary};")
        if self.hint_lbl is not None:
            self.hint_lbl.setStyleSheet(f"color:{p.text_tertiary}; font-size:12px;")


__all__ = ["EmptyState"]
