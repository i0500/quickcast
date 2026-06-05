"""Application bootstrap.

Constructs the dependency graph, starts capture/control threads, and
hands off to Qt's event loop.
"""
from __future__ import annotations

import sys
from typing import Optional

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


def _build_window_capture(hwnd: int, label: str) -> Optional[CaptureSource]:
    """Pick a window capture backend.

    Fallback ladder, preferred order:
      1. **PrintWindow** — fastest, hwnd-bound, works on most games.
      2. **WGC (Windows.Graphics.Capture)** — hwnd-bound GPU capture,
         works on PrintWindow-refusing games (Vulkan/D3D11+) AND ignores
         overlapping windows. Win10 1809+ only.
      3. **mss WindowCapture** — last-resort, captures the monitor
         region under the window's screen rect; will include any window
         placed on top of the game.

    Each tier is verified with a quick grab() + blank-frame test so a
    "succeeded but produces black frames" backend is treated as failed.
    """
    def _is_blank(frame) -> bool:
        try:
            return float(frame.image[:, :, :3].mean()) < 2.0
        except Exception:
            return True

    # Tier 1: PrintWindow
    try:
        cap = WindowPrintCapture(hwnd=hwnd, label=label)
        test = cap.grab()
        if _is_blank(test):
            raise RuntimeError("blank frame")
        return cap
    except Exception:
        pass

    # Tier 2: WGC
    try:
        from quickcast.core.window_wgc_capture import WGCWindowCapture
        cap = WGCWindowCapture(hwnd=hwnd, label=label)
        test = cap.grab()
        if _is_blank(test):
            cap.close()
            raise RuntimeError("blank frame")
        logger.info(f"🔄 [{label[:30]}] PrintWindow 거부 — WGC로 fallback (창 전용)")
        return cap
    except Exception as e:
        # WGC unsupported (Win10 pre-1809) or pkg not installed — fall through.
        logger.debug(f"WGC 시도 실패 ({label[:24]}): {e}")

    # Tier 3: mss WindowCapture (monitor-region, may pick up overlapping windows)
    try:
        cap = WindowCapture(hwnd=hwnd, label=label)
        test = cap.grab()
        if _is_blank(test):
            raise RuntimeError("blank frame")
        logger.info(
            f"🔄 [{label[:30]}] WGC도 사용 불가 — mss로 fallback "
            f"(겹친 창은 같이 캡처됨)"
        )
        return cap
    except Exception as e:
        logger.warning(f"⚠️ [{label[:30]}] 캡처 빌드 실패: {e}")
        return None


def _build_capture_for_profile(
    profile, settings: Settings, *, allow_auto_detect: bool = True,
) -> Optional[CaptureSource]:
    """Pick a capture source for a specific ClientProfile.

    ``profile.capture_window_title`` takes precedence (last user pick on
    that tab). ``allow_auto_detect=True`` lets the function fall back to
    the shared ``settings.game_window_patterns`` auto-detect — useful for
    the active client on first launch, but disabled for client2 so its
    auto-detection doesn't grab the same window as client1.

    PrintWindow refusal is handled here too: we try mss WindowCapture as
    a fallback so a GPU-accelerated game still produces frames.

    Returns ``None`` when no window is configured and auto-detect is off
    or yields nothing — callers (e.g. client2 standby) treat None as "no
    capture for this tab yet, user will pick one in the UI". The active
    client falls back to ``MonitorCapture`` so the macro can still run.
    """
    from quickcast.utils.window_finder import _window_title  # for resolved title
    if profile.capture_window_title:
        hwnd = find_window([profile.capture_window_title])
        if hwnd:
            full_title = _window_title(hwnd) or profile.capture_window_title
            logger.info(f"🎯 [{profile.label}] 캡처 대상: {full_title}")
            cap = _build_window_capture(hwnd, full_title)
            if cap is not None:
                return cap
            # PrintWindow + mss both failed — fall through.
        else:
            logger.warning(
                f"⚠️ [{profile.label}] 저장된 창 '{profile.capture_window_title}' "
                f"못 찾음 — 자동 감지 {'시도' if allow_auto_detect else '비활성'}"
            )

    if not allow_auto_detect:
        return None

    # Auto-detect using configured patterns (default includes 리니지W, PURPLE, etc.)
    hwnd = find_window(settings.game_window_patterns)
    if hwnd:
        full_title = _window_title(hwnd) or "리니지W"
        # Persist a stable substring so next launch goes straight to it.
        # Use the part before " | " so character-name changes still match.
        stable = full_title.split(" | ", 1)[0].split("|", 1)[0].strip() or full_title
        profile.capture_window_title = stable
        # Mirror into top-level when this profile happens to be active so
        # the running macro sees the same value without an extra reload.
        if profile is settings.get_profile():
            settings.capture_window_title = stable
        try:
            settings.save()
        except Exception:
            pass
        logger.success(
            f"🎯 [{profile.label}] 게임창 자동 감지: '{full_title}' "
            f"→ 저장 키 '{stable}'"
        )
        cap = _build_window_capture(hwnd, full_title)
        if cap is not None:
            return cap

    logger.info(
        f"[{profile.label}] 게임창 자동 감지 실패 "
        f"→ 모니터 {profile.capture_monitor_index} 캡처로 시작"
    )
    return MonitorCapture(monitor_index=profile.capture_monitor_index)


