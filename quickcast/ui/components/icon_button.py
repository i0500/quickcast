"""IconButton — QPushButton/QToolButton wrapper with size + variant presets."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from quickcast.ui.design.icons import Icon

_SIZES = {"sm": 14, "md": 16, "lg": 20, "xl": 24}
_BUTTON_HEIGHT = {"sm": 26, "md": 30, "lg": 34, "xl": 40}


class IconButton(QPushButton):
    """Text + icon button. Variant ∈ default/primary/ghost/danger."""

    def __init__(
        self,
        text: str,
        icon: Optional[str] = None,
        *,
        size: str = "md",
        variant: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon
        self._icon_px = _SIZES[size]
        if icon:
            self.setIcon(Icon.get(icon, self._icon_px))
            self.setIconSize(QSize(self._icon_px, self._icon_px))
        self.setMinimumHeight(_BUTTON_HEIGHT[size])
        if variant:
            self.setProperty("variant", variant)
        self.setCursor(Qt.PointingHandCursor)
        # Re-render the SVG icon when the theme changes so it stays
        # legible on the new background.
        from quickcast.ui.design.signals import bus
        bus.theme_changed.connect(self._refresh_icon)

    def _refresh_icon(self) -> None:
        if self._icon_name:
            self.setIcon(Icon.get(self._icon_name, self._icon_px))


class IconOnlyButton(QToolButton):
    """Icon-only square button — for sidebar/activity/header chrome."""

    def __init__(
        self,
        icon: str,
        *,
        size: str = "md",
        tooltip: str = "",
        checkable: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon
        self._icon_px = _SIZES[size]
        self.setIcon(Icon.get(icon, self._icon_px))
        self.setIconSize(QSize(self._icon_px, self._icon_px))
        side = _BUTTON_HEIGHT[size]
        self.setFixedSize(side, side)
        self.setCheckable(checkable)
        if tooltip:
            self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        from quickcast.ui.design.signals import bus
        bus.theme_changed.connect(self._refresh_icon)

    def _refresh_icon(self) -> None:
        self.setIcon(Icon.get(self._icon_name, self._icon_px))


__all__ = ["IconButton", "IconOnlyButton"]
