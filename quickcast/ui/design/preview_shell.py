"""Full UI shell preview — runs the complete AppShell with mock data.

This is what to use to evaluate the OVERALL design (layout, balance,
typography, colour, density). Sections show stub content; real widgets
land in Phase 4.

Run:
    .venv\\Scripts\\python.exe -m quickcast.ui.design.preview_shell

Hotkeys:
    Ctrl+T   — toggle Graphite ↔ Paper
    Ctrl+R   — re-import design modules + re-apply theme (hot reload)
    Ctrl+M   — toggle Master switch (mocked)
    F11      — toggle window maximize
    Ctrl+Q   — quit
"""
from __future__ import annotations

import importlib
import sys
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from quickcast.ui.components.activity_bar import ActivityItem
from quickcast.ui.components.app_shell import AppShell
from quickcast.ui.components.command_palette import Action, CommandPalette
from quickcast.ui.design import fonts as design_fonts
from quickcast.ui.design import themes as design_themes
from quickcast.ui.sections.dashboard_section import make_dashboard
from quickcast.ui.sections.capture_section import make_capture
from quickcast.ui.sections.combat_section import make_combat
from quickcast.ui.sections.slots_section import make_slots
from quickcast.ui.sections.alerts_section import make_alerts
from quickcast.ui.sections.settings_section import make_settings


SECTIONS = [
    ("dashboard", "gauge",     "대시보드",  make_dashboard),
    ("capture",   "crosshair", "캡처",      make_capture),
    ("combat",    "swords",    "전투 대응", make_combat),
    ("slots",     "keyboard",  "스킬 슬롯", make_slots),
    ("alerts",    "bell",      "알람",      make_alerts),
    ("settings",  "settings",  "설정",      make_settings),
]

ACTIVITY_ITEMS = [
    ActivityItem(id=sid, icon=icon, tooltip=name)
    for (sid, icon, name, _) in SECTIONS
] + [
    ActivityItem(id="palette", icon="palette", tooltip="테마 토글", bottom=True),
    ActivityItem(id="help",    icon="circle-help", tooltip="도움말", bottom=True),
]


def _reload_design() -> None:
    """Re-import design modules so edits to tokens.py / qss_template.py take effect."""
    from quickcast.ui.design import tokens as t_mod, qss_template as q_mod, themes as th_mod, icons as ic_mod
    importlib.reload(t_mod)
    importlib.reload(q_mod)
    importlib.reload(th_mod)
    importlib.reload(ic_mod)


