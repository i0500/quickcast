"""Floating master switch — small iOS toggle + drag handle.

Two-zone layout:

  ┌─────────────────────────────┐
  │  ON   [▣ ]            [▼]  │  ← top bar: master toggle + expand
  ├─────────────────────────────┤
  │  PK             [▣]         │  ← expand panel (hidden by default)
  │  물약           [▣]         │
  │  슬롯1          [▣]         │
  │  …                          │
  └─────────────────────────────┘

The expand panel mirrors the live slot/PK/potion state from
mock_settings so the user can flip individual triggers from the
overlay without alt-tabbing. Bus signals (settings_dirty +
slot_state_refresh) flow both directions: any external change
rebuilds the panel; any panel toggle re-emits settings_dirty so
the main UI's iOS toggles stay in sync.

Auto-expand: when the macro itself auto-disables a one-shot
toggle (potion fires, non-repeat slot completes, …) the panel
opens automatically so the user sees what just got turned off.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QMouseEvent, QPainter,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget,
)

from quickcast.ui.design.icons import Icon
from quickcast.ui.ios_toggle import IOSToggle
from quickcast.utils.window_finder import (
    get_client_rect_screen, get_window_rect, is_window_alive,
)

# Top-bar sizing — kept identical to the previous "small pill" floater
# so existing user anchors stay sensible after the expand-panel update.
TOGGLE_W = 36
TOGGLE_H = 20
PAD_X = 8
PAD_Y = 6
LABEL_MIN_W = 32
EXPAND_BTN_PX = 16

# Sub-toggle row sizing — much tighter than the top bar so the panel
# stays narrow and the rows feel like compact sub-controls.
ROW_TOGGLE_W = 22
ROW_TOGGLE_H = 12
ROW_LABEL_PT = 7
ROW_HEIGHT = 14

TRACK_INTERVAL_MS = 200
DRAG_THRESHOLD_PX = 3


class _DragHandle(QLabel):
    """The OFF/ON text. Click-drag to move the parent floater."""

    drag_start = Signal(QPoint)
    drag_move = Signal(QPoint)
    drag_end = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._press_global: Optional[QPoint] = None
        self._is_dragging = False
        self.setCursor(Qt.SizeAllCursor)
        self.setMinimumWidth(LABEL_MIN_W)
        self.setAlignment(Qt.AlignCenter)
        f = QFont(); f.setBold(True); f.setPointSize(9); self.setFont(f)
        self.setText("OFF")
        self.setStyleSheet("color:#ff5252; padding:0 4px;")

    def set_state(self, on: bool) -> None:
        self.setText("ON" if on else "OFF")
        self.setStyleSheet(
            f"color:{'#34c759' if on else '#ff5252'}; padding:0 4px;"
        )

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._press_global = e.globalPosition().toPoint()
            self._is_dragging = False
            self.drag_start.emit(self._press_global)
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._press_global is None:
            return
        cur = e.globalPosition().toPoint()
        if not self._is_dragging:
            d = cur - self._press_global
            if abs(d.x()) + abs(d.y()) >= DRAG_THRESHOLD_PX:
                self._is_dragging = True
        if self._is_dragging:
            self.drag_move.emit(cur)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._press_global = None
            self._is_dragging = False
            self.drag_end.emit()
            e.accept()


class FloatingSwitch(QWidget):
    """Master overlay — drag/handle + master toggle + optional expand panel."""

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None, client_id: str = "") -> None:
        super().__init__(parent)
        # WindowStaysOnTopHint — 가장 robust한 "게임창 위 상시 표시"
        # 방법. owner-window 패턴(GWLP_HWNDPARENT) 시도했으나 Qt가
        # show/hide/mouse-event 시 owner를 자체적으로 재설정해서 매번
        # 우리 SetWindowLongPtr 덮어쓰고 → 플로터가 사용자 클릭마다
        # 게임 뒤로 갔다 왔다 함. Topmost가 PySide6에서 유일하게
        # 안정적인 anchor. 단점은 다른 fullscreen 창 위로도 올라오는
        # 거지만 owner 패턴 깨짐보다 사용자 영향 적음.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Multi-client: which tab this floater belongs to. Empty = legacy
        # single-client (reads from the top-level mock_settings mirror).
        # When set, _client_profile() returns settings.clients[client_id]
        # so the expand panel + write-backs touch ONLY that tab's data —
        # no cross-tab leak on active-tab swaps.
        self._client_id: str = client_id or ""

        self._target_hwnd: Optional[int] = None
        # User-pinned floater position, expressed as a fraction of the
        # target window's client rect so a window resize keeps the
        # floater in the same relative spot (e.g. always 90 % across
        # from the left edge instead of 8 pixels from the right —
        # which drifts the moment the user resizes the game window).
        #
        # ``(ratio_x, ratio_y)`` = (top-left x / width, top-left y / height).
        # None ⇒ default top-right anchor (8 px in from the right edge).
        self._user_offset: Optional[tuple[float, float]] = None
        self._drag_origin: Optional[QPoint] = None
        self._dragging: bool = False

        # ── Top bar (master toggle + expand button) ──
        self.handle = _DragHandle(self)
        self.handle.drag_start.connect(self._on_drag_start)
        self.handle.drag_move.connect(self._on_drag_move)
        self.handle.drag_end.connect(self._on_drag_end)

        self.toggle = IOSToggle(width=TOGGLE_W, height=TOGGLE_H, parent=self)
        self.toggle.toggled.connect(self._on_toggle)

        self._expand_btn = QToolButton(self)
        self._expand_btn.setFixedSize(EXPAND_BTN_PX + 6, EXPAND_BTN_PX + 6)
        self._expand_btn.setIconSize(QSize(EXPAND_BTN_PX, EXPAND_BTN_PX))
        self._expand_btn.setIcon(Icon.get("chevron-down", EXPAND_BTN_PX, "#cfd6e2"))
        self._expand_btn.setCursor(Qt.PointingHandCursor)
        self._expand_btn.setStyleSheet(
            "QToolButton { background:transparent; border:none; }"
            "QToolButton:hover { background:rgba(255,255,255,0.10);"
            " border-radius:3px; }"
        )
        self._expand_btn.clicked.connect(self._toggle_panel)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        top_row.addWidget(self.handle)
        top_row.addWidget(self.toggle)
        top_row.addWidget(self._expand_btn)
        top_widget = QWidget(self)
        top_widget.setLayout(top_row)
        self._top_widget = top_widget

        # ── Expand panel (rebuilt on demand) ──
        self._panel = QWidget(self)
        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(0, 0, 0, 0)
        self._panel_layout.setSpacing(0)
        self._panel.setVisible(False)
        # Thin divider line above the panel so the visual boundary is
        # clear without paying horizontal padding.
        self._divider = QFrame(self)
        self._divider.setFrameShape(QFrame.HLine)
        self._divider.setStyleSheet("color:rgba(255,255,255,0.10);"
                                       " background:rgba(255,255,255,0.10);"
                                       " border:none; max-height:1px;")
        self._divider.setVisible(False)

        # Master VBox — tight margins so the floater stays small.
        v = QVBoxLayout(self)
        v.setContentsMargins(PAD_X, PAD_Y, PAD_X, PAD_Y)
        v.setSpacing(0)
        v.addWidget(self._top_widget)
        v.addWidget(self._divider)
        v.addWidget(self._panel)
        self.setLayout(v)
        # Snap initial size; the top bar drives the floater's width.
        self.adjustSize()
        self._top_width: int = self.width()

        self._tracker = QTimer(self)
        self._tracker.setInterval(TRACK_INTERVAL_MS)
        self._tracker.timeout.connect(self._track)

        # Settings + bus wiring is injected via attach_settings() below
        # — only main.py knows the production mock_settings instance.
        self._settings = None
        # Cache of {key: (use, label)} from the last rebuild so we can
        # detect auto-disabled rows and auto-expand the panel.
        self._prev_use: dict[str, bool] = {}
        self._suppress_emit = False    # guard recursive rebuilds

        self.hide()

    # ───────── public API ─────────
    def attach_settings(self, settings) -> None:
        """Wire the floater to the live Settings instance.

        Must be called once after construction. Connects bus signals
        for two-way sync and primes the initial use-state cache so
        auto-expand can detect a True→False transition next tick.
        """
        self._settings = settings
        try:
            from quickcast.ui.design.signals import bus
            bus.slot_state_refresh.connect(self._on_external_refresh)
            bus.settings_dirty.connect(self._on_external_refresh)
        except Exception:
            pass
        # Snapshot current use-state so the first auto-expand check
        # has something to compare to.
        self._prev_use = self._collect_use_state()

    def attach_to(self, hwnd: int) -> None:
        if not hwnd or not is_window_alive(hwnd):
            self.detach()
            return
        # Preserve the user's dragged position when re-attaching to the
        # SAME window — game_window_found fires repeatedly (auto window
        # detection, capture hot-swap, tab broadcasts) and each call used
        # to reset _user_offset to None, snapping the floater back to the
        # default top-right corner. Only reset when binding a genuinely
        # different hwnd (then restore the per-client saved position).
        same_target = (self._target_hwnd == int(hwnd))
        self._target_hwnd = int(hwnd)
        if not same_target:
            self._user_offset = self._load_saved_offset()
        self._tracker.start()
        self._track()
        self.show()

    def _load_saved_offset(self):
        """Read this client's saved floater position (ratio) from its
        ClientProfile, or None if never dragged (→ default anchor)."""
        prof = self._client_profile()
        if prof is None:
            return None
        try:
            rx = getattr(prof, "floater_pos_x", -1.0)
            ry = getattr(prof, "floater_pos_y", -1.0)
            if rx is not None and ry is not None and rx >= 0.0 and ry >= 0.0:
                return (float(rx), float(ry))
        except Exception:
            pass
        return None

    def detach(self) -> None:
        self._target_hwnd = None
        self._tracker.stop()
        self.hide()

    def set_state(self, on: bool) -> None:
        self.toggle.set_state(on)
        self.handle.set_state(on)

    # Opacity levels for the foreground-based highlight.
    _OPACITY_FOREGROUND = 1.0     # 이 게임창이 현재 포그라운드일 때
    _OPACITY_DIMMED = 0.40        # 다른 창이 포그라운드일 때 (둘 다 흐림)

    def _apply_foreground_opacity(self) -> None:
        """Set opacity based on whether THIS floater's game window is the
        current foreground window. Driven from _track (200 ms poll).

        탭 선택과 무관 — 실제로 사용자가 그 게임창을 보고 있을 때만
        진해지고, 다른 창(다른 게임/브라우저/QuickCast 본체)으로 가면
        둘 다 흐려진다.
        """
        if self._target_hwnd is None:
            return
        try:
            import ctypes
            fg = int(ctypes.windll.user32.GetForegroundWindow())
        except Exception:
            fg = 0
        is_fg = (fg == int(self._target_hwnd))
        target = self._OPACITY_FOREGROUND if is_fg else self._OPACITY_DIMMED
        # windowOpacity is a float; compare with tolerance to avoid
        # redundant native calls every tick.
        if abs(self.windowOpacity() - target) > 0.01:
            self.setWindowOpacity(target)
            # setWindowOpacity toggles the WS_EX_LAYERED window style on
            # Windows, which can drop the window out of the always-on-top
            # band (WindowStaysOnTopHint) — the floater then sinks behind
            # the game on the next focus change. Re-assert HWND_TOPMOST
            # right after, with NOACTIVATE so we don't steal focus. Only
            # runs when opacity actually changes (foreground flip), so no
            # per-frame flicker.
            try:
                import ctypes
                from ctypes import wintypes, c_int, c_uint
                user32 = ctypes.windll.user32
                user32.SetWindowPos.argtypes = [
                    wintypes.HWND, wintypes.HWND,
                    c_int, c_int, c_int, c_int, c_uint,
                ]
                HWND_TOPMOST = wintypes.HWND(-1)
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                user32.SetWindowPos(
                    wintypes.HWND(int(self.winId())), HWND_TOPMOST,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
            except Exception:
                pass

    def set_theme(self, _theme_id: str) -> None:
        pass

    def _on_toggle(self, on: bool) -> None:
        self.handle.set_state(on)
        self.toggled.emit(on)

    # ───────── expand panel ─────────
    def _toggle_panel(self) -> None:
        self.set_panel_open(not self._panel.isVisible())

    def set_panel_open(self, opened: bool) -> None:
        # Remember the floater's current top-left so the panel toggle
        # only changes the height. The default anchor calculation in
        # _track() includes self.height(), which would otherwise yank
        # the whole floater downward by the panel's height the moment
        # we expand. We re-pin _user_offset to the current x/y so the
        # tracker treats this position as the new anchor.
        cur_x, cur_y = self.x(), self.y()

        if opened:
            self._rebuild_panel()
        self._panel.setVisible(opened)
        self._divider.setVisible(opened)
        self._expand_btn.setIcon(Icon.get(
            "chevron-up" if opened else "chevron-down",
            EXPAND_BTN_PX, "#cfd6e2",
        ))
        # Lock the floater width to the top bar so the panel never
        # inflates the row above it.
        self.adjustSize()
        if self._top_width > 0:
            self.setFixedWidth(self._top_width)
        # Restore the original position — height changed, top-left
        # didn't, so the floater visually grows downward only.
        self.move(cur_x, cur_y)
        # Refresh _user_offset against the new (locked) position so
        # the periodic _track() call doesn't snap it back to the
        # height-dependent default anchor on the next tick.
        if self._target_hwnd is not None:
            rect = (get_client_rect_screen(self._target_hwnd)
                    or get_window_rect(self._target_hwnd))
            if rect is not None:
                w = max(1, rect.right - rect.left)
                h = max(1, rect.bottom - rect.top)
                self._user_offset = (
                    (self.x() - rect.left) / float(w),
                    (self.y() - rect.top) / float(h),
                )
        self._track()

    def _client_profile(self):
        """Return THIS floater's live data source.

        Active client: returns ``self._settings`` (the mock top-level
        mirror). UI writes from the dashboard / combat / capture pages
        land here directly, so the floater panel sees them on the next
        external-refresh tick without waiting for a tab swap to sync
        mock ↔ clients dict.

        Standby client: returns ``settings.clients[client_id]`` — the
        snapshot saved on the last active-tab swap.

        Legacy (no client_id): top-level mirror.
        """
        s = self._settings
        if s is None:
            return None
        if not self._client_id:
            return s
        try:
            if self._client_id == getattr(s, "active_client_id", None):
                return s    # mock = live writes from UI sections
            prof = s.clients.get(self._client_id)
            if prof is not None:
                return prof
        except Exception:
            pass
        return s

    def _collect_use_state(self) -> dict[str, bool]:
        """Snapshot the current use-flag of each tracked item."""
        s = self._client_profile()
        out: dict[str, bool] = {}
        if s is None:
            return out
        out["__pk__"] = bool(getattr(s.pk, "use", False))
        out["__potion__"] = bool(getattr(s.potion, "use", False))
        rec = getattr(s, "recovery", None)
        if rec is not None:
            out["__recovery__"] = bool(getattr(rec, "enabled", False))
        for sid, slot in getattr(s, "slots", {}).items():
            out[f"slot:{sid}"] = bool(slot.use)
        return out

    def _on_external_refresh(self) -> None:
        """Called from bus.slot_state_refresh / settings_dirty.

        Detects a True→False transition on any tracked item and
        auto-opens the panel so the user notices. ALWAYS rebuilds the
        panel's rows if it's currently open — even when the change came
        from our own sub-toggle click (the rebuild is cheap and the
        IOSToggle.set_state used inside it doesn't re-emit, so no
        recursion). The _suppress_emit latch only suppresses the
        auto-open behaviour to avoid re-opening the panel as a
        side-effect of the user's own click.
        """
        cur = self._collect_use_state()
        auto_off = False
        for key, was in self._prev_use.items():
            if was and not cur.get(key, False):
                auto_off = True
                break
        self._prev_use = cur
        if auto_off and not self._suppress_emit:
            self.set_panel_open(True)
        elif self._panel.isVisible():
            self._rebuild_panel()

    def _rebuild_panel(self) -> None:
        """Rebuild every row: PK, 물약, 사냥복귀, then every saved slot
        in sort order. All sub-toggles regardless of current use-state
        so the user sees the whole control surface at a glance."""
        while self._panel_layout.count():
            item = self._panel_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        s = self._client_profile()
        if s is None:
            return

        # PK / 물약 / 사냥복귀 — always shown at the top of the panel.
        self._add_row("PK", bool(s.pk.use),
                       lambda v: self._set_pk(bool(v)))
        self._add_row("물약", bool(s.potion.use),
                       lambda v: self._set_potion(bool(v)))
        rec = getattr(s, "recovery", None)
        if rec is not None:
            self._add_row("사냥복귀", bool(getattr(rec, "enabled", False)),
                            lambda v: self._set_recovery(bool(v)))

        # Every saved slot, sorted numerically (1..9, 0, then anything
        # custom like 11+).
        def _slot_sort_key(sid: str) -> tuple[int, int, str]:
            # Order: 1..9, 0, then numeric extras 11+, then non-numeric.
            try:
                n = int(sid)
            except ValueError:
                return (2, 0, sid)
            if 1 <= n <= 9:
                return (0, n, sid)
            if n == 0:
                return (0, 10, sid)
            return (1, n, sid)

        for sid in sorted(s.slots.keys(), key=_slot_sort_key):
            slot = s.slots[sid]
            label = slot.label or f"슬롯-{sid}"
            self._add_row(label, bool(slot.use),
                            lambda v, _sid=sid: self._set_slot(_sid, bool(v)))

    def _add_row(self, label_text: str, state: bool,
                  on_toggle) -> None:
        row = QWidget(self._panel)
        row.setFixedHeight(ROW_HEIGHT)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        lbl = QLabel(label_text)
        # font-size has to live INSIDE the stylesheet — Qt's stylesheet
        # engine overrides any QFont/setFont when there's a QSS rule
        # on the widget, which silently kept the rows at the default
        # ~9 pt instead of our intended ROW_LABEL_PT.
        lbl.setStyleSheet(
            f"QLabel {{"
            f" color:{'#cfd6e2' if state else '#7f8694'};"
            f" font-size:{ROW_LABEL_PT}pt;"
            f" padding:0; margin:0;"
            f"}}"
        )
        lbl.setMinimumWidth(0)
        h.addWidget(lbl, 1)
        tgl = IOSToggle(width=ROW_TOGGLE_W, height=ROW_TOGGLE_H, parent=row)
        # ``animate=False`` for the initial value — newly created
        # IOSToggle starts at OFF, and the default ``set_state(True)``
        # plays a 180 ms knob-slide animation, which the user sees as
        # a brief "OFF → ON flash" the first time the panel opens.
        # Subsequent user clicks still animate (set_state on the
        # mouse press path keeps animate=True).
        tgl.set_state(state, animate=False)
        tgl.toggled.connect(on_toggle)
        h.addWidget(tgl, 0)
        self._panel_layout.addWidget(row)

    # ───────── settings writes (scoped to this floater's client) ─────────
    def _set_pk(self, on: bool) -> None:
        p = self._client_profile()
        if p is None or bool(p.pk.use) == on:
            return
        p.pk.use = on
        self._broadcast_change()

    def _set_potion(self, on: bool) -> None:
        p = self._client_profile()
        if p is None or bool(p.potion.use) == on:
            return
        p.potion.use = on
        self._broadcast_change()

    def _set_recovery(self, on: bool) -> None:
        p = self._client_profile()
        if p is None or getattr(p, "recovery", None) is None:
            return
        if bool(p.recovery.enabled) == on:
            return
        p.recovery.enabled = on
        self._broadcast_change()

    def _set_slot(self, sid: str, on: bool) -> None:
        p = self._client_profile()
        if p is None or sid not in p.slots:
            return
        if bool(p.slots[sid].use) == on:
            return
        p.slots[sid].use = on
        self._broadcast_change()

    def _broadcast_change(self) -> None:
        """Emit the same signals an in-app toggle would so the rest
        of the UI (main iOS toggles in combat / slots sections) stays
        in lockstep with what the floater just did.

        ``_suppress_emit`` stays True past the emit so any QUEUED
        delivery of our own signals back to ``_on_external_refresh``
        is filtered out — that callback used to fire a full panel
        rebuild on every sub-toggle, which made the whole floater
        flicker / shift as widgets were torn down and recreated.
        We release the flag on the next event-loop tick via a
        zero-delay singleShot so it covers both immediate and
        queued connections.
        """
        if self._suppress_emit:
            return
        # Pre-snapshot so the next external refresh isn't fooled into
        # thinking *our* write was an auto-off event.
        self._suppress_emit = True
        try:
            from quickcast.ui.design.signals import bus
            bus.settings_dirty.emit()
            bus.slot_state_refresh.emit()
        except Exception:
            pass
        self._prev_use = self._collect_use_state()
        # Release the latch on the next event-loop tick so any signal
        # our emit produced has already been consumed and ignored.
        QTimer.singleShot(0, self._end_suppress)

    def _end_suppress(self) -> None:
        self._suppress_emit = False

    # ───────── window tracking ─────────
    def _track(self) -> None:
        if self._target_hwnd is None:
            return
        # NOTE: don't return early on _dragging here — the owner-restore
        # block at the end of this method MUST run even while dragging,
        # otherwise the mouse-down click that initiated the drag (which
        # clears GWLP_HWNDPARENT in some Qt code paths) leaves the
        # floater dangling and it visually disappears mid-drag.
        if not is_window_alive(self._target_hwnd):
            self._target_hwnd = None
            self.hide()
            return

        # Minimized game window → hide (no point showing the floater
        # for a window the user can't see anyway). All other cases let
        # the z-order coupling below decide.
        try:
            import ctypes
            user32 = ctypes.windll.user32
            is_min = bool(user32.IsIconic(self._target_hwnd))
            is_vis = bool(user32.IsWindowVisible(self._target_hwnd))
        except Exception:
            is_min = False; is_vis = True
        if is_min or not is_vis:
            if self.isVisible():
                self.hide()
            return

        # Skip the auto-position calculation while the user is actively
        # dragging — otherwise their movement gets fought by the tracker.
        # The owner-restore block below still runs so the click that
        # started the drag doesn't lose us behind the game.
        if not self._dragging:
            rect = (get_client_rect_screen(self._target_hwnd)
                    or get_window_rect(self._target_hwnd))
            if rect is not None:
                if self._user_offset is None:
                    tx = rect.right - self.width() - 8
                    ty = rect.top + 8 + self.height()
                else:
                    rx, ry = self._user_offset
                    w = max(1, rect.right - rect.left)
                    h = max(1, rect.bottom - rect.top)
                    tx = int(rect.left + rx * w)
                    ty = int(rect.top + ry * h)
                if (self.x(), self.y()) != (tx, ty):
                    self.move(tx, ty)
            if not self.isVisible():
                self.show()

        # Z-order: WindowStaysOnTopHint handles "always above the game"
        # automatically. No per-tick SetWindowPos / SetWindowLongPtr.

        # Foreground-based dim/highlight — this floater is solid only when
        # its own game window is the active foreground window; otherwise
        # dimmed. Independent of which QuickCast tab is selected.
        self._apply_foreground_opacity()

    # ───────── drag handlers ─────────
    def _on_drag_start(self, _global_pos: QPoint) -> None:
        self._drag_origin = self.pos()
        self._dragging = True

    def _on_drag_move(self, global_pos: QPoint) -> None:
        new_x = global_pos.x() - self.handle.x() - self.handle.width() // 2
        new_y = global_pos.y() - self.handle.y() - self.handle.height() // 2
        self.move(new_x, new_y)

    def _on_drag_end(self) -> None:
        self._dragging = False
        if self._target_hwnd is None:
            return
        rect = get_client_rect_screen(self._target_hwnd) or get_window_rect(self._target_hwnd)
        if rect is None:
            return
        # Save as fraction of the client rect so a resize keeps the
        # floater in the same relative spot. Top-left corner is the
        # anchor point (mirrors how _track positions it).
        w = max(1, rect.right - rect.left)
        h = max(1, rect.bottom - rect.top)
        rx = (self.x() - rect.left) / float(w)
        ry = (self.y() - rect.top) / float(h)
        self._user_offset = (rx, ry)
        # Persist into this client's ClientProfile so the position
        # survives re-attaches AND app restarts.
        prof = self._client_profile()
        if prof is not None:
            try:
                prof.floater_pos_x = float(rx)
                prof.floater_pos_y = float(ry)
                from quickcast.ui.design.signals import bus
                bus.settings_dirty.emit()
            except Exception:
                pass

    # ───────── paint ─────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(20, 25, 40, 220)))
        # Squarish corners when the expand panel is open, pill when
        # only the top bar is showing.
        radius = 12.0 if self._panel.isVisible() else self.height() / 2.0
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), radius, radius)


__all__ = ["FloatingSwitch"]
