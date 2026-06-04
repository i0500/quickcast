"""Client-tab strip for the title bar — multi-client switcher.

Sits between the app name and the Master toggle in :class:`TitleBar`.
Renders one button per client (currently [클라1][클라2] — fixed) and
emits :pyattr:`tab_changed` when the user clicks a different tab. The
active tab is highlighted; each tab carries a small dot indicator that
turns green when that client's macro is gated ON (Phase 4 will wire
this to ClientProfile.enabled).

Phase 3 scope: visual tab + click signal. AppWindow handles the actual
client switch (Settings.switch_client() + controller swap + section
refresh).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T


class _ClientTabButton(QPushButton):
    """One tab button with active-style and an ON-indicator dot."""

    def __init__(self, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(label, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setFlat(True)
        self.setFixedHeight(28)
        self.setMinimumWidth(72)
        f = QFont(); f.setPointSize(10)
        self.setFont(f)
        self._enabled_dot = False
        reactive(self, self._style)

    def set_enabled_dot(self, on: bool) -> None:
        if self._enabled_dot == on:
            return
        self._enabled_dot = on
        self.setStyleSheet(self._style())

    def _style(self) -> str:
        pal = T.palette
        # Active = filled accent background, inactive = subtle text-only.
        active = self.isChecked()
        bg = pal.accent_subtle if active else "transparent"
        fg = pal.text_primary if active else pal.text_secondary
        border = pal.accent_default if active else "transparent"
        dot_color = pal.state_success if self._enabled_dot else "transparent"
        # 작은 좌측 dot으로 enabled 상태 표시. 비활성이면 투명 — 공간만 차지.
        return (
            f"QPushButton {{"
            f"  background: {bg};"
            f"  color: {fg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 6px;"
            f"  padding: 2px 12px 2px 22px;"
            f"  text-align: left;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background: {pal.bg_hover};"
            f"  color: {pal.text_primary};"
            f"}}"
            # Dot drawn as a background image-ish overlay using a tiny radial
            # gradient on a fixed left margin. Cheaper than custom paint().
            f"QPushButton {{"
            f"  background-image: qradialgradient("
            f"    cx:0.10, cy:0.50, radius:0.18, fx:0.10, fy:0.50,"
            f"    stop:0 {dot_color}, stop:0.6 {dot_color}, stop:0.61 transparent"
            f"  );"
            f"  background-position: left center;"
            f"  background-repeat: no-repeat;"
            f"}}"
        )

    def nextCheckState(self) -> None:  # noqa: N802 (Qt naming)
        # Disable auto-toggle on click — ClientTabs.set_active drives the
        # check state explicitly so clicking the already-active tab is a
        # no-op (no flicker, no signal storm).
        return


class ClientTabs(QFrame):
    """Horizontal client-tab strip.

    Signals:
        tab_changed(str): emitted with the client_id the user clicked,
            but ONLY when the new tab differs from the current active.
    """
    tab_changed = Signal(str)

    def __init__(
        self,
        items: list[tuple[str, str]],
        active_id: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        """``items``: list of (client_id, label). ``active_id``: initial."""
        super().__init__(parent)
        self.setObjectName("clientTabs")
        self._buttons: dict[str, _ClientTabButton] = {}
        self._active_id: str = ""

        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        for cid, label in items:
            btn = _ClientTabButton(label, self)
            btn.clicked.connect(lambda _=False, _cid=cid: self._on_clicked(_cid))
            h.addWidget(btn)
            self._buttons[cid] = btn

        if active_id and active_id in self._buttons:
            self.set_active(active_id)
        elif self._buttons:
            self.set_active(next(iter(self._buttons.keys())))

    # ───────── public API ─────────
    def set_active(self, client_id: str) -> None:
        """Programmatically select a tab (no signal emit). Used when the
        active client changes from somewhere other than a user click."""
        if client_id not in self._buttons:
            return
        if client_id == self._active_id:
            return
        for cid, btn in self._buttons.items():
            btn.setChecked(cid == client_id)
            btn.setStyleSheet(btn._style())
        self._active_id = client_id

    def set_label(self, client_id: str, label: str) -> None:
        """Rename a tab (e.g. user-edited label in settings)."""
        btn = self._buttons.get(client_id)
        if btn is not None:
            btn.setText(label)

    def set_enabled_dot(self, client_id: str, on: bool) -> None:
        """Toggle the green ON-indicator dot for a client."""
        btn = self._buttons.get(client_id)
        if btn is not None:
            btn.set_enabled_dot(on)

    def active_id(self) -> str:
        return self._active_id

    # ───────── internal ─────────
    def _on_clicked(self, client_id: str) -> None:
        if client_id == self._active_id:
            # Clicking the already-active tab does nothing — no flicker,
            # no spurious refresh of all sections.
            self._buttons[client_id].setChecked(True)
            return
        self.set_active(client_id)
        self.tab_changed.emit(client_id)


__all__ = ["ClientTabs"]