def main() -> None:
    from quickcast.utils.logger import setup as setup_logging
    setup_logging()

    app = QApplication(sys.argv)
    design_fonts.register()
    design_themes.apply_theme(app, "graphite")

    shell = AppShell(items=ACTIVITY_ITEMS, app_name="QuickCast · Design Preview")

    # Floating switch — created up-front but only ATTACHED to the shell
    # window when the user toggles the titlebar's floater button ON.
    # Detaching hides the widget; re-attaching restores it.
    from quickcast.ui.floating_switch import FloatingSwitch
    from PySide6.QtCore import QPoint
    floater = FloatingSwitch()

    def _on_floater_toggled(on: bool) -> None:
        if on:
            try:
                floater.attach_to(int(shell.winId()))
                floater._user_offset = QPoint(220, 10)
            except Exception:
                pass
        else:
            floater.detach()
    shell.floater_toggled.connect(_on_floater_toggled)
    floater.toggled.connect(lambda on: shell.set_master(on))
    shell.master_toggled.connect(lambda on: floater.set_state(on))

    # Register real sections
    for sid, icon, name, factory in SECTIONS:
        sidebar_w, main_w = factory()
        shell.add_section(sid, sidebar_w, main_w)

    # Mock master toggle wiring
    state = {"master": False}
    def _on_master(on: bool) -> None:
        state["master"] = on
        shell.set_master(on)
    shell.master_toggled.connect(_on_master)

    # Bottom items — palette toggles theme, help opens a small dialog.
    def _on_section(sid: str) -> None:
        if sid == "palette":
            current = "paper" if (getattr(shell, "_theme", "graphite") == "graphite") else "graphite"
            shell._theme = current
            design_themes.apply_theme(app, current)
            shell.activity.set_active(shell._current or "dashboard")
        elif sid == "help":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                shell,
                "단축키",
                "Ctrl+T  테마 전환\n"
                "Ctrl+R  디자인 핫 리로드\n"
                "Ctrl+M  Master 토글\n"
                "F11     전체화면\n"
                "Ctrl+Q  종료",
            )
            shell.activity.set_active(shell._current or "dashboard")
    shell.section_changed.connect(_on_section)

    # Mock connection state
    shell.status_bar.update_capture(True, "1280×720·60fps")
    shell.status_bar.update_arduino(True, "COM3")
    shell.status_bar.update_telegram(False, "미연결")
    shell.status_bar.update_master(False)

    # Wire Dashboard's preview recognition signal → StatusBar (real values)
    from quickcast.ui.sections.dashboard_section import attach_recognition_to_statusbar
    dash_widget = shell._sections["dashboard"][1]
    if hasattr(dash_widget, "_dashboard_preview"):
        attach_recognition_to_statusbar(
            dash_widget._dashboard_preview,
            shell.status_bar,
        )

    # Hotkeys
    def _toggle_theme():
        cur = getattr(shell, "_theme", "graphite")
        nxt = "paper" if cur == "graphite" else "graphite"
        shell._theme = nxt
        design_themes.apply_theme(app, nxt)
    QShortcut(QKeySequence("Ctrl+T"), shell, activated=_toggle_theme)
    def _hot_reload():
        _reload_design()
        cur = getattr(shell, "_theme", "graphite")
        # Re-import returns fresh module so re-fetch apply_theme
        from quickcast.ui.design import themes as th_mod
        th_mod.apply_theme(app, cur)
    QShortcut(QKeySequence("Ctrl+R"), shell, activated=_hot_reload)
    def _toggle_master():
        new_state = not state["master"]
        state["master"] = new_state
        shell.set_master(new_state)
    QShortcut(QKeySequence("Ctrl+M"), shell, activated=_toggle_master)
    QShortcut(QKeySequence("F11"), shell, activated=shell._toggle_max)
    QShortcut(QKeySequence("Ctrl+Q"), shell, activated=shell.close)

    # ── Command Palette (Ctrl+K) ──
    def _build_actions() -> list[Action]:
        out: list[Action] = []
        # Section navigation
        for sid, icon, name, _ in SECTIONS:
            out.append(Action(
                id=f"goto:{sid}", title=f"이동: {name}", hint=f"섹션 · {sid}",
                icon=icon, section="이동",
                callback=lambda _sid=sid: shell.activity.set_active(_sid),
            ))
        # Theme
        out.append(Action(
            id="theme:graphite", title="테마: Graphite (다크)", icon="moon",
            section="테마",
            callback=lambda: (setattr(shell, "_theme", "graphite"),
                              design_themes.apply_theme(app, "graphite")),
        ))
        out.append(Action(
            id="theme:paper", title="테마: Paper (라이트)", icon="sun",
            section="테마",
            callback=lambda: (setattr(shell, "_theme", "paper"),
                              design_themes.apply_theme(app, "paper")),
        ))
        out.append(Action(
            id="theme:toggle", title="테마 토글", hint="Ctrl+T",
            icon="palette", section="테마", callback=_toggle_theme,
        ))
        # Window
        out.append(Action(
            id="win:max", title="최대화/복원", hint="F11",
            icon="maximize-2", section="창", callback=shell._toggle_max,
        ))
        out.append(Action(
            id="win:quit", title="종료", hint="Ctrl+Q",
            icon="power", section="창", callback=shell.close,
        ))
        # Master
        out.append(Action(
            id="master:toggle", title="Master 토글", hint="Ctrl+M",
            icon="power", section="제어", callback=_toggle_master,
        ))
        # Floater
        out.append(Action(
            id="floater:on", title="플로팅 버튼 표시", icon="target",
            section="플로터",
            callback=lambda: shell.title_bar.floater_toggle.set_state(True, animate=True),
        ))
        out.append(Action(
            id="floater:off", title="플로팅 버튼 숨기기", icon="target",
            section="플로터",
            callback=lambda: shell.title_bar.floater_toggle.set_state(False, animate=True),
        ))
        # Hot reload
        out.append(Action(
            id="reload", title="디자인 핫 리로드", hint="Ctrl+R",
            icon="repeat", section="개발", callback=_hot_reload,
        ))
        return out

    def _open_palette() -> None:
        palette = CommandPalette(_build_actions(), parent=shell)
        palette.exec()
    QShortcut(QKeySequence("Ctrl+K"), shell, activated=_open_palette)

    shell.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
