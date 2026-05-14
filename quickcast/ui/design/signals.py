"""Cross-cutting signal bus for design-system events.

Custom-paint widgets (RangeSlider, IOSToggle, InteractivePreview)
cannot be styled by QSS, so they subscribe to `theme_changed` and
repaint themselves whenever the active TokenSet changes.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _SignalBus(QObject):
    theme_changed = Signal()    # active TokenSet was swapped
    # Live recognition snapshot from the dashboard preview's recognizer.
    # (hp%, mp%, pk_score, potion_score, pk_detected, potion_empty, fps)
    live_scores = Signal(int, int, float, float, bool, bool, float)
    # Any control wrote to mock_settings — AppWindow saves with debounce.
    settings_dirty = Signal()
    # Slot list / alarm list mutated (add/delete) — sidebars rebuild.
    slot_list_changed = Signal()
    alarm_list_changed = Signal()
    # User picked a different game window via Capture section. AppWindow
    # listens and hot-swaps the controller's capture source.
    capture_target_changed = Signal()
    # Capture source aspect-ratio bucket changed (e.g. "16:9" → "16:10"
    # when the game window goes fullscreen on a 16:10 laptop). Controller
    # emits AFTER swapping the active ROI profile so UI listeners just
    # need to redraw — sections subscribed to settings_dirty see the
    # updated coords automatically.
    aspect_changed = Signal(str, bool)    # (new_aspect, used_existing_profile)
    # Auto window-detection found / re-found a game window HWND while
    # the previous one was missing. Floater / Win32 input backends
    # listen so they can re-attach to the new hwnd without the user
    # having to re-pick from the Capture section.
    game_window_found = Signal(int, str)    # (hwnd, title)
    # User saved a new digit-template set via the OCR calibration
    # dialog. Recognizer subscribes and reloads its in-memory templates
    # so the next frame's OCR sees the freshly learned glyphs without
    # an app restart.
    digit_templates_changed = Signal()
    # Item-close click coord picker: capture section asks the preview
    # to enter pick mode, preview emits the chosen point on click.
    item_close_pick_request = Signal()
    item_close_pick_done = Signal(int, int)    # (frame_x, frame_y)
    # "테스트 클릭" — capture section asks the controller to fire one
    # item-close click immediately so the user can verify the coord
    # without waiting for the interval timer.
    item_close_test_request = Signal()
    # Live captured frame (numpy BGRA), latest analysis, and fps.
    # Emitted from AppWindow so any widget (dashboard preview, fullscreen
    # window, …) can mirror the real game image without each rebuilding
    # its own recognizer/timer.
    live_frame = Signal(object, object, float)
    # Connection requests from Settings UI → AppWindow
    arduino_connect_request = Signal()
    telegram_connect_request = Signal()
    # Connection state broadcasts ← AppWindow → UI dots
    arduino_state_changed = Signal(bool, str)    # (connected, label)
    telegram_state_changed = Signal(bool, str)
    capture_state_changed = Signal(bool, str)
    # Test key send via a specific input backend ("arduino" / "postmessage" /
    # "attachinput"). AppWindow picks up, fires the key once, and toasts.
    test_input_request = Signal(str, str)    # (backend_id, key_name)
    # User changed input backend — AppWindow swaps controller.input.
    input_backend_changed = Signal(str)
    # Recovery step picker — UI requests "next click on preview captures
    # x/y for step N", dashboard ack via _done with (idx, x, y).
    recovery_pick_request = Signal(int)
    recovery_pick_done = Signal(int, int, int)
    # Master grace-period countdown (seconds remaining; 0 when done).
    master_grace_changed = Signal(float)
    # Request switching the active section by id (e.g. "dashboard").
    activate_section = Signal(str)
    # Fired by AppWindow when the controller auto-disabled a one-shot
    # toggle (potion.use=False after fire, pk.use=False on non-repeat,
    # slot.use=False on non-repeat). Sections re-read settings and
    # refresh their iOS toggles so the change is visible to the user.
    slot_state_refresh = Signal()
    # User clicked a step's "테스트" button — AppWindow performs a single
    # click_at against the saved (x,y) so the user can verify each step
    # individually without running the whole recovery sequence.
    recovery_step_test = Signal(int)
    # New log line from loguru → dashboard log card row.
    # (level, message). Emitted from add_ui_sink callback; cross-thread
    # safe via Qt's queued connection on receivers in the GUI thread.
    log_entry = Signal(str, str)
    # Alarm fired — main.py emits this from the AlarmScheduler callback
    # so AppWindow shows toast + popup on the GUI thread without going
    # through QMetaObject.invokeMethod (which fails to marshal Pydantic
    # Alarm objects via Q_ARG(object, ...) on PySide6 + queued).
    # Args: label, hour, minute, days_csv (or empty), mode, repeat_min
    alarm_fired = Signal(str, int, int, str, str, int)
    # Alarm repeat lifecycle — emitted by AppWindow when a repeating
    # alarm starts firing in cycle (active=True) and when it auto-
    # stops or the user manually halts it (active=False). Dashboard
    # sidebar rows subscribe to show a "정지" affordance.
    alarm_repeat_active = Signal(str, bool)   # (label, active)
    # User clicked the "정지" button next to a repeating alarm row —
    # AppWindow handles by killing that label's timer.
    alarm_stop_request = Signal(str)          # label
    # Overlay-close detection results — emitted per frame from the
    # capture loop with the latest score + detected flag per overlay
    # id (pet_whistle, item_acquired, …). Capture section's overlay
    # card reads it to update "점수: N" labels.
    overlay_scores = Signal(dict)             # {ov_id: {"score": int, "detected": bool}}
    # User clicked the per-overlay "테스트 ESC" button. AppWindow
    # routes a single close_key press through the active input backend
    # so the user can verify the wire without a real popup.
    overlay_close_test = Signal(str, str)     # (ov_id, close_key)


bus = _SignalBus()

__all__ = ["bus"]
