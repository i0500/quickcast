"""AppShell — frameless main window assembling TitleBar / ActivityBar /
Sidebar / Main / StatusBar.

Sections register via `add_section(id, sidebar_widget, main_widget)`.
The first registered section becomes active.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

from quickcast.ui.components.activity_bar import ActivityBar, ActivityItem
from quickcast.ui.components.sidebar import Sidebar
from quickcast.ui.components.statusbar import StatusBar
from quickcast.ui.components.titlebar import TitleBar


class AppShell(QMainWindow):
    section_changed = Signal(str)
    master_toggled = Signal(bool)
    floater_toggled = Signal(bool)

    def __init__(
        self,
        items: list[ActivityItem],
        app_name: str = "QuickCast",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("appShell")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(1100, 720)
        self.resize(1280, 820)

        self._sections: dict[str, tuple[QWidget, QWidget]] = {}
        self._current: str = ""

        self._build(items, app_name)

    # ───────── building ─────────
    def _build(self, items: list[ActivityItem], app_name: str) -> None:
        central = QWidget(); central.setObjectName("central")
        outer = QVBoxLayout(central); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        self.title_bar = TitleBar(app_name)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.maximize_clicked.connect(self._toggle_max)
        self.title_bar.close_clicked.connect(self.close)
        self.title_bar.master_toggled.connect(self.master_toggled.emit)
        self.title_bar.floater_toggled.connect(self.floater_toggled.emit)
        outer.addWidget(self.title_bar)

        body = QWidget()
        bl = QHBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)

        self.activity = ActivityBar(items)
        self.activity.section_changed.connect(self._on_activity)
        bl.addWidget(self.activity)

        # Vertical separator
        sep = QFrame(); sep.setFrameShape(QFrame.NoFrame); sep.setFixedWidth(0)
        bl.addWidget(sep)

        self.sidebar = Sidebar()
        bl.addWidget(self.sidebar)

        self.main_stack = QStackedWidget()
        bl.addWidget(self.main_stack, stretch=1)

        outer.addWidget(body, stretch=1)

        self.status_bar = StatusBar()
        outer.addWidget(self.status_bar)

        self.setCentralWidget(central)

    # ───────── sections ─────────
    def add_section(self, item_id: str, sidebar_widget: QWidget, main_widget: QWidget) -> None:
        self._sections[item_id] = (sidebar_widget, main_widget)
        self.main_stack.addWidget(main_widget)
        if not self._current:
            self._current = item_id
            self.activity.set_active(item_id)

    def _on_activity(self, item_id: str) -> None:
        # Always forward the click — bottom items (palette/help) are not
        # registered as sections but still need to fire section_changed
        # so the host can react (theme toggle, help dialog, …).
        if item_id in self._sections:
            sidebar_w, main_w = self._sections[item_id]
            # Sections may opt out of the sidebar by passing None — the
            # main panel then expands to fill the freed width.
            if sidebar_w is None:
                self.sidebar.setVisible(False)
            else:
                self.sidebar.setVisible(True)
                self.sidebar.set_content(sidebar_w)
            self.main_stack.setCurrentWidget(main_w)
            self._current = item_id
        self.section_changed.emit(item_id)

    # ───────── window ops ─────────
    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ───────── master mirror ─────────
    def set_master(self, on: bool) -> None:
        self.title_bar.set_master(on)
        self.status_bar.update_master(on)


__all__ = ["AppShell"]
