"""AppWindow — production main window built on AppShell + new sections.

Replaces the legacy `MainWindow` (1000+ lines) with a thin composition
layer. Real `Settings` are wired into the section factories via
`state_bridge`, so toggling a slot, editing an alarm, or moving the
sensitivity slider writes back to userdata.json (with debounce).

Boot order:
    1. state_bridge.install(settings)   — mutate shared mock_settings
    2. AppShell + section factories     — sections see real values
    3. state_bridge.wire_persistence()  — connect change signals → save
    4. controller.on_analysis hookup    — feed StatusBar live data
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMenu, QStyle, QSystemTrayIcon,
)

from quickcast.config import Settings
from quickcast.ui.components.activity_bar import ActivityItem
from quickcast.ui.components.app_shell import AppShell
from quickcast.ui.components.command_palette import Action, CommandPalette
from quickcast.ui.components.notification_center import NotificationCenter
from quickcast.ui.design import themes as design_themes
from quickcast.ui.design.signals import bus
from quickcast.ui.sections.alerts_section import make_alerts
from quickcast.ui.sections.capture_section import make_capture
from quickcast.ui.sections.combat_section import make_combat
from quickcast.ui.sections.dashboard_section import (
    attach_recognition_to_statusbar, make_dashboard,
)
from quickcast.ui.sections.settings_section import make_settings
from quickcast.ui.sections.slots_section import make_slots
from quickcast.ui.services import Services
from quickcast.ui import state_bridge
from quickcast.utils.logger import logger


def _load_app_icon(style):
    """Load the bundled `icon.ico`. PyInstaller --onefile extracts data
    to `sys._MEIPASS`; in dev, fall back to the source path. Falls
    back to Qt's stock computer icon if the file isn't available."""
    import sys
    from pathlib import Path
    from PySide6.QtGui import QIcon
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "quickcast" / "data" / "icon.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "data" / "icon.ico")
    for p in candidates:
        if p.exists():
            return QIcon(str(p))
    return style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)


SECTIONS = [
    ("dashboard", "gauge",     "대시보드"),
    ("capture",   "crosshair", "캡처"),
    ("combat",    "swords",    "전투 대응"),
    ("slots",     "keyboard",  "스킬 슬롯"),
    ("alerts",    "bell",      "알람"),
    ("settings",  "settings",  "설정"),
]

ACTIVITY_ITEMS = [ActivityItem(id=sid, icon=icon, tooltip=name) for (sid, icon, name) in SECTIONS] + [
    ActivityItem(id="palette", icon="palette",     tooltip="테마 토글", bottom=True),
    ActivityItem(id="help",    icon="circle-help", tooltip="도움말",    bottom=True),
]


class AppWindow(AppShell):
    """Production AppShell — wires real services + persistence."""

    def __init__(
        self,
        settings: Settings,
        controller,
        arduino,
        telegram,
        alarms,
    ) -> None:
        super().__init__(items=ACTIVITY_ITEMS, app_name="QuickCast")

        self.settings = settings
        self.controller = controller
        self.arduino = arduino
        self.telegram = telegram
        # True when a real quit is in progress (tray "종료" menu, etc.)
        # so closeEvent can let the close through instead of hiding.
        self._quitting = False
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._do_save)

        # 1. Mutate the shared mock_settings IN PLACE so section factories
        #    that captured `from _mock_state import mock_settings` see the
        #    real values.
        state_bridge.install(settings)
        # CRITICAL: section UI mutates mock_settings, not the original
        # `settings` object we were handed. We must save mock_settings
        # for user edits to actually reach disk. Re-bind self.settings to
        # the same instance the UI is editing.
        from quickcast.ui.sections._mock_state import mock_settings as _mock
        self.settings = _mock
        # ALSO re-point the controller — its recognition loop reads
        # `controller.settings.hp_cap.x` etc. every frame. Without this
        # swap, ROI / threshold edits the user makes won't affect
        # recognition until the next process restart.
        if controller is not None:
            controller.settings = _mock

        # 2. Build sections — they use the (now real) shared mock_settings.
        for sid, _, _ in SECTIONS:
            factory = {
                "dashboard": make_dashboard, "capture": make_capture,
                "combat":    make_combat,    "slots":   make_slots,
                "alerts":    make_alerts,    "settings": make_settings,
            }[sid]
            sb, mw = factory()
            self.add_section(sid, sb, mw)

        # 3. Persistence wiring — slot/alarm toggles save automatically.
        state_bridge.wire_persistence(self.save_debounced)
        # Generic dirty signal — any section can emit settings_dirty after
        # mutating mock_settings, and we'll save with debounce.
        bus.settings_dirty.connect(self.save_debounced)
        # Capture target changed by user → hot-swap controller's capture source.
        bus.capture_target_changed.connect(self._hot_swap_capture)
        # User saved new digit templates → make recognizer pick them up.
        bus.digit_templates_changed.connect(self._on_digit_templates_changed)
        # "테스트" button in the item-close card → controller fires
        # one click at the saved coord immediately (no interval wait).
        bus.item_close_test_request.connect(self._on_item_close_test)
        # "테스트 ESC" button next to each overlay-close row — fires the
        # configured close_key once via the active input backend.
        bus.overlay_close_test.connect(self._on_overlay_close_test)
        # Periodic safety save (every 30s, only if dirty since last save).
        self._dirty = False
        self._poll_timer = QTimer(self); self._poll_timer.setInterval(30_000)
        self._poll_timer.timeout.connect(self._poll_save)
        self._poll_timer.start()

        # Slot cooldown broadcaster — polls the controller's per-slot
        # cooldown registry every 100 ms and emits remaining seconds on
        # the bus so the dashboard sidebar's skill rows can render a
        # live countdown + progress gauge.
        self._cd_timer = QTimer(self)
        self._cd_timer.setInterval(100)
        self._cd_timer.timeout.connect(self._broadcast_slot_cooldowns)
        self._cd_timer.start()
        # Track which slot ids we've already logged a "started" line for
        # in this cooldown cycle, so logs stay readable.
        self._cd_logged: set[str] = set()
        # User-driven manual reset from the per-row ✕ button.
        bus.slot_cooldown_reset_request.connect(self._reset_slot_cooldown)

        # Auto window-detection (every 5s while no game window is bound).
        # The user can launch the game AFTER QuickCast and have it picked
        # up automatically — same path as the manual Capture-section pick.
        self._auto_find_timer = QTimer(self)
        self._auto_find_timer.setInterval(5_000)
        self._auto_find_timer.timeout.connect(self._auto_find_game_window)
        self._auto_find_timer.start()

        # 4. Live recognition: controller.on_analysis fires on capture
        #    thread → marshal to UI → update status bar + bus.
        if self.controller is not None:
            self._wire_controller_to_ui()

        # 5. Activity bar bottom items + master.
        self.section_changed.connect(self._on_section_changed)
        self.master_toggled.connect(self._on_master_toggled)
        self.floater_toggled.connect(self._on_floater_toggled)

        # Initial status bar text
        self.status_bar.update_capture(False, "대기 중")
        a_ok = arduino is not None and getattr(arduino, "connected", False)
        t_ok = telegram is not None and getattr(telegram, "connected", False)
        self.status_bar.update_arduino(a_ok, settings.arduino_port if a_ok else "미연결")
        self.status_bar.update_telegram(t_ok, "연결됨" if t_ok else "미연결")
        # Belt-and-braces master OFF: reset both the UI and the controller's
        # internal state so the macro never fires until the user explicitly
        # clicks the toggle.
        self.settings.master_switch = False
        if self.controller is not None:
            self.controller.set_master_switch(False)
        self.set_master(False)
        # Broadcast initial connection state so Settings dots match.
        bus.arduino_state_changed.emit(a_ok, settings.arduino_port if a_ok else "미연결")
        bus.telegram_state_changed.emit(t_ok, "연결됨" if t_ok else "미연결")
        # Capture starts as 'waiting' — will flip to True on first frame.
        self._capture_seen = False

        # Wire UI-driven connect/disconnect requests.
        bus.arduino_connect_request.connect(self._toggle_arduino)
        bus.telegram_connect_request.connect(self._toggle_telegram)
        bus.test_input_request.connect(self._test_input)
        bus.input_backend_changed.connect(self._swap_input_backend)
        bus.recovery_step_test.connect(self._test_recovery_step)
        bus.alarm_fired.connect(self._on_alarm_fired)
        bus.alarm_stop_request.connect(self._stop_alarm_by_label)
        bus.activate_section.connect(self._activate_section_by_id)

        # Save on theme change.
        bus.theme_changed.connect(self._save_theme_if_changed)

        # Hotkeys
        QShortcut(QKeySequence("F11"),    self, activated=self._toggle_max)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_master)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_palette)
        # Tutorial (re-launchable via Help menu / shortcut).
        QShortcut(QKeySequence("Ctrl+Shift+T"), self, activated=self.start_tutorial)
        # 섹션 전환 단축키 Ctrl+1 ~ Ctrl+6 — 캡처 도구·키보드 사용자 편의.
        # ApplicationShortcut context — 윈도우가 inactive 상태에서도 동작.
        from PySide6.QtCore import Qt as _Qt
        for _idx, (_sid, _icon, _name) in enumerate(SECTIONS):
            _sc = QShortcut(QKeySequence(f"Ctrl+{_idx+1}"), self,
                             activated=lambda __sid=_sid: self._activate_section_by_id(__sid))
            _sc.setContext(_Qt.ApplicationShortcut)

        # Tray (hide-on-close, balloon for alarms)
        self._build_tray()

        # In-app notification center — pops bottom-right toasts.
        NotificationCenter.attach(self)
        bus.slot_list_changed.connect(
            lambda: NotificationCenter.toast("슬롯 목록 갱신", level="info"))
        bus.alarm_list_changed.connect(
            lambda: NotificationCenter.toast("알람 목록 갱신", level="info"))

        # GitHub 릴리즈 업데이트 체크 — 시작 5초 후 + 6시간마다.
        # 새 버전 감지 시 사이드바 ‘설정’ 아이콘에 빨간 점 + 토스트.
        # 사용자가 ‘설정’으로 들어가서 [업데이트 확인]을 누르거나 자동
        # 다이얼로그 한 번 띄워서 릴리즈 페이지로 안내한다.
        from quickcast.utils.update_check import UpdateChecker
        self._update_checker = UpdateChecker(self)
        self._update_prompted = False
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start_periodic()

        # First-run tutorial — auto-launches if the user hasn't done it
        # yet, OR if QUICKCAST_FORCE_TUTORIAL is set (the desktop "Design
        # Preview" shortcut sets this so the tutorial always shows
        # regardless of saved state — no preview script needed).
        import os as _os
        from PySide6.QtCore import QTimer as _QT
        force_tutorial = bool(_os.environ.get("QUICKCAST_FORCE_TUTORIAL"))
        completed = getattr(self.settings, "tutorial_completed", False)
        print(f"[TUTORIAL TRACE] env QUICKCAST_FORCE_TUTORIAL={_os.environ.get('QUICKCAST_FORCE_TUTORIAL')!r} "
              f"force={force_tutorial} completed={completed}")
        if force_tutorial or not completed:
            print("[TUTORIAL TRACE] scheduling singleShot(800, start_tutorial)")
            _QT.singleShot(800, self.start_tutorial)
        else:
            print("[TUTORIAL TRACE] skip — already completed and not forced")

    def start_tutorial(self) -> None:
        print("[TUTORIAL TRACE] start_tutorial entered")
        try:
            from quickcast.ui.components.tutorial import TutorialOverlay
            from quickcast.ui.tutorial_steps import build_steps
            logger.info("📘 튜토리얼 시작 요청")
            try:
                NotificationCenter.toast("📘 튜토리얼 시작", level="info", duration_ms=2000)
            except Exception:
                pass
            if not hasattr(self, "_tutorial") or self._tutorial is None:
                steps = build_steps()
                logger.info(f"📘 튜토리얼 단계 {len(steps)}개 생성")
                self._tutorial = TutorialOverlay(self, steps)
                self._tutorial.finished.connect(self._on_tutorial_finished)
            self._tutorial.idx = 0
            self._tutorial.show_at()
            print(f"[TUTORIAL TRACE] show_at done. visible={self._tutorial.isVisible()} "
                  f"geom={self._tutorial.geometry().getRect()} "
                  f"parent={self._tutorial.parentWidget()}")
            logger.info(f"📘 튜토리얼 표시: visible={self._tutorial.isVisible()} "
                         f"geom={self._tutorial.geometry().getRect()}")
        except Exception:
            logger.exception("❌ 튜토리얼 시작 실패")
            try:
                NotificationCenter.toast("❌ 튜토리얼 시작 실패", level="danger", duration_ms=4000)
            except Exception:
                pass

    def _on_update_available(self, current: str, latest: str, url: str) -> None:
        """Show red-dot badge on the settings icon + one-shot toast/dialog.

        Called when UpdateChecker confirms a newer release tag than the
        running build. The badge persists for the rest of the session
        (cleared on app restart or manual dismiss). The dialog is shown
        only once per session so an idle user with the app open doesn't
        get re-nagged every 6 hours.
        """
        try:
            self.activity.set_badge("settings", True)
        except Exception:
            logger.exception("update-check: 사이드바 배지 표시 실패")
        try:
            NotificationCenter.toast(
                f"🔔 새 버전 {latest} (현재 v{current}) — 설정 → 정보에서 확인",
                level="info", duration_ms=5000,
            )
        except Exception:
            pass
        if self._update_prompted:
            return
        self._update_prompted = True
        # 자동 다이얼로그는 세션당 한 번만. 사용자가 ‘나중에’를 누르면
        # 다음 실행 때 다시 안내한다 (백그라운드 6시간 폴링은 계속).
        try:
            from PySide6.QtWidgets import QMessageBox
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            ret = QMessageBox.question(
                self, "QuickCast 업데이트",
                f"새 버전 {latest}가 공개되었습니다.\n"
                f"현재 버전: v{current}\n\n"
                "다운로드 페이지를 지금 열까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret == QMessageBox.Yes and url:
                QDesktopServices.openUrl(QUrl(url))
        except Exception:
            logger.exception("update-check: 업데이트 다이얼로그 표시 실패")

    def _on_tutorial_finished(self, completed: bool) -> None:
        # Don't persist the "completed" flag when running in design-
        # preview mode — the desktop shortcut sets QUICKCAST_FORCE_TUTORIAL
        # so it always launches the tutorial fresh.
        import os
        if os.environ.get("QUICKCAST_FORCE_TUTORIAL"):
            return
        self.settings.tutorial_completed = True
        self.save_debounced()

    # ───────── persistence ─────────
    def save_debounced(self) -> None:
        self._dirty = True
        self._save_timer.start()

    def _do_save(self) -> None:
        if not self._dirty:
            return
        try:
            self.settings.save()
            self._dirty = False
            logger.debug("settings saved")
            NotificationCenter.toast("설정 저장됨", level="success", duration_ms=1200)
        except Exception:
            logger.exception("settings.save() failed")
            NotificationCenter.toast("저장 실패 — 로그 확인", level="danger", duration_ms=3000)

    def _poll_save(self) -> None:
        if self._dirty:
            self._do_save()

    def _save_theme_if_changed(self) -> None:
        # Theme id is on the active token set — store its name.
        from quickcast.ui.design.tokens import T
        new_theme = T.name.lower() if T.name else "graphite"
        if self.settings.theme != new_theme:
            self.settings.theme = new_theme
            self.save_debounced()

    # ───────── controller wiring ─────────
    def _wire_controller_to_ui(self) -> None:
        """Forward controller.on_analysis (capture thread) → UI thread.

        Also flips the dashboard preview to "external mode" — its synthetic
        frame timer is disabled and we feed it real captured frames here.
        """
        from quickcast.core.recognition import FrameAnalysis
        from quickcast.core.capture import Frame as CapFrame

        self._mb = _AnalysisMailbox()
        self._frame_mb = _FrameMailbox()

        # Find the dashboard preview widget so we can feed real frames.
        dash = self._sections.get("dashboard", (None, None))[1]
        self._dash_preview = getattr(dash, "_dashboard_preview", None) if dash else None
        if self._dash_preview is not None:
            self._dash_preview.set_external_mode(True)

        def _on_analysis(analysis: FrameAnalysis, frame: CapFrame) -> None:
            self._mb.sig.emit(
                analysis.hp, analysis.mp,
                float(analysis.pk_score), float(analysis.potion_score),
                analysis.pk_detected, analysis.potion_empty,
                float(self.settings.capture_fps),
            )
            # Marshal the BGRA frame + analysis into the UI thread.
            self._frame_mb.sig.emit(frame.image, analysis,
                                     float(self.settings.capture_fps))

        def _on_ui(hp, mp, pk_s, po_s, pk_d, po_e, fps):
            sb = self.status_bar
            sb.update_hp(hp); sb.update_mp(mp)
            sb.update_pk(pk_d); sb.update_potion(po_e)
            sb.update_fps(float(fps))
            bus.live_scores.emit(hp, mp, pk_s, po_s, pk_d, po_e, float(fps))
            # First frame from controller — flip capture dot to ON.
            if not self._capture_seen:
                self._capture_seen = True
                lbl = f"{self.settings.capture_window_title or '모니터'} · {fps:.0f}fps"
                sb.update_capture(True, lbl)
                bus.capture_state_changed.emit(True, lbl)

        def _on_frame(image, analysis, fps):
            if self._dash_preview is not None:
                self._dash_preview.feed_frame(image, analysis, fps)
            # Re-broadcast on bus so secondary previews (fullscreen,
            # picture-in-picture, …) can mirror the same frame.
            bus.live_frame.emit(image, analysis, fps)
            # Pipe source dimensions + window DPI to any diagnostic UI
            # (capture card label). Comes from the active capture backend
            # so window-capture and monitor-capture both contribute.
            try:
                cap = getattr(self.controller, "capture", None)
                if cap is not None:
                    sw, sh = getattr(cap, "last_source_size", (0, 0))
                    sdpi = int(getattr(cap, "last_window_dpi", 96))
                    bus.capture_source_info.emit(int(sw), int(sh), sdpi)
            except Exception:
                pass
            # Forward per-overlay match scores so the capture section's
            # overlay-close card can update its live "점수: N" labels.
            ov = getattr(analysis, "overlay_matches", None)
            if ov:
                payload = {
                    ov_id: {"score": int(m.score), "detected": bool(m.detected)}
                    for ov_id, m in ov.items()
                }
                bus.overlay_scores.emit(payload)

        self._mb.sig.connect(_on_ui, Qt.QueuedConnection)
        self._frame_mb.sig.connect(_on_frame, Qt.QueuedConnection)
        self.controller.on_analysis = _on_analysis

    # ───────── activity bar bottom items ─────────
    def _on_section_changed(self, sid: str) -> None:
        if sid == "palette":
            self._toggle_theme()
            self.activity.set_active(self._current or "dashboard")
        elif sid == "help":
            # Open the Command Palette (same as Ctrl+K). It already lists
            # every action including theme/master/quit/section navigation,
            # so it doubles as the "help / shortcut" surface.
            self.activity.set_active(self._current or "dashboard")
            self._open_palette()

    def _toggle_theme(self) -> None:
        cur = self.settings.theme
        nxt = "paper" if cur == "graphite" else "graphite"
        app = QApplication.instance()
        if app is not None:
            design_themes.apply_theme(app, nxt)
        self.settings.theme = nxt
        self.save_debounced()

    def _toggle_master(self) -> None:
        # Menu/shortcut path → route through the single handler so
        # countdown + controller + visual all stay in sync.
        new = not self.settings.master_switch
        self._on_master_toggled(new)

    def _on_master_toggled(self, on: bool) -> None:
        # Single source of truth for master state changes — fires
        # whether the user clicked the title-bar toggle, the floater,
        # or hit a menu shortcut. We update the visual mirror ourselves
        # so the title bar reflects the new state when the change came
        # from somewhere other than the title bar's own toggle.
        self.settings.master_switch = on
        self.set_master(on)  # visual mirror (titlebar + statusbar)
        if self.controller is not None:
            self.controller.set_master_switch(on)
        if on:
            self._start_grace_countdown(3.0)
        else:
            self._stop_grace_countdown()
        self.save_debounced()

    def _start_grace_countdown(self, seconds: float) -> None:
        if not hasattr(self, "_grace_timer"):
            self._grace_timer = QTimer(self)
            self._grace_timer.setInterval(100)
            self._grace_timer.timeout.connect(self._on_grace_tick)
        self._grace_remaining = float(seconds)
        bus.master_grace_changed.emit(self._grace_remaining)
        self._grace_timer.start()

    def _stop_grace_countdown(self) -> None:
        if hasattr(self, "_grace_timer"):
            self._grace_timer.stop()
        self._grace_remaining = 0.0
        bus.master_grace_changed.emit(0.0)

    def _on_grace_tick(self) -> None:
        self._grace_remaining -= 0.1
        if self._grace_remaining <= 0:
            self._grace_timer.stop()
            self._grace_remaining = 0.0
            bus.master_grace_changed.emit(0.0)
            logger.info("✅ 가동 시작")
        else:
            bus.master_grace_changed.emit(self._grace_remaining)

    def _on_floater_toggled(self, on: bool) -> None:
        # main.py creates a FloatingSwitch and listens to this signal to
        # attach it against the game window's HWND. AppWindow just
        # forwards the user intent.
        pass

    # ───────── command palette ─────────
    def _open_palette(self) -> None:
        actions = self._build_actions()
        palette = CommandPalette(actions, parent=self)
        palette.exec()

    def _build_actions(self) -> list[Action]:
        out: list[Action] = []
        for sid, icon, name in SECTIONS:
            out.append(Action(
                id=f"goto:{sid}", title=f"이동: {name}", hint=f"섹션 · {sid}",
                icon=icon, section="이동",
                callback=lambda _sid=sid: self.activity.set_active(_sid),
            ))
        out.append(Action(id="theme:toggle", title="테마 토글", hint="Ctrl+T",
                          icon="palette", section="테마", callback=self._toggle_theme))
        out.append(Action(id="master:toggle", title="Master 토글", hint="Ctrl+M",
                          icon="power", section="제어", callback=self._toggle_master))
        out.append(Action(id="win:max", title="최대화/복원", hint="F11",
                          icon="maximize-2", section="창", callback=self._toggle_max))
        out.append(Action(id="win:quit", title="종료",
                          icon="power", section="창", callback=self.close))
        # Tutorial — appended at the bottom so it's always discoverable.
        out.append(Action(id="tutorial:start", title="튜토리얼 다시 보기",
                          hint="Ctrl+Shift+T", icon="help-circle",
                          section="도움말", callback=self.start_tutorial))
        return out

    # ───────── section navigation ─────────
    def _activate_section_by_id(self, sid: str) -> None:
        """External code (recovery pick, etc.) requested a tab switch."""
        if sid not in self._sections:
            logger.warning(f"activate_section: unknown id '{sid}'")
            return
        logger.debug(f"activate_section → {sid}")
        self.activity.set_active(sid)

    # ───────── connection toggles ─────────
    def _toggle_arduino(self) -> None:
        if self.arduino is None:
            return
        if getattr(self.arduino, "connected", False):
            try:
                self.arduino.close()
            except Exception:
                logger.exception("arduino.close() failed")
            self.status_bar.update_arduino(False, "미연결")
            bus.arduino_state_changed.emit(False, "미연결")
            NotificationCenter.toast("Arduino 연결 해제", level="info")
            return
        # Empty port = ask the backend to auto-detect (looks for Arduino /
        # CH340 / usb-serial in COM port descriptions).
        requested_port = self.settings.arduino_port
        try:
            self.arduino.port = requested_port
            self.arduino.baud = self.settings.arduino_baud
            self.arduino.connect()
        except Exception as exc:
            logger.exception("arduino.connect() failed")
            NotificationCenter.toast(f"Arduino 연결 실패: {exc}", level="danger", duration_ms=4000)
            return
        ok = bool(getattr(self.arduino, "connected", False))
        # If auto-detect picked a port, persist it back into settings so the
        # field shows the actual COMx going forward.
        actual_port = getattr(self.arduino, "port", "") or ""
        if ok and actual_port and actual_port != self.settings.arduino_port:
            self.settings.arduino_port = actual_port
            bus.settings_dirty.emit()
        label = (actual_port or requested_port) if ok else "연결 실패"
        self.status_bar.update_arduino(ok, label)
        bus.arduino_state_changed.emit(ok, label)
        NotificationCenter.toast(
            f"Arduino {'연결됨' if ok else '연결 실패'} ({label})",
            level="success" if ok else "danger",
        )

    def _toggle_telegram(self) -> None:
        if self.telegram is None:
            return
        if getattr(self.telegram, "connected", False):
            try:
                self.telegram.close()
            except Exception:
                logger.exception("telegram.close() failed")
            self.status_bar.update_telegram(False, "미연결")
            bus.telegram_state_changed.emit(False, "미연결")
            NotificationCenter.toast("Telegram 연결 해제", level="info")
            return
        if not self.settings.telegram_token:
            NotificationCenter.toast(
                "Telegram 토큰 미입력 (Settings → 토큰 설정)", level="warning",
            )
            return
        try:
            self.telegram.token = self.settings.telegram_token
            self.telegram.chat_id = self.settings.telegram_chat_id
            self.telegram.connect()
        except Exception as exc:
            logger.exception("telegram.connect() failed")
            NotificationCenter.toast(f"Telegram 연결 실패: {exc}", level="danger", duration_ms=4000)
            return
        ok = bool(getattr(self.telegram, "connected", False))
        label = "연결됨" if ok else "연결 실패"
        self.status_bar.update_telegram(ok, label)
        bus.telegram_state_changed.emit(ok, label)
        NotificationCenter.toast(
            f"Telegram {'연결됨' if ok else '연결 실패'}",
            level="success" if ok else "danger",
        )

    # ───────── input backend swap ─────────
    def _swap_input_backend(self, backend_id: str) -> None:
        """User changed input method — point controller.input at the
        matching backend so subsequent slot fires use the new method.

        Without this, slots keep going through whatever backend was
        chosen at boot (typically Arduino), which is why the test
        button worked focus-free but slot fires required focus.
        """
        if self.controller is None:
            return
        backends = getattr(self.controller, "_backends", {}) or {}
        new_backend = backends.get(backend_id)
        if new_backend is None:
            NotificationCenter.toast(
                f"백엔드 '{backend_id}' 인스턴스가 없음", level="warning",
            )
            return
        # Make sure the Win32 backends are pointed at the current game HWND.
        target_hwnd = getattr(self.controller, "_auto_hwnd", 0)
        if backend_id in ("postmessage", "attachinput") and target_hwnd:
            if hasattr(new_backend, "set_target"):
                new_backend.set_target(int(target_hwnd),
                                        self.settings.capture_window_title or "")
        self.controller.input = new_backend
        logger.info(f"controller.input swapped → {backend_id}")
        NotificationCenter.toast(
            f"입력 방식 전환 → {backend_id}", level="success",
        )

    # ───────── test input ─────────
    def _test_input(self, backend_id: str, key: str) -> None:
        """Send a single key via the specified backend so the user can
        verify the game actually receives it. PostMessage / AttachInput
        only fire if a target HWND has been set on them."""
        if not key:
            NotificationCenter.toast("테스트 키가 비어있음", level="warning")
            return
        backends = getattr(self.controller, "_backends", {}) if self.controller else {}
        backend = backends.get(backend_id)
        if backend is None:
            NotificationCenter.toast(
                f"백엔드 '{backend_id}'를 찾을 수 없음", level="warning",
            )
            return

        target_hwnd = getattr(self.controller, "_auto_hwnd", 0) if self.controller else 0
        if backend_id in ("postmessage", "attachinput"):
            if not target_hwnd:
                NotificationCenter.toast(
                    "게임창 미선택 — Capture 페이지에서 선택 후 다시 테스트",
                    level="warning",
                )
                return
            if hasattr(backend, "set_target"):
                backend.set_target(int(target_hwnd), self.settings.capture_window_title or "")

        if backend_id == "arduino" and not getattr(backend, "connected", False):
            NotificationCenter.toast(
                "Arduino 미연결 — '연결' 먼저 누르세요", level="warning",
            )
            return

        try:
            backend.send_key(key)
        except Exception as exc:
            logger.exception(f"test_input '{backend_id}' send_key failed")
            NotificationCenter.toast(
                f"테스트 실패 ({backend_id}): {exc}",
                level="danger", duration_ms=4000,
            )
            return
        NotificationCenter.toast(
            f"'{key}' 전송됨 → {backend_id}", level="success", duration_ms=1800,
        )

    # ───────── recovery step test ─────────
    def _test_recovery_step(self, idx: int) -> None:
        """Click once at the saved coord for step `idx`. Always fires
        regardless of master_switch state — manual verifier."""
        from quickcast.input_io.win32_input import click_at
        rec = getattr(self.settings, "recovery", None)
        if rec is None or not rec.steps:
            NotificationCenter.toast("복귀 단계가 비어있음", level="warning")
            return
        if idx < 0 or idx >= len(rec.steps):
            NotificationCenter.toast(f"잘못된 단계 번호 #{idx + 1}", level="warning")
            return
        step = rec.steps[idx]
        hwnd = int(getattr(self.controller, "_auto_hwnd", 0) or 0) if self.controller else 0
        if not hwnd:
            NotificationCenter.toast(
                "게임창 미선택 — Capture 페이지에서 선택", level="warning",
            )
            return
        # Use latest captured frame's pixel size for DPI-aware scaling.
        frame_size = None
        if self.controller is not None:
            try:
                with self.controller._latest_lock:
                    if self.controller._latest_frame is not None:
                        h, w = self.controller._latest_frame.image.shape[:2]
                        frame_size = (int(w), int(h))
            except Exception:
                frame_size = None
        try:
            if step.key:
                # Route key-press through the configured input backend
                # so test fires whatever Arduino / PostMessage / etc.
                # the user has selected.
                if self.controller is not None:
                    self.controller.input.send_key(step.key)
                NotificationCenter.toast(
                    f"단계 #{idx+1} '{step.label}' 키 '{step.key}' 전송",
                    level="success", duration_ms=2000,
                )
                return
            click_at(hwnd, int(step.x), int(step.y),
                      frame_size=frame_size, method="attach")
        except Exception as exc:
            logger.exception("recovery-test: click failed")
            NotificationCenter.toast(
                f"테스트 실패: {exc}", level="danger", duration_ms=3000,
            )
            return
        NotificationCenter.toast(
            f"단계 #{idx + 1} '{step.label}' 테스트 클릭 → ({step.x},{step.y})",
            level="success", duration_ms=2000,
        )

    # ───────── overlay-close test fire ─────────
    def _on_overlay_close_test(self, ov_id: str, close_key: str) -> None:
        """User clicked '테스트 ESC' for one of the overlay-close rows.
        Send the configured close_key once through the active input
        backend so they can verify the wire end-to-end (window
        targeting, key path) without triggering a real popup."""
        if self.controller is None:
            return
        key = (close_key or "esc").strip().lower() or "esc"
        try:
            self.controller.input.send_key(key)
            NotificationCenter.toast(
                f"🚪 {ov_id} 테스트 — '{key}' 사용",
                level="success", duration_ms=2000,
            )
        except Exception:
            logger.exception(f"overlay-close test fire failed: {ov_id}")
            NotificationCenter.toast(
                "오버레이 테스트 실패 — 로그 확인", level="danger", duration_ms=3000,
            )

    # ───────── item-close test fire ─────────
    def _on_item_close_test(self) -> None:
        """User clicked '테스트' next to the item-close coord — fire one
        click at the saved (x, y) right now so they can verify it's
        on target. Goes through the same MacroController path that the
        interval timer uses, so the click pathway is identical."""
        if self.controller is None:
            return
        fn = getattr(self.controller, "fire_item_close_now", None)
        if fn is None:
            return
        try:
            fn()
            NotificationCenter.toast(
                "🖱️ 아이템닫기 테스트 클릭 전송 — 게임에서 확인하세요",
                level="success", duration_ms=2000,
            )
        except Exception:
            logger.exception("item-close test fire failed")
            NotificationCenter.toast(
                "테스트 클릭 실패 — 로그 확인", level="danger", duration_ms=3000,
            )

    # ───────── OCR digit-templates reload ─────────
    def _on_digit_templates_changed(self) -> None:
        """User saved a new set of digit templates — make the live
        recognizer pick them up without an app restart."""
        if self.controller is None:
            return
        rec = getattr(self.controller, "recognizer", None)
        if rec is not None and hasattr(rec, "reload_digits"):
            try:
                rec.reload_digits()
                NotificationCenter.toast(
                    "🔤 OCR 글자 학습 적용됨", level="success", duration_ms=1500,
                )
            except Exception:
                logger.exception("recognizer.reload_digits failed")

    # ───────── slot cooldown broadcaster ─────────
    def _broadcast_slot_cooldowns(self) -> None:
        """Poll controller.slot_manager.cooldown for each known slot id
        and emit `{sid: (remaining, total)}` on the bus. The dashboard
        sidebar's skill rows listen and update their countdown labels
        and the bottom-edge progress gauge.
        """
        if self.controller is None:
            return
        sm = getattr(self.controller, "slot_manager", None)
        cd = getattr(sm, "cooldown", None) if sm is not None else None
        if cd is None:
            return
        try:
            slots = self.settings.slots
        except Exception:
            return
        payload: dict = {}
        for sid, slot in slots.items():
            try:
                rem = float(cd.remaining(sid))
                total = float(getattr(slot, "cooltime", 0.0) or 0.0)
            except Exception:
                rem, total = 0.0, 0.0
            payload[sid] = (rem, total)
        # Diagnostic — log once per cooldown cycle when a slot transitions
        # ready → cooling. Cleared when the slot becomes ready again so
        # repeated fires log fresh. Goes through loguru → dashboard log card.
        cooling_now: set[str] = set()
        for sid, (rem, total) in payload.items():
            if rem > 0.05:
                cooling_now.add(sid)
                if sid not in self._cd_logged:
                    self._cd_logged.add(sid)
                    logger.info(f"⏱️ 슬롯 '{sid}' 쿨다운 시작 {rem:.1f}/{total:.1f}s")
        # Clear log latch for slots that finished cooling.
        for sid in list(self._cd_logged):
            if sid not in cooling_now:
                self._cd_logged.discard(sid)
        bus.slot_cooldown_tick.emit(payload)

    def _reset_slot_cooldown(self, slot_id: str) -> None:
        """Manual cooldown reset from a sidebar row's ✕ button — clears
        the cooldown so the slot can re-fire on the next eligible tick.
        """
        if self.controller is None:
            return
        sm = getattr(self.controller, "slot_manager", None)
        cd = getattr(sm, "cooldown", None) if sm is not None else None
        if cd is None:
            return
        try:
            cd.reset(slot_id)
        except Exception:
            logger.exception(f"cooldown reset failed for slot '{slot_id}'")
            return
        self._cd_logged.discard(slot_id)
        logger.info(f"🧹 슬롯 '{slot_id}' 쿨다운 수동 해제")
        # Push an immediate broadcast so the row clears without waiting
        # for the next 100ms poll cycle.
        self._broadcast_slot_cooldowns()

    # ───────── auto game-window detection ─────────
    def _auto_find_game_window(self) -> None:
        """Re-scan visible windows every 5s for a Lineage W match.

        Runs only when the controller has no live HWND bound (e.g. user
        launched QuickCast before the game, or the game crashed and was
        restarted). On a successful match: persists the title, calls the
        existing hot-swap path so capture / input backends rewire, and
        emits ``game_window_found`` so the floater can re-attach.
        """
        if self.controller is None:
            return
        # Skip when we already have an alive hwnd — the existing capture
        # path (and minimised-window grace logic) handles the live case.
        try:
            from quickcast.utils.window_finder import (
                find_window, is_window_alive, _window_title,
            )
        except Exception:
            return
        cur_hwnd = int(getattr(self.controller, "_auto_hwnd", 0) or 0)
        if cur_hwnd and is_window_alive(cur_hwnd):
            return

        # Prefer the user's saved title (if any), then the configured
        # default patterns. Mirrors _build_capture() in main.py.
        patterns: list[str] = []
        saved = (self.settings.capture_window_title or "").strip()
        if saved:
            patterns.append(saved)
        patterns.extend(getattr(self.settings, "game_window_patterns", []) or [])
        if not patterns:
            return

        hwnd = find_window(patterns)
        if not hwnd:
            return

        full_title = _window_title(hwnd) or saved or "리니지W"
        # Persist a stable substring so a character-name change doesn't
        # break the saved pick (matches main._build_capture behaviour).
        stable = (
            full_title.split(" | ", 1)[0].split("|", 1)[0].strip()
            or full_title
        )
        if self.settings.capture_window_title != stable:
            self.settings.capture_window_title = stable
            self.save_debounced()

        logger.success(f"🎯 게임창 자동 감지: '{full_title}' (hwnd 0x{hwnd:X})")
        try:
            NotificationCenter.toast(
                f"🎯 게임창 자동 연결: {full_title[:24]}",
                level="success", duration_ms=2200,
            )
        except Exception:
            pass

        # Trigger the existing hot-swap path so capture + input backends
        # are rebuilt. _hot_swap_capture reads capture_window_title we
        # just persisted above and falls through to find_window itself.
        try:
            self._hot_swap_capture()
        except Exception:
            logger.exception("auto-find: hot_swap_capture failed")

        # Broadcast for the floater + anything else that wants to react.
        try:
            bus.game_window_found.emit(int(hwnd), full_title)
        except Exception:
            pass

    # ───────── capture hot-swap ─────────
    def _hot_swap_capture(self) -> None:
        """Swap controller.capture to the saved capture_window_title (live).

        Called when the user picks a new game window from the Capture
        section. Tests PrintWindow first; falls back to mss WindowCapture
        if the target app refuses (returns all-black). Updates the Win32
        input backends to match.
        """
        if self.controller is None:
            return
        title = self.settings.capture_window_title
        if not title:
            return
        try:
            from quickcast.utils.window_finder import (
                find_window, get_client_rect_screen, get_window_rect, _window_title,
            )
            from quickcast.core.capture import WindowCapture
            from quickcast.core.window_print_capture import WindowPrintCapture
        except Exception:
            logger.exception("capture-swap: import failed")
            return

        hwnd = find_window([title])
        if not hwnd:
            NotificationCenter.toast(
                f"'{title[:30]}' 창을 찾을 수 없습니다", level="warning",
            )
            return
        rect = get_client_rect_screen(hwnd) or get_window_rect(hwnd)
        if rect is None or rect.width <= 0 or rect.height <= 0:
            NotificationCenter.toast(
                "창 영역을 읽을 수 없음 (최소화?)", level="warning",
            )
            return

        full_title = _window_title(hwnd) or title

        def _is_blank(frame) -> bool:
            try:
                return float(frame.image[:, :, :3].mean()) < 2.0
            except Exception:
                return True

        new_cap = None
        used = ""
        try:
            new_cap = WindowPrintCapture(hwnd=hwnd, label=full_title)
            test = new_cap.grab()
            if _is_blank(test):
                raise RuntimeError("blank frame")
            used = "PrintWindow"
        except Exception:
            try:
                new_cap = WindowCapture(hwnd=hwnd, label=full_title)
                test = new_cap.grab()
                if _is_blank(test):
                    raise RuntimeError("blank frame")
                used = "mss"
            except Exception as e:
                NotificationCenter.toast(
                    f"캡처 전환 실패: {e}", level="danger", duration_ms=4000,
                )
                return

        # Swap and tear down old.
        try:
            old = self.controller.capture
            self.controller.capture = new_cap
            try:
                old.close()
            except Exception:
                pass
            # Refresh Win32 input backends.
            backends = getattr(self.controller, "_backends", {}) or {}
            for name in ("postmessage", "attachinput"):
                b = backends.get(name)
                if b is not None and hasattr(b, "set_target"):
                    b.set_target(hwnd, full_title)
            self.controller._auto_hwnd = hwnd
            self.controller._auto_title = full_title
            self.status_bar.update_capture(
                True, f"{full_title[:24]} {rect.width}×{rect.height}",
            )
            NotificationCenter.toast(
                f"캡처 전환됨 ({used})", level="success", duration_ms=1800,
            )
            logger.success(
                f"🎯 캡처 전환: {used} → '{full_title}' "
                f"({rect.width}x{rect.height})"
            )
            # Broadcast so the floater (and anything else holding an
            # hwnd reference) re-attaches to the new target window.
            try:
                bus.game_window_found.emit(int(hwnd), full_title)
            except Exception:
                pass
        except Exception:
            logger.exception("capture-swap: hot-swap failed")
            NotificationCenter.toast("캡처 전환 실패 — 로그 확인", level="danger")

    # ───────── tray ─────────
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("QuickCast")
        # Use bundled icon.ico — same Lineage W icon embedded in the
        # EXE resource, so tray balloon / Alt-Tab / taskbar all match.
        self.tray.setIcon(_load_app_icon(self.style()))
        menu = QMenu()
        show_act = QAction("창 표시", self); show_act.triggered.connect(self.showNormal)
        toggle_act = QAction("Master 토글", self)
        toggle_act.triggered.connect(self._toggle_master)
        stop_alarms_act = QAction("울리는 알림 모두 정지", self)
        stop_alarms_act.triggered.connect(self._stop_all_repeating_alarms)
        quit_act = QAction("종료", self)
        quit_act.triggered.connect(self._request_quit)
        menu.addAction(show_act); menu.addAction(toggle_act)
        menu.addSeparator(); menu.addAction(stop_alarms_act)
        menu.addSeparator(); menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self.showNormal() if r == QSystemTrayIcon.DoubleClick else None
        )
        self.tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("⚠️ 시스템 트레이 사용 불가 — Windows 알림 미표시")

    @Slot(str, str)
    def show_toast(self, title: str, message: str) -> None:
        if hasattr(self, "tray") and self.tray.isVisible():
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 5000)

    def _on_alarm_fired(self, label: str, hour: int, minute: int,
                         days_csv: str, mode: str, repeat_min: int) -> None:
        """Alarm receiver — fires Windows tray balloon + in-app toast +
        sound. Schedules repeat reminders if `repeat_min` > 0 and
        auto-stops them after `settings.alarm_auto_close_minutes`."""
        time_str = f"{hour:02d}:{minute:02d}"
        logger.info(f"⏰ 알람 '{label}'  {time_str}")
        # Effective repeat: alarm-level value wins; else fall back to global.
        eff_repeat = repeat_min if repeat_min > 0 else int(self.settings.alarm_repeat_minutes or 0)
        eff_auto_close = int(self.settings.alarm_auto_close_minutes or 0)

        # Track active repeaters keyed by (label, time_str) so a future
        # re-arming of the same alarm doesn't accumulate ghost timers.
        if not hasattr(self, "_alarm_repeaters"):
            self._alarm_repeaters: dict[tuple, QTimer] = {}
        key = (label, time_str)
        old = self._alarm_repeaters.pop(key, None)
        if old is not None:
            try:
                old.stop()
                old.deleteLater()
            except Exception:
                pass

        # Fire the first notification immediately.
        self._fire_alarm_notification(label, time_str)

        # Schedule recurring re-fires + auto-stop guard.
        if eff_repeat > 0:
            t = QTimer(self)
            t.setInterval(eff_repeat * 60_000)
            t.timeout.connect(lambda l=label, ts=time_str: self._fire_alarm_notification(l, ts))
            t.start()
            self._alarm_repeaters[key] = t
            logger.info(f"🔁 알람 '{label}' {eff_repeat}분 간격으로 재알림")
            # Notify the dashboard sidebar that this label is now in
            # an active repeat cycle (it'll show a 정지 button).
            bus.alarm_repeat_active.emit(label, True)

            if eff_auto_close > 0:
                # Stop the repeater after auto_close_minutes.
                def _stop_repeat(k=key, lbl=label):
                    timer = self._alarm_repeaters.pop(k, None)
                    if timer is not None:
                        timer.stop(); timer.deleteLater()
                        logger.info(f"⏹️ 알람 '{lbl}' 반복 종료 (자동, {eff_auto_close}분 경과)")
                        bus.alarm_repeat_active.emit(lbl, False)
                QTimer.singleShot(eff_auto_close * 60_000, _stop_repeat)

    def _stop_alarm_by_label(self, label: str) -> None:
        """Manual stop request from the dashboard sidebar 정지 button."""
        if not hasattr(self, "_alarm_repeaters"):
            self._alarm_repeaters = {}
        # Match all (label, time) entries with that label.
        killed = 0
        for k in list(self._alarm_repeaters.keys()):
            if k[0] == label:
                t = self._alarm_repeaters.pop(k)
                try:
                    t.stop(); t.deleteLater()
                except Exception:
                    pass
                killed += 1
        if killed:
            logger.info(f"⏹️ 알람 '{label}' 반복 정지 (수동)")
            bus.alarm_repeat_active.emit(label, False)

    def _stop_all_repeating_alarms(self) -> None:
        """Tray-menu action: kill every active repeat timer right now."""
        if not hasattr(self, "_alarm_repeaters"):
            self._alarm_repeaters = {}
        n = len(self._alarm_repeaters)
        labels = {k[0] for k in self._alarm_repeaters.keys()}
        for key, t in list(self._alarm_repeaters.items()):
            try:
                t.stop(); t.deleteLater()
            except Exception:
                pass
        self._alarm_repeaters.clear()
        for lbl in labels:
            bus.alarm_repeat_active.emit(lbl, False)
        if n:
            logger.info(f"🛑 반복 알림 {n}개 정지됨")
        try:
            NotificationCenter.toast(
                f"반복 알림 {n}개 정지됨" if n else "정지할 반복 알림이 없습니다",
                level="info", duration_ms=3000,
            )
        except Exception:
            pass

    def _fire_alarm_notification(self, label: str, time_str: str) -> None:
        """One-shot alarm notification — tray + in-app + sound."""
        msg = f"{label} ({time_str})"
        if self.settings.notify_on_alarm_toast and hasattr(self, "tray") and self.tray.isVisible():
            try:
                self.tray.showMessage(
                    "⏰ QuickCast 알람", msg,
                    QSystemTrayIcon.Information, 8000,
                )
            except Exception:
                pass
        try:
            NotificationCenter.toast(f"⏰ {msg}", level="info", duration_ms=10_000)
        except Exception:
            pass
        try:
            from quickcast.notify.sound import play_alarm
            sid = self.settings.alarm_sound or "default"
            if sid != "off":
                play_alarm(times=2, interval_s=1.5, sound_id=sid)
        except Exception:
            pass

    @Slot(object)
    def show_alarm_popup(self, alarm) -> None:
        """Custom alarm popup — countdown + repeat beeps. Mirrors HTML behaviour."""
        logger.info(f"show_alarm_popup: '{alarm.label}' {alarm.hour:02d}:{alarm.minute:02d}")
        # In-app NotificationCenter toast as a reliable backup that
        # works even if the system tray balloon is suppressed by
        # Windows Focus-Assist or the AlarmPopup window fails to draw.
        try:
            NotificationCenter.toast(
                f"⏰ {alarm.label} ({alarm.hour:02d}:{alarm.minute:02d})",
                level="info", duration_ms=8000,
            )
        except Exception:
            logger.exception("alarm: NotificationCenter toast failed")
        from quickcast.ui.alarm_popup import AlarmPopup
        time_str = f"{alarm.hour:02d}:{alarm.minute:02d}"
        if alarm.days and len(alarm.days) < 7:
            day_names = ["일", "월", "화", "수", "목", "금", "토"]
            days_text = ", ".join(day_names[d] for d in alarm.days)
        else:
            days_text = "매일"
        mode_text = "반복" if alarm.mode == "repeat" else "1회만"
        info = f"<div>{days_text}</div><div>{mode_text}</div>"
        popup = AlarmPopup(
            title=alarm.label,
            time_str=time_str,
            info_html=info,
            auto_close_minutes=self.settings.alarm_auto_close_minutes,
            repeat_minutes=(self.settings.alarm_repeat_minutes
                            if alarm.repeat_minutes == 0 else alarm.repeat_minutes),
            parent=self,
        )
        popup.show()
        if not hasattr(self, "_open_popups"):
            self._open_popups = []
        self._open_popups.append(popup)
        popup.closed.connect(lambda: self._open_popups.remove(popup))

    # ───────── lifecycle ─────────
    def _request_quit(self) -> None:
        """Tray menu "종료" path — flips the quit flag so closeEvent
        lets the close through, then asks Qt to quit. Without the flag,
        closeEvent would intercept and hide the window to tray, making
        it look like the menu had no effect."""
        self._quitting = True
        # Hide the tray icon proactively so the next quit attempt (if
        # something else cancels close) won't re-hide to tray.
        if hasattr(self, "tray"):
            try: self.tray.hide()
            except Exception: pass
        QApplication.instance().quit()

    def closeEvent(self, e) -> None:
        self._do_save()
        # Real quit path (tray menu, Cmd+Q, app.quit()) — let it close.
        if self._quitting:
            super().closeEvent(e)
            return
        if hasattr(self, "tray") and self.tray.isVisible():
            self.hide()
            self.tray.showMessage(
                "QuickCast", "트레이로 최소화되었습니다.",
                QSystemTrayIcon.Information, 2000,
            )
            e.ignore()
        else:
            super().closeEvent(e)


class _AnalysisMailbox(QObject):
    """Signal carrier — bridges capture thread to UI thread."""
    sig = Signal(int, int, float, float, bool, bool, float)


class _FrameMailbox(QObject):
    """Carries (numpy frame image, FrameAnalysis, fps) to the UI thread."""
    sig = Signal(object, object, float)


__all__ = ["AppWindow"]
