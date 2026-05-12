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
ROW_TOGGLE_W = 26
ROW_TOGGLE_H = 14
ROW_LABEL_PT = 8

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._target_hwnd: Optional[int] = None
        self._user_offset: Optional[QPoint] = None
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
        self._target_hwnd = hwnd
        self._user_offset = None
        self._tracker.start()
        self._track()
        self.show()

    def detach(self) -> None:
        self._target_hwnd = None
        self._tracker.stop()
        self.hide()

    def set_state(self, on: bool) -> None:
        self.toggle.set_state(on)
        self.handle.set_state(on)

    def set_theme(self, _theme_id: str) -> None:
        pass

    def _on_toggle(self, on: bool) -> None:
        self.handle.set_state(on)
        self.toggled.emit(on)

    # ───────── expand panel ─────────
    def _toggle_panel(self) -> None:
        self.set_panel_open(not self._panel.isVisible())

    def set_panel_open(self, opened: bool) -> None:
        if opened:
            self._rebuild_panel()
        self._panel.setVisible(opened)
        self._divider.setVisible(opened)
        self._expand_btn.setIcon(Icon.get(
            "chevron-up" if opened else "chevron-down",
            EXPAND_BTN_PX, "#cfd6e2",
        ))
        # Anchor at top-right corner: re-fit and re-position so the
        # panel grows downward without shifting the toggle. We lock
        # the floater width to the top bar so the panel never inflates
        # the row above it.
        self.adjustSize()
        if self._top_width > 0:
            self.setFixedWidth(self._top_width)
        self._track()

    def _collect_use_state(self) -> dict[str, bool]:
        """Snapshot the current use-flag of each tracked item."""
        s = self._settings
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
        auto-opens the panel so the user notices. Always rebuilds the
        panel's rows if it's currently open.
        """
        cur = self._collect_use_state()
        auto_off = False
        for key, was in self._prev_use.items():
            if was and not cur.get(key, False):
                auto_off = True
                break
        self._prev_use = cur
        if auto_off:
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

        s = self._settings
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
        # Constrain row height so the panel stays vertically tight.
        row.setFixedHeight(ROW_TOGGLE_H + 4)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(
            f"color:{'#cfd6e2' if state else '#7f8694'};"
            f" padding:0; margin:0;"
        )
        f = QFont(); f.setPointSize(ROW_LABEL_PT); lbl.setFont(f)
        lbl.setMinimumWidth(0)
        h.addWidget(lbl, 1)
        tgl = IOSToggle(width=ROW_TOGGLE_W, height=ROW_TOGGLE_H, parent=row)
        tgl.set_state(state)
        tgl.toggled.connect(on_toggle)
        h.addWidget(tgl, 0)
        self._panel_layout.addWidget(row)

    # ───────── settings writes ─────────
    def _set_pk(self, on: bool) -> None:
        if self._settings is None:
            return
        if bool(self._settings.pk.use) == on:
            return
        self._settings.pk.use = on
        self._broadcast_change()

    def _set_potion(self, on: bool) -> None:
        if self._settings is None:
            return
        if bool(self._settings.potion.use) == on:
            return
        self._settings.potion.use = on
        self._broadcast_change()

    def _set_recovery(self, on: bool) -> None:
        s = self._settings
        if s is None or getattr(s, "recovery", None) is None:
            return
        if bool(s.recovery.enabled) == on:
            return
        s.recovery.enabled = on
        self._broadcast_change()

    def _set_slot(self, sid: str, on: bool) -> None:
        s = self._settings
        if s is None or sid not in s.slots:
            return
        if bool(s.slots[sid].use) == on:
            return
        s.slots[sid].use = on
        self._broadcast_change()

    def _broadcast_change(self) -> None:
        """Emit the same signals an in-app toggle would so the rest
        of the UI (main iOS toggles in combat / slots sections) stays
        in lockstep with what the floater just did."""
        if self._suppress_emit:
            return
        try:
            from quickcast.ui.design.signals import bus
            self._suppress_emit = True
            bus.settings_dirty.emit()
            bus.slot_state_refresh.emit()
        finally:
            self._suppress_emit = False
        # Update our snapshot so the next external refresh doesn't
        # mistake our own write for an auto-off event.
        self._prev_use = self._collect_use_state()

    # ───────── window tracking ─────────
    def _track(self) -> None:
        if self._target_hwnd is None:
            return
        if self._dragging:
            return
        if not is_window_alive(self._target_hwnd):
            self._target_hwnd = None
            self.hide()
            return
        rect = get_client_rect_screen(self._target_hwnd) or get_window_rect(self._target_hwnd)
        if rect is None:
            return
        if self._user_offset is None:
            tx = rect.right - self.width() - 8
            ty = rect.top + 8 + self.height()
        else:
            tx = rect.right - self.width() - self._user_offset.x()
            ty = rect.top + self._user_offset.y()
        if (self.x(), self.y()) != (tx, ty):
            self.move(tx, ty)

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
        self._user_offset = QPoint(
            rect.right - self.x() - self.width(),
            self.y() - rect.top,
        )

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
