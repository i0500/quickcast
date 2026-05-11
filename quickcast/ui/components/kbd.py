"""Kbd — small keycap label (e.g. Ctrl+K) for menus, tooltips, hints."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QWidget

from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


class Kbd(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("kbd")
        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        # Sized to match the rest of the StatusBar (11px) using mono so the
        # keycap reads as a key, not body text.
        self.setStyleSheet(
            "QLabel#kbd {"
            f"  background: {p.bg_input};"
            f"  border: 1px solid {p.border_default};"
            "  border-radius: 4px;"
            "  padding: 1px 6px;"
            f"  color: {p.text_secondary};"
            f"  font-family: {T.type.mono};"
            "  font-size: 11px;"
            "}"
        )


__all__ = ["Kbd"]
