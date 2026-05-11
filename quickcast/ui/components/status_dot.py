"""StatusDot — small ● + label, used in the StatusBar and connection rows."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


class StatusDot(QWidget):
    def __init__(self, label: str, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._label = label
        self._ok: bool = False
        self._text: str = "미연결"

        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)

        self._dot = QLabel("●"); self._dot.setMinimumWidth(8)
        self._lbl = QLabel()
        h.addWidget(self._dot); h.addWidget(self._lbl)

        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def set(self, ok: bool, text: str = "") -> None:
        self._ok = ok
        self._text = text or ("연결됨" if ok else "미연결")
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        col = p.state_success if self._ok else p.text_tertiary
        # Match HP/MP/PK/Potion/FPS sizing — 11px throughout the StatusBar.
        self._dot.setStyleSheet(f"color:{col}; font-size:11px;")
        self._lbl.setStyleSheet("font-size:11px;")
        self._lbl.setText(f"<span style='color:{p.text_secondary}'>{self._label}</span>"
                          f" <span style='color:{p.text_primary}'>{self._text}</span>")


__all__ = ["StatusDot"]
