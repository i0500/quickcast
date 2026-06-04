"""Main controller — capture thread + control thread.

Mirrors the original `captureLoop` (every ~500 ms) and `controlLoop`
(every ~100 ms) but each runs on its own thread so a slow Telegram
upload or template match never starves the other.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from quickcast.config import ClientProfile, Settings
from quickcast.core.capture import Frame, ScreenCapture
from quickcast.core.recognition import FrameAnalysis, Recognizer
from quickcast.core.state import RuntimeState
from quickcast.input_io.input_router import InputBackend, NullBackend
from quickcast.notify.telegram import TelegramNotifier
from quickcast.slots.slot_manager import FireEvent, SlotManager
from quickcast.utils.logger import logger


# Public callback signature: receives latest analysis for UI display.
AnalysisCallback = Callable[[FrameAnalysis, Frame], None]


class MacroController:
    """Owns the capture/control threads and ties the modules together."""

    def __init__(
        self,
        settings: Settings,
        capture: ScreenCapture,
        recognizer: Recognizer,
        slot_manager: SlotManager,
        input_backend: InputBackend,
        telegram: Optional[TelegramNotifier] = None,
        on_analysis: Optional[AnalysisCallback] = None,
        on_slot_state_changed: Optional[Callable[[], None]] = None,
        client_id: str = "",
    ) -> None:
        self.settings = settings
        # Which client tab this controller is bound to. Empty string ⇒
        # legacy single-client mode (falls back to active). Two controllers
        # sharing the same Settings instance read DIFFERENT ClientProfile
        # data via this id, so two tabs can run in parallel without
        # cross-firing each other's slots / capture / overlays.
        self.client_id = client_id or settings.active_client_id
        self.capture = capture
        self.recognizer = recognizer
        self.slot_manager = slot_manager
        self.input = input_backend or NullBackend()
        self.telegram = telegram
        self.on_analysis = on_analysis
        # Fired (no args) whenever a one-shot toggle (pk.use, potion.use,
        # slot.use for non-repeat) is auto-disabled by SlotManager so the
        # UI can refresh its iOS toggles to match.
        self.on_slot_state_changed = on_slot_state_changed
        self.state = RuntimeState()

        self._stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._control_thread: Optional[threading.Thread] = None

        # Most-recent frame + analysis shared between threads
        self._latest_lock = threading.Lock()
        self._latest_frame: Optional[Frame] = None
        self._latest_analysis: Optional[FrameAnalysis] = None
        # Timestamp of the last "item close" auto-click. Throttles the
        # popup-dismiss feature to settings.item_close.interval_seconds
        # so we don't spam the game window with clicks.
        self._last_item_close_at: float = 0.0
        # Per-overlay last-fire timestamp + edge-trigger latch so we
        # send ESC once per popup appearance, not every frame the
        # template still matches (the popup takes ~half a second to
        # animate out). Cleared the moment the template goes undetected.
        self._last_overlay_close_at: dict[str, float] = {}
        self._overlay_handled: set[str] = set()
        # 오버레이가 처음 감지된 시각 — sustain_seconds 동안 연속 유지될
        # 때만 close_key를 보낸다. detected=False가 되면 즉시 클리어.
        self._overlay_first_seen_at: dict[str, float] = {}
        # Town-idle trigger — timestamp of the first frame where the
        # buff badge stopped matching. Cleared when the badge returns
        # (full count restored) or recovery sequence fires. None means
        # "badge currently matches, nothing pending".
        self._town_idle_started_at: Optional[float] = None

    # ───────── client-scoped data ─────────
    @property
    def profile(self) -> ClientProfile:
        """Return the ClientProfile this controller is bound to.

        Resolved every call (instead of cached at __init__) so that a
        future tab-rename or programmatic profile swap is picked up
        without restarting the controller. Cost is one dict lookup per
        frame — negligible at our 10-30 fps cadence.
        """
        return self.settings.get_profile(self.client_id)

    @property
    def is_active_tab(self) -> bool:
        """True when this controller drives the currently-displayed tab.

        Used to gate operations that mutate the shared top-level mirror
        fields on Settings (e.g. sync_aspect rewrites settings.hp_cap)
        so a standby controller never overwrites the active tab's ROIs.
        """
        return self.client_id == self.settings.active_client_id

    # ───────── lifecycle ─────────
    def start(self) -> None:
        if self._capture_thread and self._capture_thread.is_alive():
            return
        self._stop.clear()
        self.state.capture_connected = True

        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="CaptureLoop", daemon=True
        )
        self._control_thread = threading.Thread(
            target=self._control_loop, name="ControlLoop", daemon=True
        )
        self._capture_thread.start()
        self._control_thread.start()
        logger.debug("MacroController started")

    def stop(self) -> None:
        self._stop.set()
        for t in (self._capture_thread, self._control_thread):
            if t:
                t.join(timeout=2.0)
        self._capture_thread = self._control_thread = None
        self.state.capture_connected = False
        logger.debug("MacroController stopped")

    def set_enabled(self, on: bool) -> None:
        """Toggle this client's macro gate + side effects.

        Replaces the old set_master_switch role (cooldown reset + grace
        start + recovery latch clear) but scoped to THIS controller's
        ClientProfile rather than the long-retired global master_switch.
        The titlebar Master toggle and the floater both call this on the
        active client.
        """
        prof = self.profile
        if prof is not None:
            prof.enabled = on
        if on:
            self.state.recovery_stop.clear()
            try:
                self.slot_manager.cooldown.reset()
            except Exception:
                pass
            # Clear recovery edge-trigger latches so a fresh enable
            # cycle starts with all triggers eligible again.
            self.state.recovery_handled.clear()
            self.state._last_recovery_at = 0.0
            # Reset town-idle accumulator too — re-enabling shouldn't
            # immediately fire on a stale timer from the previous run.
            self._town_idle_started_at = None
            self.state.begin_master_grace(3.0)
            tag = getattr(prof, "label", "") or self.client_id
            logger.info(f"🟢 [{tag}] 매크로 ON (3초 후 가동, 쿨타임 초기화)")
        else:
            self.state.end_master_grace()
            if self.state.recovery_in_progress:
                self.state.recovery_stop.set()
            self.state._last_recovery_at = 0.0
            tag = getattr(prof, "label", "") or self.client_id
            logger.info(f"🔴 [{tag}] 매크로 OFF")

    def set_master_switch(self, on: bool) -> None:
        """Legacy alias — kept for any callers that still poke the
        global master_switch field. Forwards to set_enabled() so the
        actual per-client gate state stays consistent."""
        self.settings.master_switch = on
        self.set_enabled(on)

    # ───────── capture thread ─────────
    def _capture_loop(self) -> None:
        # Cap at the user's chosen fps (max 30). PrintWindow naturally
        # throttles to ~30 fps anyway, and beyond that the OS-level
        # render becomes the bottleneck — pushing higher just wastes
        # CPU on low-end hardware without gaining responsiveness.
        target_period = 1.0 / max(1, min(30, int(self.settings.capture_fps)))
        next_tick = time.monotonic()
        # Track last error message so a flapping minimise/restore doesn't
        # spam the log with one identical line per tick.
        last_err_key = ""
        while not self._stop.is_set():
            try:
                frame = self.capture.grab()
                # Aspect-ratio profile sync — keeps HP/MP/PK/POTION coords
                # correct when the game window switches between 16:9 (PC
                # monitor) and 16:10 / 3:2 (typical laptop). Cheap: a
                # dict lookup + maybe one Pydantic copy per ratio flip.
                self._maybe_sync_aspect()
                analysis = self.recognizer.analyze(frame, self.settings, self.profile)
                with self._latest_lock:
                    self._latest_frame = frame
                    self._latest_analysis = analysis
                self.state.update_analysis(analysis)
                if self.on_analysis:
                    try:
                        self.on_analysis(analysis, frame)
                    except Exception as e:
                        logger.warning(f"on_analysis callback error: {e}")
                if last_err_key:
                    logger.info("✅ 캡처 정상화")
                    last_err_key = ""
            except Exception as e:
                # Lazy import to avoid module-load cycle.
                try:
                    from quickcast.core.window_print_capture import WindowMinimizedError
                except Exception:
                    WindowMinimizedError = None
                if WindowMinimizedError is not None and isinstance(e, WindowMinimizedError):
                    key = "minimized"
                    if last_err_key != key:
                        logger.info("ℹ️ 게임창 최소화 — 마지막 프레임 유지")
                        last_err_key = key
                else:
                    msg = str(e)
                    if last_err_key != msg:
                        logger.error(f"❌ 캡처 오류: {msg}")
                        last_err_key = msg
                time.sleep(0.5)

            # Steady cadence; falls back gracefully if a frame took too long
            next_tick += target_period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                self._stop.wait(sleep_for)
            else:
                next_tick = time.monotonic()

    def _maybe_sync_aspect(self) -> None:
        """Check the capture source's current aspect bucket; switch ROI
        profile when it changes.

        Reads ``capture.last_source_size`` (set inside grab()) and calls
        ``settings.sync_aspect()``. On a real change, emits both
        ``settings_dirty`` (so AppWindow's debounced save writes the
        newly-active profile to disk) and ``aspect_changed`` (so the
        preview / status bar can repaint with the new coords).

        Skipped entirely when ``settings.lock_aspect_profile`` is True
        (the default) — letterboxing in the capture layer now preserves
        source aspect, so a single ROI calibration works across all
        source aspects without auto-swapping.
        """
        if getattr(self.profile, "lock_aspect_profile", True):
            return
        # Only the active-tab controller may rewrite Settings.sync_aspect()
        # — sync_aspect mutates the top-level mirror fields which represent
        # the *active* client. A standby controller calling it would
        # silently overwrite the other tab's ROIs.
        if not self.is_active_tab:
            return
        size = getattr(self.capture, "last_source_size", None)
        if not size or size[0] <= 0 or size[1] <= 0:
            return
        try:
            from quickcast.config import classify_aspect
        except Exception:
            return
        aspect = classify_aspect(size[0], size[1])
        if aspect == self.settings.active_aspect:
            return
        try:
            changed, used_existing = self.settings.sync_aspect(aspect)
        except Exception:
            logger.exception("aspect-sync: sync_aspect failed")
            return
        if not changed:
            return
        try:
            from quickcast.ui.design.signals import bus
            bus.aspect_changed.emit(aspect, bool(used_existing))
            bus.settings_dirty.emit()
        except Exception:
            # Bus may not be importable in headless / test contexts —
            # that's fine, aspect data is already on the settings object.
            pass

    # ───────── control thread ─────────
    def _control_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_control()
            except Exception as e:
                logger.error(f"❌ 제어 루프 오류: {e}")
            self._stop.wait(0.1)  # ~100 ms cadence, matches JS

    def _tick_control(self) -> None:
        # Single gate per client — ClientProfile.enabled. Master "global"
        # was retired: the titlebar Master toggle now writes onto the
        # active tab's profile.enabled (same field as the floater). Both
        # toggles + the per-tab ●dot stay synchronised via the UI layer.
        if not getattr(self.profile, "enabled", True):
            return
        if self.state.in_grace_period():
            return
        if getattr(self.state, "recovery_in_progress", False):
            return
        # Auto-pause while the game window is minimised — its rendered
        # frame is stale so reactions to it would be wrong. Edge-log
        # the transition once in each direction.
        hwnd = int(getattr(self, "_auto_hwnd", 0) or 0)
        if hwnd:
            try:
                import ctypes
                is_min = bool(ctypes.windll.user32.IsIconic(hwnd))
            except Exception:
                is_min = False
            if is_min != self.state.window_minimized:
                was_min = self.state.window_minimized
                self.state.window_minimized = is_min
                if is_min:
                    logger.info("⏸️ 게임창 최소화 감지 — 매크로 일시정지")
                else:
                    # Restore — give the capture loop a 1-second grace
                    # to grab a FRESH (non-black) frame before we trust
                    # `_latest_analysis` again. Without this, the stale
                    # black-frame HP=0 reading would fire heal slots
                    # the moment IsIconic flips False. Also wipe the
                    # stale analysis so even if grace check is bypassed
                    # somehow, the eval has no data to act on.
                    self.state.begin_master_grace(1.0)
                    with self._latest_lock:
                        self._latest_analysis = None
                    logger.info("▶️ 게임창 복구 — 매크로 재개 (1초 안정화 대기)")
                # Either direction — return so this tick doesn't fall
                # through to slot evaluation with potentially stale data.
                return
            if is_min:
                return

        with self._latest_lock:
            analysis = self._latest_analysis
            frame = self._latest_frame
        if analysis is None:
            return

        # Snapshot use-state so we can detect SlotManager auto-disabling
        # any one-shot toggle this tick (potion fires, non-repeat slots
        # / pk firing) and notify the UI to refresh its toggles.
        # Reads from this controller's ClientProfile so two tabs don't
        # observe each other's slot state flips.
        p = self.profile
        prev = (p.pk.use, p.potion.use,
                {sid: sl.use for sid, sl in p.slots.items()})

        # Fire any matching slot events first.
        events = self.slot_manager.evaluate(self.settings, analysis, p)
        fired_ids: list[str] = []
        for event in events:
            self._fire(event, frame)
            fired_ids.append(event.slot_id)

        if events:
            # Compare snapshot to detect any toggle that flipped True→False.
            cur_slots = {sid: sl.use for sid, sl in p.slots.items()}
            if (prev[0] != p.pk.use or prev[1] != p.potion.use
                    or any(prev[2].get(k) != cur_slots.get(k) for k in cur_slots)):
                if self.on_slot_state_changed:
                    try:
                        self.on_slot_state_changed()
                    except Exception:
                        logger.exception("on_slot_state_changed callback failed")

        # Recovery sequence trigger detection — uses the same `analysis`
        # and the list of slots that just fired this tick.
        self._maybe_trigger_recovery(analysis, fired_ids)

        # Overlay popup auto-close (pet whistle paw / item acquired
        # chest / …). Fires ESC once per popup appearance with a
        # per-overlay cooldown so we don't spam keys.
        self._maybe_close_overlays(analysis)

        # Auto-click the "item acquired" popup dismiss point. Runs
        # only while master is on (we're already past the early-out)
        # and respects the user's interval.
        self._maybe_item_close_click()

    def _maybe_close_overlays(self, analysis: FrameAnalysis) -> None:
        """Send ESC (or configured key) when any overlay is detected.

        Edge-triggered per overlay: once a popup is detected we fire the
        close key, latch ``_overlay_handled`` so we don't repeat-fire
        every frame the template still matches, and clear the latch the
        moment the template stops matching (popup dismissed). A
        per-overlay cooldown is a secondary safeguard for popups that
        re-appear quickly.
        """
        cfg = getattr(self.profile, "overlay_closes", None) or {}
        matches = getattr(analysis, "overlay_matches", None) or {}
        if not cfg or not matches:
            return
        now = time.monotonic()
        for ov_id, ov in cfg.items():
            if not getattr(ov, "enabled", False):
                continue
            m = matches.get(ov_id)
            if m is None:
                continue
            if not m.detected:
                # Popup gone — release the latch so the next appearance fires.
                self._overlay_handled.discard(ov_id)
                self._overlay_first_seen_at.pop(ov_id, None)
                continue
            if ov_id in self._overlay_handled:
                continue
            # 3초 유지(또는 ov.sustain_seconds) — 첫 감지 시각을 기록하고
            # 연속으로 sustain_seconds 동안 detected 가 유지될 때만 통과.
            sustain = max(0.0, float(getattr(ov, "sustain_seconds", 0.0) or 0.0))
            first_seen = self._overlay_first_seen_at.get(ov_id)
            if first_seen is None:
                self._overlay_first_seen_at[ov_id] = now
                first_seen = now
            if sustain > 0.0 and (now - first_seen) < sustain:
                continue
            cooldown = max(0.1, float(ov.cooldown_seconds))
            last = self._last_overlay_close_at.get(ov_id, 0.0)
            if (now - last) < cooldown:
                continue
            self._last_overlay_close_at[ov_id] = now
            self._overlay_handled.add(ov_id)
            key = (ov.close_key or "esc").strip().lower() or "esc"
            held = now - first_seen
            logger.info(
                f"🚪 오버레이 감지 {held:.1f}s 유지 → {ov_id} 닫기 ('{key}' 사용)  "
                f"score={int(m.score):,}"
            )
            try:
                self.input.send_key(key)
            except Exception:
                logger.exception(f"overlay-close[{ov_id}]: send_key failed")

    def _maybe_item_close_click(self) -> None:
        ic = getattr(self.profile, "item_close", None)
        if ic is None or not getattr(ic, "enabled", False):
            return
        # Coordinates of 0×0 means "not set yet" — don't click random
        # corner of the screen on a half-configured install.
        if ic.x <= 0 and ic.y <= 0:
            return
        hwnd = int(getattr(self, "_auto_hwnd", 0) or 0)
        if not hwnd:
            return
        interval = max(0.5, float(ic.interval_seconds))
        now = time.monotonic()
        if (now - self._last_item_close_at) < interval:
            return
        self._last_item_close_at = now
        self.fire_item_close_now()

    def fire_item_close_now(self) -> None:
        """Click the item-close coord exactly once, regardless of timer.

        Used by:
          - the interval-driven path above (after it confirms enabled
            + interval elapsed),
          - the "테스트 클릭" button in the capture section, so the
            user can verify the coordinate without waiting 5 minutes.
        """
        ic = getattr(self.profile, "item_close", None)
        if ic is None:
            return
        hwnd = int(getattr(self, "_auto_hwnd", 0) or 0)
        if not hwnd:
            logger.warning("⚠️ 아이템닫기 — 게임창 미연결 상태")
            return
        frame_size = None
        with self._latest_lock:
            if self._latest_frame is not None:
                fh, fw = self._latest_frame.image.shape[:2]
                frame_size = (int(fw), int(fh))
        logger.info(
            f"🖱️ 아이템닫기 클릭 → ({ic.x},{ic.y}) "
            f"frame={frame_size} hwnd=0x{hwnd:X}"
        )
        try:
            from quickcast.input_io.win32_input import click_at
            # method="attach" — AttachThreadInput → PostMessage so the
            # game treats our click as if it came from its own input
            # queue. Plain "postmessage" gets silently filtered by
            # some Lineage W builds for tiny UI buttons (the item
            # acquired popup's close icon being one of them).
            click_at(hwnd, int(ic.x), int(ic.y),
                      frame_size=frame_size, method="attach")
        except Exception:
            logger.exception("item-close: click_at failed")

    def _maybe_trigger_recovery(self, analysis: FrameAnalysis,
                                  fired_slot_ids: Optional[list] = None) -> None:
        rec = getattr(self.profile, "recovery", None)
        if rec is None or not rec.enabled or not rec.steps:
            return
        # Edge-triggered latch — the recovery_handled set tracks which
        # triggers already fired recovery and are still asserted. We
        # clear an entry the moment its source condition goes False,
        # forcing the user (or the game) to re-arm the trigger before
        # we run the sequence again.
        handled = self.state.recovery_handled
        # ── Latch release: based on the LEVEL state of the source
        # condition (potion_empty / pk_detected / hp<=1). When the
        # game's empty-potion icon disappears, "potion" releases and
        # can re-fire next time it shows up.
        level_active: set = set()
        if analysis.potion_empty:    level_active.add("potion")
        if analysis.pk_detected:     level_active.add("pk")
        if analysis.hp <= 1:         level_active.add("hp_zero")
        for h in list(handled):
            if h.startswith("slot:"):
                continue
            if h not in level_active:
                handled.discard(h)

        # ── Fire decision: PK / Potion recovery only fires when the
        # corresponding slot actually fired this tick. So if the user's
        # potion slot is OFF / out of HP range / didn't trigger, the
        # recovery doesn't fire either — they stay perfectly in lockstep.
        fired = set(fired_slot_ids or [])
        fire_set: set = set()
        if rec.trigger_potion and "potion" in fired:
            fire_set.add("potion")
        if rec.trigger_pk and "pk" in fired:
            fire_set.add("pk")
        if rec.trigger_hp_zero and analysis.hp <= 1:
            fire_set.add("hp_zero")

        # ── Town-idle: OCR-read buff count fell below threshold and stayed
        # there for ``town_idle_seconds``. Only meaningful when the OCR
        # scanner actually produced a confident integer this tick — a None
        # ``buff_count`` (untrained / occluded badge) leaves the timer
        # cleared so we don't false-fire on missing data.
        # Per-frame OCR confidence below ``town_idle_min_confidence`` is
        # treated as a false read so noisy single-frame misreads don't
        # advance the town-idle timer. User-adjustable in the capture tab.
        town_idle_active = False
        threshold_n = int(getattr(rec, "town_idle_threshold", 75))
        min_conf = float(getattr(rec, "town_idle_min_confidence", 0.60) or 0.0)
        buff_conf = float(getattr(analysis, "buff_confidence", 0.0) or 0.0)
        if rec.trigger_town_idle and getattr(analysis, "buff_scanned", False) \
                and analysis.buff_count is not None \
                and buff_conf >= min_conf:
            now_t = time.monotonic()
            below = analysis.buff_count < threshold_n
            if below:
                if self._town_idle_started_at is None:
                    self._town_idle_started_at = now_t
                    logger.info(
                        f"⏳ 마을 대기 감지 시작 — 버프 카운트={analysis.buff_count}"
                        f" < {threshold_n}"
                    )
                elapsed = now_t - self._town_idle_started_at
                threshold_s = max(1.0, float(rec.town_idle_seconds))
                if elapsed >= threshold_s:
                    town_idle_active = True
                    fire_set.add("town_idle")
                    level_active.add("town_idle")
            else:
                if self._town_idle_started_at is not None:
                    held = now_t - self._town_idle_started_at
                    logger.debug(
                        f"⏳ 마을 대기 타이머 리셋 (유지 {held:.1f}s 후 버프 복원"
                        f"={analysis.buff_count})"
                    )
                self._town_idle_started_at = None
        else:
            # Trigger disabled or OCR couldn't read — drop the in-flight
            # timer so re-enabling won't fire immediately on stale state.
            self._town_idle_started_at = None
        # Latch release symmetric with potion/pk/hp_zero: town_idle stays
        # latched until the buff count returns to threshold.
        if "town_idle" not in level_active and "town_idle" in handled:
            handled.discard("town_idle")

        # No global cooldown — edge-trigger latch already prevents
        # the recovery loop. Each trigger fires once until its
        # condition releases (or potion.use is re-toggled).

        # Decide what to fire — first un-handled active trigger wins.
        triggered_by = ""
        for k in ("potion", "pk", "hp_zero", "town_idle"):
            if k in fire_set and k not in handled:
                triggered_by = k
                break
        slot_triggered = False
        if not triggered_by and fired_slot_ids and getattr(rec, "trigger_slot_ids", None):
            for sid in fired_slot_ids:
                if sid in rec.trigger_slot_ids:
                    triggered_by = f"slot:{sid}"
                    slot_triggered = True
                    break
        if not triggered_by:
            return
        # Only level-source triggers (potion/pk/hp_zero) get latched.
        # Slot triggers are inherently edge events so they're free to
        # re-fire on each fresh slot fire without latching.
        if not slot_triggered:
            handled.add(triggered_by)
        # Need a known game HWND for the click coords to mean anything.
        hwnd = int(getattr(self, "_auto_hwnd", 0) or 0)
        if not hwnd:
            logger.warning("⚠️ 사냥터 복귀 트리거됐지만 게임창 미선택")
            return

        self.state._last_recovery_at = time.monotonic()
        self.state.recovery_in_progress = True
        frame_size = None
        with self._latest_lock:
            if self._latest_frame is not None:
                h, w = self._latest_frame.image.shape[:2]
                frame_size = (int(w), int(h))
        logger.info(
            f"🏃 사냥터 복귀 시작 ({triggered_by})  "
            f"{rec.start_delay_seconds}초 대기 → {len(rec.steps)}단계 실행"
        )
        threading.Thread(
            target=self._run_recovery_thread,
            args=(hwnd, rec.start_delay_seconds, list(rec.steps), frame_size),
            name="RecoveryRunner", daemon=True,
        ).start()

    def _run_recovery_thread(self, hwnd: int, start_delay_s: int, steps: list,
                              frame_size: Optional[tuple] = None) -> None:
        from quickcast.input_io.win32_input import click_at, attach_input_scope

        def _aborted() -> bool:
            return (self._stop.is_set()
                    or self.state.recovery_stop.is_set()
                    or not getattr(self.profile, "enabled", True))

        def _wait(seconds: float) -> bool:
            """Wait `seconds`, returning True if we were aborted mid-sleep.
            Polls master/abort every 100ms so master OFF stops promptly
            even during the long start_delay_seconds window."""
            deadline = time.monotonic() + max(0.0, seconds)
            while time.monotonic() < deadline:
                if _aborted():
                    return True
                self._stop.wait(min(0.1, deadline - time.monotonic()))
            return _aborted()

        try:
            if _wait(float(start_delay_s)):
                logger.info("🏃 사냥터 복귀 중단됨 (시작 대기 중)")
                return
            with attach_input_scope(hwnd):
                for i, step in enumerate(steps, 1):
                    if _aborted():
                        logger.info(f"🏃 사냥터 복귀 중단됨 (단계 {i} 직전)")
                        return
                    if step.key:
                        # Key-press step — route through the configured
                        # input backend (Arduino / PostMessage / etc).
                        logger.info(
                            f"🏃 단계 {i}/{len(steps)} {step.label} → 키 '{step.key}'"
                        )
                        try:
                            self.input.send_key(step.key)
                        except Exception:
                            logger.exception("recovery: key send failed")
                    else:
                        logger.info(
                            f"🏃 단계 {i}/{len(steps)} {step.label} → 클릭 ({step.x},{step.y})"
                        )
                        click_at(hwnd, int(step.x), int(step.y),
                                  frame_size=frame_size, method="postmessage")
                    if _wait(step.delay_after_ms / 1000.0):
                        logger.info(f"🏃 사냥터 복귀 중단됨 (단계 {i} 후)")
                        return
            logger.success("✅ 사냥터 복귀 완료")
        except Exception:
            logger.exception("❌ 사냥터 복귀 시퀀스 오류")
        finally:
            # 복귀 시퀀스가 끝나면 누적된 모든 인식/대기 타이머를 리셋.
            # 그렇지 않으면: start_delay(2~5분) 동안 마을에 있어서 buff
            # 카운터가 임계 이하로 내려가 _town_idle_started_at 이 잡혔는데,
            # 복귀 시퀀스 동안 _tick_control 이 early return 으로 빠져
            # 이 값이 "갱신은 안 되지만 살아있는 상태"로 동결된다. 시퀀스가
            # 끝나는 순간 elapsed = (시퀀스 소요시간 + 잠재된 동결시간) 이
            # 즉시 town_idle_seconds 를 넘어서 같은 복귀 시퀀스를 중복
            # 발사하는 버그가 있었다. sustain/overlay 타이머도 같은 이유로
            # 클리어 — "복귀 직전 1.8초 유지"가 복귀 후에도 이어지는 건
            # 의미가 없다.
            self._town_idle_started_at = None
            self._overlay_first_seen_at.clear()
            self._overlay_handled.clear()
            try:
                self.slot_manager.reset_sustain()
            except Exception:
                pass
            self.state.recovery_in_progress = False

    def _fire(self, event: FireEvent, frame: Optional[Frame]) -> None:
        backend = self.input
        backend_name = type(backend).__name__
        backend_ok = bool(getattr(backend, "connected", True))
        if not backend_ok:
            # Silent fallback to a connected sibling — keep this clean.
            backends = getattr(self, "_backends", {}) or {}
            fallback = None
            for name in ("postmessage", "attachinput", "arduino"):
                cand = backends.get(name)
                if cand is None or cand is backend:
                    continue
                if bool(getattr(cand, "connected", False)):
                    fallback = (name, cand)
                    break
            if fallback is not None:
                logger.warning(
                    f"⚠️ {backend_name} 미연결 — {fallback[0]}으로 폴백"
                )
                backend = fallback[1]
                backend_name = type(backend).__name__
            else:
                logger.warning(
                    f"⚠️ {event.label} 사용 실패: 사용 가능한 백엔드 없음"
                )

        # Hardware first; notification is best-effort and async
        try:
            if event.count <= 1:
                backend.send_key(event.key)
            else:
                self._send_burst_via(backend, event.key, event.count, event.delay)
        except Exception:
            logger.exception(f"_fire: send via {backend_name} failed")

        if event.tele_use and self.telegram and self.telegram.connected:
            # Multi-client: prefix the tab label so two clients sharing
            # one Telegram bot don't blur together in chat history.
            # Single-client deployments see just the event label (empty
            # prefix when the tab label is the default "클라1" / blank).
            tab_label = (getattr(self.profile, "label", "") or "").strip()
            prefix = f"[{tab_label}] " if tab_label else ""
            if event.snapshot and frame is not None:
                self.telegram.send_photo(
                    frame.image, caption=f"{prefix}{event.label} 실행",
                )
            else:
                self.telegram.send_text(f"{prefix}{event.label} 기능을 사용했습니다")

    def _send_burst(self, key: str, count: int, delay: float) -> None:
        self._send_burst_via(self.input, key, count, delay)

    def _send_burst_via(self, backend, key: str, count: int, delay: float) -> None:
        # Use the backend's burst API if available (Arduino), else loop
        burst = getattr(backend, "send_sequence", None)
        if callable(burst):
            burst(key, count, delay)
        else:
            for i in range(count):
                backend.send_key(key)
                if i < count - 1 and delay > 0:
                    time.sleep(delay)


__all__ = ["MacroController", "AnalysisCallback"]
