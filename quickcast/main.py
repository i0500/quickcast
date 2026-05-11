"""Application bootstrap.

Constructs the dependency graph, starts capture/control threads, and
hands off to Qt's event loop.
"""
from __future__ import annotations

import sys

# Force per-monitor v2 DPI awareness BEFORE Qt initialises so the
# captured frame size, GetClientRect, and PostMessage lParam all live
# in the same physical-pixel coordinate space — otherwise the recovery
# clicker lands at a "엉뚱한" (wrong) spot whenever the user runs at
# 125%/150% Windows scaling.
def _force_pmv2_dpi() -> None:
    try:
        import ctypes
        # -4 == DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            # Fallback for older Win10 (1607-)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _register_app_user_model_id() -> None:
    """Set an explicit AppUserModelID so Windows associates our tray
    notifications with this app (and not the generic 'Python.exe'
    grouping). Without this, Windows 11 can silently suppress our
    showMessage balloons under tightened notification policy.
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "QuickCast.Desktop.App"
        )
    except Exception:
        pass


_force_pmv2_dpi()
_register_app_user_model_id()

from PySide6.QtWidgets import QApplication

from quickcast.config import Settings
from quickcast.core.capture import CaptureSource, MonitorCapture, WindowCapture
from quickcast.core.window_print_capture import WindowPrintCapture
from quickcast.core.controller import MacroController
from quickcast.core.recognition import Recognizer
from quickcast.input_io.arduino import ArduinoBackend
from quickcast.input_io.win32_input import AttachInputBackend, PostMessageBackend
from quickcast.notify.alarm import AlarmEvent, AlarmScheduler
from quickcast.notify.telegram import TelegramNotifier
from quickcast.slots.slot_manager import SlotManager
from quickcast.ui.app_window import AppWindow
from quickcast.ui.design.themes import apply_theme as apply_design_theme
from quickcast.ui.design import fonts as design_fonts
from quickcast.utils.logger import logger, setup as setup_logging
from quickcast.utils.window_finder import find_window


def _on_alarm(event: AlarmEvent, telegram: TelegramNotifier) -> None:
    msg = f"⏰ {event.alarm.label} ({event.fired_at.strftime('%H:%M')})"
    if telegram.connected:
        telegram.send_text(msg)


def _build_capture(settings: Settings) -> CaptureSource:
    """Pick a window capture if configured + window currently exists, else monitor.

    Window capture uses PrintWindow (matches browser getDisplayMedia behaviour:
    works on background/occluded/minimised windows, multi-monitor safe).

    Order of precedence:
      1. settings.capture_window_title — exact substring match (last user pick)
      2. settings.game_window_patterns — auto-detect Lineage W on first run
         (handles "리니지W | 캐릭명" form because find_window does substring match)
      3. monitor fallback
    """
    from quickcast.utils.window_finder import _window_title  # for resolved title
    if settings.capture_window_title:
        hwnd = find_window([settings.capture_window_title])
        if hwnd:
            full_title = _window_title(hwnd) or settings.capture_window_title
            logger.info(f"🎯 캡처 대상: {full_title}")
            return WindowPrintCapture(hwnd=hwnd, label=full_title)
        logger.warning(
            f"⚠️ 저장된 창 '{settings.capture_window_title}' 못 찾음 — 자동 감지 시도"
        )

    # Auto-detect using configured patterns (default includes 리니지W, PURPLE, etc.)
    hwnd = find_window(settings.game_window_patterns)
    if hwnd:
        full_title = _window_title(hwnd) or "리니지W"
        # Persist a stable substring so next launch goes straight to it.
        # Use the part before " | " so character-name changes still match.
        stable = full_title.split(" | ", 1)[0].split("|", 1)[0].strip() or full_title
        settings.capture_window_title = stable
        try:
            settings.save()
        except Exception:
            pass
        logger.success(
            f"🎯 게임창 자동 감지: '{full_title}' "
            f"→ 저장 키 '{stable}'"
        )
        return WindowPrintCapture(hwnd=hwnd, label=full_title)

    logger.info(
        f"게임창 자동 감지 실패 → 모니터 {settings.capture_monitor_index} 캡처로 시작"
    )
    return MonitorCapture(monitor_index=settings.capture_monitor_index)


def run() -> None:
    setup_logging()
    # Bridge loguru → bus.log_entry so the dashboard log card sees every
    # INFO+ message. Qt queued connections marshal back to the GUI
    # thread automatically when the log fires from worker threads.
    try:
        from quickcast.utils.logger import add_ui_sink
        from quickcast.ui.design.signals import bus as _bus
        add_ui_sink(lambda lvl, msg: _bus.log_entry.emit(lvl, msg))
    except Exception:
        logger.exception("log → bus bridge failed")
    logger.info("🚀 QuickCast 시작")

    # Create QApplication + splash AS EARLY AS POSSIBLE so the user
    # sees a real-time progress bar across the whole init phase
    # (settings load → controller wire → window show), not just the
    # final UI-build step.
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    try:
        from pathlib import Path
        from PySide6.QtGui import QIcon
        if getattr(sys, "frozen", False):
            ico_path = Path(sys._MEIPASS) / "quickcast" / "data" / "icon.ico"
        else:
            ico_path = Path(__file__).resolve().parent / "data" / "icon.ico"
        if ico_path.exists():
            app.setWindowIcon(QIcon(str(ico_path)))
    except Exception:
        logger.exception("앱 아이콘 로드 실패")
    from quickcast.ui.components.splash import show_splash, pyi_close
    pyi_close()
    splash = show_splash()
    splash.update_message("설정 로드 중…", 5)

    loaded = Settings.load()
    splash.update_message("설정 적용 중…", 15)
    # Safety: master_switch always boots OFF regardless of what was saved.
    # The original macro behaved this way to prevent the macro firing
    # immediately on launch when the user is still arranging windows.
    if loaded.master_switch:
        logger.debug("master_switch reset to OFF on launch (safety)")
        loaded.master_switch = False
    # AttachInput was retired from the UI — migrate any saved value to
    # PostMessage so the radio doesn't show "no selection" on first load.
    if getattr(loaded, "input_backend", "") == "attachinput":
        logger.debug("input_backend migrated: attachinput → postmessage")
        loaded.input_backend = "postmessage"

    # Apply state_bridge IMMEDIATELY: every downstream consumer
    # (controller, alarms, capture, AppWindow) must use the same
    # `mock_settings` instance the UI mutates. Otherwise edits the user
    # makes (ROI / threshold / slot toggles) are visible only to the UI
    # thread and the recognition loop keeps reading stale values.
    from quickcast.ui import state_bridge
    state_bridge.install(loaded)
    from quickcast.ui.sections._mock_state import mock_settings as settings
    splash.update_message("입력 백엔드 초기화…", 25)

    # Pre-sanitise capture_window_title BEFORE we kick off the capture
    # pipeline. state_bridge already cleared dev artefacts; this is a
    # belt-and-braces guard for the auto-detect branch.
    from quickcast.ui.state_bridge import _DEV_TITLE_HINTS, _looks_like_real_game
    raw_title = settings.capture_window_title or ""
    if raw_title and (
        any(h in raw_title.lower() for h in _DEV_TITLE_HINTS)
        or not _looks_like_real_game(raw_title)
    ):
        logger.debug(f"main: cleared non-game saved capture title '{raw_title}'")
        settings.capture_window_title = ""

    # Hardware / IO — pick backend from settings; UI lets user switch
    arduino = ArduinoBackend(port=settings.arduino_port, baud=settings.arduino_baud)
    postmsg = PostMessageBackend()
    attachinp = AttachInputBackend()
    if settings.input_backend == "arduino":
        # Always TRY to connect on boot when Arduino is the chosen backend.
        # ArduinoBackend.connect() will fall back to auto_detect() (Arduino
        # / CH340 / usb-serial in COM description) when port is empty —
        # mirrors the pre-refactor behaviour where the macro just worked
        # if the board was plugged in.
        connected = arduino.connect()
        if connected and arduino.port and arduino.port != settings.arduino_port:
            settings.arduino_port = arduino.port    # persist auto-detected port
        if not connected:
            logger.debug("Arduino auto-connect skipped — manual connect needed")
    # Choose initial backend; controller swaps later if user changes it
    if settings.input_backend == "postmessage":
        active_input = postmsg
    elif settings.input_backend == "attachinput":
        active_input = attachinp
    else:
        active_input = arduino
    telegram = TelegramNotifier(token=settings.telegram_token, chat_id=settings.telegram_chat_id)
    if settings.telegram_token:
        telegram.connect()
    splash.update_message("게임창 감지 + 캡처 준비…", 40)

    # Core — choose capture source based on settings
    capture = _build_capture(settings)
    recognizer = Recognizer()
    slot_manager = SlotManager()
    splash.update_message("매크로 컨트롤러 구성…", 55)
    # Bridge controller → UI: when SlotManager auto-disables a one-shot
    # toggle (potion.use=False after fire, etc.), emit a Qt signal on
    # the design bus so combat / slots sections refresh their iOS
    # toggles. Cross-thread emit is safe — Qt uses queued connections
    # for receivers on the GUI thread.
    def _emit_slot_refresh() -> None:
        try:
            from quickcast.ui.design.signals import bus
            bus.slot_state_refresh.emit()
        except Exception:
            logger.exception("slot_state_refresh emit failed")

    controller = MacroController(
        settings=settings,
        capture=capture,
        recognizer=recognizer,
        slot_manager=slot_manager,
        input_backend=active_input,
        telegram=telegram,
        on_slot_state_changed=_emit_slot_refresh,
    )
    # Stash all backends so the UI can hot-swap them
    controller._backends = {
        "arduino": arduino,
        "postmessage": postmsg,
        "attachinput": attachinp,
    }
    # If the capture pipeline auto-detected a window, sync the Win32 input
    # backends + remember the HWND so MainWindow can attach the floater.
    auto_hwnd = 0
    auto_title = ""
    if isinstance(capture, WindowPrintCapture):
        auto_hwnd = capture.hwnd
        auto_title = capture.label
    elif settings.capture_window_title:
        from quickcast.utils.window_finder import find_window as _fw
        h = _fw([settings.capture_window_title])
        if h:
            auto_hwnd = h
            auto_title = settings.capture_window_title
    if auto_hwnd:
        postmsg.set_target(auto_hwnd, auto_title)
        attachinp.set_target(auto_hwnd, auto_title)
    controller._auto_hwnd = auto_hwnd
    controller._auto_title = auto_title

    # Alarm scheduler — fires both Telegram and (later) tray toast.
    alarms = AlarmScheduler(settings, on_alarm=lambda e: _on_alarm(e, telegram))
    alarms.start()
    splash.update_message("폰트 / 테마 적용…", 65)

    design_fonts.register()
    apply_design_theme(app, settings.theme)
    splash.update_message("UI 빌드 중…", 75)

    window = AppWindow(settings, controller, arduino, telegram, alarms)
    splash.update_message("캡처 / 제어 시작…", 92)
    window.show()
    splash.update_message("준비 완료", 100)
    splash.finish_for(window)

    # Floating switch — attached to the game window's HWND when the user
    # turns it on from the titlebar's floater toggle. Mirrors the master
    # toggle: clicking the floater flips master, and master flips it.
    from quickcast.ui.floating_switch import FloatingSwitch
    floater = FloatingSwitch()
    def _on_floater_toggled(on: bool) -> None:
        if on:
            target_hwnd = getattr(controller, "_auto_hwnd", 0)
            if not target_hwnd:
                # No saved game window — fall back to the app window.
                try:
                    target_hwnd = int(window.winId())
                except Exception:
                    target_hwnd = 0
            if target_hwnd:
                try:
                    floater.attach_to(int(target_hwnd))
                except Exception:
                    pass
        else:
            floater.detach()
    window.floater_toggled.connect(_on_floater_toggled)
    # Two-way master sync via the floater. The floater drives the same
    # logical entrypoint as the title-bar toggle so the controller
    # actually starts and the 3-second grace countdown shows up on the
    # capture preview. (Previously this only updated the visual mirror,
    # which is why clicking the floater silently did nothing.)
    floater.toggled.connect(window._on_master_toggled)
    window.master_toggled.connect(lambda on: floater.set_state(on))

    # Auto-attach floater on boot if the user has it enabled and a
    # game window was auto-detected. Mirrors the title-bar toggle so
    # the user sees the toggle as "ON" too.
    if settings.floater_enabled and auto_hwnd:
        try:
            floater.attach_to(int(auto_hwnd))
            window.title_bar.floater_toggle.set_state(True, animate=False)
        except Exception:
            logger.exception("floater auto-attach failed")

    # Re-attach the floater whenever the system rebinds to a new game
    # window — either through the user's manual Capture-section pick
    # OR the periodic auto-detection in AppWindow. Without this the
    # floater would silently keep tracking the dead HWND from before
    # the game restart / window swap.
    from quickcast.ui.design.signals import bus as _gw_bus
    def _on_game_window_found(hwnd: int, _title: str) -> None:
        if not settings.floater_enabled:
            return
        try:
            floater.attach_to(int(hwnd))
        except Exception:
            logger.exception("floater re-attach failed")
        # Keep the title-bar toggle in sync (in case this is the
        # first window we've ever seen this session).
        try:
            window.title_bar.floater_toggle.set_state(True, animate=False)
        except Exception:
            pass
    _gw_bus.game_window_found.connect(_on_game_window_found)
    # Persist the floater state when the user toggles it via title bar.
    def _on_floater_state_change(on: bool) -> None:
        if settings.floater_enabled != on:
            settings.floater_enabled = on
            try:
                settings.save()
            except Exception:
                pass
    window.floater_toggled.connect(_on_floater_state_change)

    # Wire alarm to UI via bus.alarm_fired so PySide6 marshals only
    # primitive types across the alarm-thread → GUI-thread boundary.
    # Q_ARG(object, pydantic_alarm) was raising under queued connection,
    # which suppressed both toast and popup.
    from quickcast.ui.design.signals import bus as _alarm_bus
    original_cb = alarms.on_alarm
    def _bridged(e: AlarmEvent, _orig=original_cb) -> None:
        try:
            _orig(e)
        except Exception:
            logger.exception("alarm: telegram callback failed")
        try:
            days_csv = ",".join(str(d) for d in (e.alarm.days or []))
            _alarm_bus.alarm_fired.emit(
                e.alarm.label,
                int(e.alarm.hour), int(e.alarm.minute),
                days_csv,
                e.alarm.mode or "repeat",
                int(e.alarm.repeat_minutes),
            )
        except Exception:
            logger.exception("alarm: bus emit failed")
    alarms.on_alarm = _bridged

    controller.start()

    try:
        exit_code = app.exec()
    finally:
        controller.stop()
        alarms.stop()
        telegram.close()
        arduino.close()
        capture.close()
        # Save the SAME instance the UI was editing — mock_settings, not
        # the original loaded `settings` object that's now stale.
        try:
            from quickcast.ui.sections._mock_state import mock_settings
            mock_settings.save()
        except Exception:
            settings.save()
        logger.info("👋 QuickCast 종료")

    sys.exit(exit_code)


if __name__ == "__main__":
    run()
