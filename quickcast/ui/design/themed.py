"""Theme-reactive helpers.

Most sections set inline `setStyleSheet(f"color:{T.palette.X};")` once at
construction. The hex value is frozen at that point — when the user
swaps theme later, the widget keeps the old color and ends up unreadable
on the new background. Wrap the call in `reactive()` and the style is
re-applied on every `bus.theme_changed`.

Usage:
    reactive(label, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from quickcast.ui.design.signals import bus


def reactive(widget: QWidget, builder: Callable[[], str]) -> None:
    """Apply builder() now and on every theme change.

    The widget is auto-detached when destroyed (Qt drops dangling slots),
    but we wrap setStyleSheet in a try/except for safety in case Python
    holds a reference past Qt's lifecycle.
    """
    def _apply() -> None:
        try:
            widget.setStyleSheet(builder())
        except RuntimeError:
            pass
    _apply()
    bus.theme_changed.connect(_apply)


__all__ = ["reactive"]