def _build_capture(settings: Settings) -> CaptureSource:
    """Backward-compat shim — build capture for the active client.

    Pre-multi-client callers (tests, old scripts) get the same behaviour
    as before via this thin wrapper. New code should call
    ``_build_capture_for_profile`` directly.
    """
    cap = _build_capture_for_profile(
        settings.get_profile(), settings, allow_auto_detect=True,
    )
    if cap is None:
        # Active client always falls back to MonitorCapture (the original
        # behaviour) so the macro pipeline never starts with a null
        # capture. _build_capture_for_profile only returns None when
        # auto-detect is disabled, which doesn't happen here.
        cap = MonitorCapture(monitor_index=settings.capture_monitor_index)
    return cap


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
        client_id=settings.active_client_id,
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
    # All three window-bound capture backends expose .hwnd / .label.
    if hasattr(capture, "hwnd") and hasattr(capture, "label"):
        auto_hwnd = int(getattr(capture, "hwnd", 0) or 0)
        auto_title = getattr(capture, "label", "") or ""
    if not auto_hwnd and settings.capture_window_title:
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

    # ── Standby runtime for the non-active client ──────────────────────
    # Pre-build capture + slot_manager for every non-active client so the
    # UI tab switch (Phase 3) can flip the live runtime without a window
    # re-bind round-trip. client2's capture is OPTIONAL — if the user
    # hasn't picked a window for that tab yet, ``_build_capture_for_profile``
    # returns None and we record a placeholder. Auto-detect is disabled
    # for non-active clients so we don't accidentally grab the active
    # client's game window for both tabs.
    #
    # Phase 2A leaves these in standby (no controller, no thread). Phase 2B
    # will wire per-client MacroController instances so two tabs can run
    # concurrently.
    standby_runtimes: dict[str, dict] = {}
    for cid, profile in settings.clients.items():
        if cid == settings.active_client_id:
            continue
        try:
            sb_cap = _build_capture_for_profile(
                profile, settings, allow_auto_detect=False,
            )
        except Exception:
            logger.exception(f"standby capture build failed for {cid}")
            sb_cap = None
        sb_sm = SlotManager()
        # Per-client PostMessage / AttachInput so each tab targets its
        # own HWND. Arduino is shared (single physical device, user
        # decision). Backends start untargeted — set_target() runs when
        # a game window resolves.
        sb_postmsg = PostMessageBackend()
        sb_attach = AttachInputBackend()
        sb_hwnd = 0
        sb_title = ""
        # All window-bound backends (PrintWindow / WGC / mss WindowCapture)
        # expose .hwnd / .label — duck-type rather than isinstance-list so
        # adding a new backend doesn't require touching this site.
        if sb_cap is not None and hasattr(sb_cap, "hwnd") and hasattr(sb_cap, "label"):
            sb_hwnd = int(getattr(sb_cap, "hwnd", 0) or 0)
            sb_title = getattr(sb_cap, "label", "") or ""
            if sb_hwnd:
                sb_postmsg.set_target(sb_hwnd, sb_title)
                sb_attach.set_target(sb_hwnd, sb_title)

        # Build a controller for this client if we have a capture.
        # NOT started yet — Phase 3 will swap which one is "active" when
        # the user clicks a tab, Phase 4 starts both for concurrent run.
        # The recognizer is SHARED across clients (template targets are
        # global per-game, no per-tab calibration needed). The Arduino
        # backend is shared too (single physical device).
        sb_controller = None
        if sb_cap is not None:
            sb_pick_input = sb_postmsg
            if profile.input_backend == "attachinput":
                sb_pick_input = sb_attach
            elif profile.input_backend == "arduino":
                sb_pick_input = arduino
            # Recognizer holds per-frame state (_buff_history /
            # _buff_smoothed / score-log throttles) — sharing one across
            # both clients caused cross-tab leak (one tab's buff count
            # polluting the other's smoothing). Each controller gets its
            # own. Template targets are loaded from disk → cheap and
            # identical across instances; no global cache needed.
            sb_recognizer = Recognizer()
            sb_controller = MacroController(
                settings=settings,
                capture=sb_cap,
                recognizer=sb_recognizer,
                slot_manager=sb_sm,
                input_backend=sb_pick_input,
                telegram=telegram,
                on_slot_state_changed=_emit_slot_refresh,
                client_id=cid,
            )
            sb_controller._backends = {
                "arduino": arduino,
                "postmessage": sb_postmsg,
                "attachinput": sb_attach,
            }
            sb_controller._auto_hwnd = sb_hwnd
            sb_controller._auto_title = sb_title

        standby_runtimes[cid] = {
            "profile": profile,
            "capture": sb_cap,
            "slot_manager": sb_sm,
            "postmsg": sb_postmsg,
            "attachinp": sb_attach,
            "auto_hwnd": sb_hwnd,
            "auto_title": sb_title,
            "controller": sb_controller,
        }
        logger.info(
            f"📋 [{profile.label}] 대기 런타임 준비됨 "
            f"(capture={'OK' if sb_cap else '없음'}, "
            f"controller={'OK' if sb_controller else '없음'}, "
            f"hwnd={sb_hwnd or '미설정'})"
        )
    # Hand-off slot for Phase 3 — AppWindow doesn't directly consume this
    # yet, but Phase 3's tab-switch hook reads standby_runtimes[cid] to
    # know which capture/controller to flip live when the user clicks.
    controller._standby_runtimes = standby_runtimes

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

    # ── Floating switches — ONE PER CLIENT ─────────────────────────────
    # Each client tab gets its own floater glued to its own game window.
    # Clicking a floater toggles ONLY that client's ClientProfile.enabled
    # (per-tab macro gate) without depending on which tab is currently
    # shown in the main window. The global master_switch (title bar) is
    # the outer AND gate — both must be ON for that client to fire.
    from quickcast.ui.floating_switch import FloatingSwitch
    from quickcast.ui.design.signals import bus
    floaters: dict[str, FloatingSwitch] = {}

    def _client_runtime_for(cid: str) -> dict:
        """Return the runtime dict for client `cid` (hwnd/title/etc.).

        AppWindow's ``_controllers`` cache is the source of truth — it
        stays fresh through tab swaps. The boot-time ``controller``
        variable here is a STALE reference after the first swap (Python
        closure captured it at boot, and AppWindow.controller swap
        doesn't propagate back). Reading from the cache avoids the
        floater-to-wrong-window bug.
        """
        # Try the persistent cache first.
        ctrl_map = getattr(window, "_controllers", None) or {}
        ctrl = ctrl_map.get(cid)
        if ctrl is None:
            # Cache may not be populated yet on the very first build —
            # fall through to AppWindow's currently-active controller
            # (always fresh) when ids match.
            active = getattr(window, "controller", None)
            if active is not None and getattr(active, "client_id", "") == cid:
                ctrl = active
        if ctrl is not None:
            return {
                "auto_hwnd": int(getattr(ctrl, "_auto_hwnd", 0) or 0),
                "auto_title": getattr(ctrl, "_auto_title", "") or "",
            }
        # Last resort — original standby dict (only useful pre-cache).
        return standby_runtimes.get(cid, {})

    def _make_floater_toggle_handler(cid: str):
        def _h(on: bool) -> None:
            prof = settings.clients.get(cid)
            if prof is None:
                return
            if prof.enabled == on:
                return
            # Find that client's controller so we get grace/cooldown
            # side effects (not just a flag flip). All controllers are
            # tracked in AppWindow._controllers — fresh after tab swaps.
            ctrl_map = getattr(window, "_controllers", None) or {}
            ctrl = ctrl_map.get(cid)
            if ctrl is not None:
                ctrl.set_enabled(on)
            else:
                prof.enabled = on
            # Broadcast — titlebar Master mirror picks this up if cid is
            # currently active; ClientTabs ●dot mirrors on every emit.
            try:
                bus.client_enable_changed.emit(cid, bool(on))
            except Exception:
                pass
            bus.settings_dirty.emit()
            logger.info(
                f"{'▶️' if on else '⏸️'} [{prof.label}] 플로터 토글 "
                f"→ enabled={on}"
            )
        return _h

    # Build one FloatingSwitch per client. `client_id` makes the floater
    # read its expand panel data from settings.clients[cid] directly —
    # NOT the top-level active mirror — so two floaters never share the
    # same sub-toggle states.
    for _cid, _prof in settings.clients.items():
        fl = FloatingSwitch(client_id=_cid)
        fl.attach_settings(settings)
        # Per-client toggle handler — does NOT touch master_switch.
        fl.toggled.connect(_make_floater_toggle_handler(_cid))
        # Initial visual state mirrors that client's enabled.
        fl.set_state(bool(_prof.enabled))
        floaters[_cid] = fl

    # Auto-attach each floater to its own client's HWND on boot.
    for _cid, fl in floaters.items():
        prof = settings.clients[_cid]
        if not prof.floater_enabled:
            logger.info(f"🪟 [{prof.label}] 플로터 비활성 — attach 건너뜀")
            continue
        rt = _client_runtime_for(_cid)
        h = int(rt.get("auto_hwnd", 0) or 0)
        if not h:
            logger.info(f"🪟 [{prof.label}] hwnd 없음 — attach 건너뜀")
            continue
        try:
            fl.attach_to(h)
            logger.success(
                f"🪟 [{prof.label}] 플로터 attach → hwnd 0x{h:X}"
            )
        except Exception:
            logger.exception(f"floater {_cid} auto-attach failed")

    # Title bar's floater_toggle controls visibility of the ACTIVE tab's
    # floater (show/hide only — doesn't affect macro gate). Persists onto
    # the active ClientProfile so re-launching keeps the tab's choice.
    def _on_titlebar_floater_toggled(on: bool) -> None:
        active_cid = settings.active_client_id
        fl = floaters.get(active_cid)
        prof = settings.clients.get(active_cid)
        if prof is None or fl is None:
            return
        prof.floater_enabled = on
        if settings.floater_enabled != on:
            settings.floater_enabled = on  # top-level mirror for legacy
        if on:
            rt = _client_runtime_for(active_cid)
            h = int(rt.get("auto_hwnd", 0) or 0)
            if h:
                try:
                    fl.attach_to(h)
                except Exception:
                    logger.exception("floater attach failed")
            else:
                # No game window for this tab — at least hide.
                fl.detach()
        else:
            fl.detach()
        try:
            settings.save()
        except Exception:
            pass
    window.floater_toggled.connect(_on_titlebar_floater_toggled)
    # Initial titlebar visual state.
    try:
        window.title_bar.floater_toggle.set_state(
            bool(settings.get_profile().floater_enabled), animate=False,
        )
    except Exception:
        pass

    # When a game window's HWND changes (capture-section pick / auto-
    # detect / tab swap broadcast), update the matching client's floater.
    # `game_window_found` always refers to the ACTIVE client — non-active
    # clients keep their existing floater glue until they swap to active
    # and the user re-picks a window from the capture page.
    def _on_game_window_found(hwnd: int, _title: str) -> None:
        active_cid = settings.active_client_id
        fl = floaters.get(active_cid)
        prof = settings.clients.get(active_cid)
        if fl is None or prof is None:
            return
        if prof.floater_enabled and hwnd:
            try:
                fl.attach_to(int(hwnd))
            except Exception:
                logger.exception("floater re-attach failed")
        else:
            try:
                fl.detach()
            except Exception:
                pass
        # Mirror titlebar toggle to the active client's saved state.
        try:
            window.title_bar.floater_toggle.set_state(
                bool(prof.floater_enabled), animate=False,
            )
        except Exception:
            pass
    bus.game_window_found.connect(_on_game_window_found)

    # Mirror each client's enabled state back into its floater visual
    # — covers indirect changes (e.g. settings reload).
    def _resync_floaters_from_profiles() -> None:
        for _cid, fl in floaters.items():
            prof = settings.clients.get(_cid)
            if prof is not None:
                try:
                    fl.set_state(bool(prof.enabled))
                except Exception:
                    pass
    bus.settings_dirty.connect(_resync_floaters_from_profiles)

    # Floater dim/highlight is now driven inside FloatingSwitch._track
    # based on the actual foreground game window (not the selected tab),
    # so no main.py wiring is needed for that visual.

    # Cross-mirror titlebar Master ↔ per-client floater states.
    # When the titlebar (or shortcut) flips the active client's enabled,
    # this updates the matching floater. When a floater flips a client's
    # enabled, this updates the titlebar IF that client is active.
    def _on_client_enable_changed(cid: str, on: bool) -> None:
        fl = floaters.get(cid)
        if fl is not None:
            try:
                fl.set_state(bool(on))
            except Exception:
                pass
        if cid == settings.active_client_id:
            try:
                window.title_bar.set_master(bool(on))
            except Exception:
                pass
        # ClientTabs ●dot stays in sync too.
        try:
            if window._client_tabs is not None:
                window._client_tabs.set_enabled_dot(cid, bool(on))
        except Exception:
            pass
    bus.client_enable_changed.connect(_on_client_enable_changed)

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
    # ── Phase 4: start every standby controller too so two tabs can run
    # concurrently. Each one has its own capture/recognizer/slot_manager
    # but reads from the SAME Settings instance via its client_id, so the
    # active-tab top-level mirror doesn't affect what they evaluate.
    # ClientProfile.enabled (per-tab) AND settings.master_switch (global)
    # gate the actual macro fires — capture loops stay alive regardless.
    for cid, rt in standby_runtimes.items():
        sb_ctrl = rt.get("controller")
        if sb_ctrl is None:
            continue
        try:
            sb_ctrl.start()
            logger.info(f"▶️ [{rt['profile'].label}] 컨트롤러 가동 시작")
        except Exception:
            logger.exception(f"standby controller start failed: {cid}")
    # Warn if two tabs are pointed at the same game window — the user
    # almost certainly meant to pick a separate client for the second tab.
    hwnd_to_cid: dict[int, str] = {}
    for cid, prof in settings.clients.items():
        h = 0
        if cid == settings.active_client_id:
            h = int(getattr(controller, "_auto_hwnd", 0) or 0)
        else:
            h = int(standby_runtimes.get(cid, {}).get("auto_hwnd", 0) or 0)
        if h:
            if h in hwnd_to_cid:
                logger.warning(
                    f"⚠️ 클라 '{cid}'와 '{hwnd_to_cid[h]}'가 동일 HWND 0x{h:X}를 "
                    f"잡았습니다 — 한 쪽 탭의 캡처를 다른 게임창으로 바꾸세요"
                )
            else:
                hwnd_to_cid[h] = cid

    try:
        exit_code = app.exec()
    finally:
        controller.stop()
        # Stop any standby controllers (no-op if start() was never
        # called, but defensive for Phase 4 when both run concurrently).
        for cid, rt in (getattr(controller, "_standby_runtimes", {}) or {}).items():
            sb_ctrl = rt.get("controller")
            if sb_ctrl is not None:
                try:
                    sb_ctrl.stop()
                except Exception:
                    logger.exception(f"standby controller stop failed: {cid}")
            sb_cap = rt.get("capture")
            if sb_cap is not None:
                try:
                    sb_cap.close()
                except Exception:
                    logger.exception(f"standby capture close failed: {cid}")
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
