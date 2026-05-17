"""Dashboard — live capture preview + HP/MP meters + recent events.

Uses the real `InteractivePreview` so drag-to-move and corner/edge
resize on the ROI rectangles work in the design preview too. The frame
underneath is a synthetic faux-game image so we can iterate without a
running game.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconOnlyButton
from quickcast.ui.components.meter import Meter
from quickcast.ui.design.icons import Icon
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.interactive_preview import InteractivePreview
from quickcast.ui.ios_toggle import IOSToggle
from quickcast.config import Settings


def _get_mock_settings() -> Settings:
    """Returns the shared mock Settings instance."""
    from quickcast.ui.sections._mock_state import mock_settings
    return mock_settings


_PK_TEMPLATE: np.ndarray | None = None
_POTION_TEMPLATE: np.ndarray | None = None


def _load_templates() -> None:
    """Load actual PK/Potion PNGs once; used so synthetic frames can
    reach the matchTemplate threshold (real recognition flow tested)."""
    global _PK_TEMPLATE, _POTION_TEMPLATE
    if _PK_TEMPLATE is not None:
        return
    import cv2
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent.parent / "data" / "targets"
    for path, attr in (("pk.png", "_PK_TEMPLATE"), ("potion.png", "_POTION_TEMPLATE")):
        p = base / path
        if not p.exists():
            continue
        data = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        globals()[attr] = img


def _make_synthetic_frame(tick: int) -> np.ndarray:
    """1280×720 BGRA frame the recognizer will actually parse.

    HP/MP bars use colours the recognition algorithm catches; PK and
    Potion regions are filled with the *real* embedded templates so
    matchTemplate returns a high enough score to trigger detection.
    """
    _load_templates()
    img = np.zeros((720, 1280, 4), dtype=np.uint8)
    img[:, :, 3] = 255
    img[:, :] = [22, 32, 18, 255]      # deep green canvas

    hp_ratio = 0.6 + 0.4 * abs((tick % 200 - 100) / 100)
    img[32:38, 90 : 90 + int(190 * hp_ratio)] = [60, 70, 240, 255]   # red
    mp_ratio = 0.4 + 0.5 * abs((tick % 240 - 120) / 120)
    img[45:51, 90 : 90 + int(190 * mp_ratio)] = [180, 170, 90, 255]

    # PK alert — paste real 25x25 template every ~1s
    if (tick // 30) % 4 == 0 and _PK_TEMPLATE is not None:
        h, w = _PK_TEMPLATE.shape[:2]
        img[533 : 533 + h, 1057 : 1057 + w] = _PK_TEMPLATE
    # Potion-empty — paste real 13x13 template every ~2s
    if (tick // 30) % 6 == 0 and _POTION_TEMPLATE is not None:
        h, w = _POTION_TEMPLATE.shape[:2]
        img[633 : 633 + h, 478 : 478 + w] = _POTION_TEMPLATE
    return img


class _LivePreviewWrap(QWidget):
    """Hosts an InteractivePreview + drives a synthetic frame at ~30fps.

    The frame is fed through the REAL `Recognizer` so HP/MP percentages
    and PK/potion match scores you see are computed by the same code
    path the actual macro uses. Only the input frame is synthetic.
    """

    recognition = Signal(int, int, bool, bool, float)
    # hp%, mp%, pk_match, potion_empty, fps

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0)
        self._settings = _get_mock_settings()
        self.preview = InteractivePreview(self._settings)
        v.addWidget(self.preview)

        # Real recognizer — not a mock. Reads the embedded PK/Potion templates.
        from quickcast.core.recognition import Recognizer
        self._recognizer = Recognizer()

        self._tick = 0
        self._external_mode = False
        self._timer = QTimer(self); self._timer.timeout.connect(self._advance)
        self._timer.start(33)

    def set_external_mode(self, on: bool) -> None:
        """When True, stop generating synthetic frames; expect feed_frame()."""
        self._external_mode = on
        if on:
            self._timer.stop()
        else:
            self._timer.start(33)

    def feed_frame(self, image, analysis, fps: float = 0.0) -> None:
        """Receive a real captured frame + analysis from the controller."""
        if image is None or analysis is None:
            return
        self.preview.update_frame(image)
        self.preview.update_recognition(
            hp=analysis.hp, mp=analysis.mp,
            pk_score=analysis.pk_score, potion_score=analysis.potion_score,
            pk_thr=self._settings.pk.threshold,
            potion_thr=self._settings.potion.threshold,
            pk_match_xy=analysis.pk_match_xy,
            potion_match_xy=analysis.potion_match_xy,
            pk_match_scale=analysis.pk_match_scale,
            potion_match_scale=analysis.potion_match_scale,
            overlay_matches=getattr(analysis, "overlay_matches", None),
        )
        self.recognition.emit(
            int(analysis.hp), int(analysis.mp),
            bool(analysis.pk_detected), bool(analysis.potion_empty),
            float(fps),
        )
        # Mirror to bus so the Combat tab's live PK/potion score
        # readout updates from real frames — not just from the
        # synthetic preview which only runs when the game is closed.
        from quickcast.ui.design.signals import bus
        bus.live_scores.emit(
            int(analysis.hp), int(analysis.mp),
            float(analysis.pk_score), float(analysis.potion_score),
            bool(analysis.pk_detected), bool(analysis.potion_empty),
            float(fps),
        )

    def _advance(self) -> None:
        from quickcast.core.capture import Frame as CapFrame
        from quickcast.ui.design.signals import bus
        self._tick = (self._tick + 1) % 100000
        img = _make_synthetic_frame(self._tick)
        analysis = self._recognizer.analyze(CapFrame(image=img), self._settings)

        self.preview.update_frame(img)
        self.preview.update_recognition(
            hp=analysis.hp, mp=analysis.mp,
            pk_score=analysis.pk_score, potion_score=analysis.potion_score,
            pk_thr=self._settings.pk.threshold,
            potion_thr=self._settings.potion.threshold,
            pk_match_xy=analysis.pk_match_xy,
            potion_match_xy=analysis.potion_match_xy,
            pk_match_scale=analysis.pk_match_scale,
            potion_match_scale=analysis.potion_match_scale,
            overlay_matches=getattr(analysis, "overlay_matches", None),
        )
        self.recognition.emit(
            analysis.hp, analysis.mp,
            analysis.pk_detected, analysis.potion_empty,
            60.0,
        )
        # Broadcast scores so other sections (Combat) can mirror live values.
        bus.live_scores.emit(
            analysis.hp, analysis.mp,
            float(analysis.pk_score), float(analysis.potion_score),
            analysis.pk_detected, analysis.potion_empty,
            60.0,
        )


def _event_row(time: str, label: str) -> QWidget:
    w = QWidget()
    w.setMinimumHeight(26)   # comfortable touch target + descender room
    h = QHBoxLayout(w); h.setContentsMargins(10, 4, 10, 4); h.setSpacing(10)
    t = QLabel(time)
    reactive(t, lambda: (
        f"color:{T.palette.text_tertiary};"
        f" font-family:{T.type.mono}; font-size:13px;"
    ))
    t.setMinimumWidth(56)
    txt = QLabel(label)
    reactive(txt, lambda: f"color:{T.palette.text_primary}; font-size:13px;")
    h.addWidget(t); h.addWidget(txt); h.addStretch(1)
    return w


# Real-event log starts empty — entries are appended at runtime by
# AppWindow when slots fire / alarms trigger / capture changes. The
# previous demo data has been removed.
_RECENT_EVENTS: list[tuple[str, str]] = []


from quickcast.ui.sections._mock_state import slot_state, alarm_state

# 리니지 퍼플 톤 — 쿨다운 카운터 텍스트 / 행 하단 게이지 / ↺ 리셋 버튼.
# 팔레트 state_warning(주황)을 쓰면 PK 위험 알림과 시각 충돌하므로 별도 컬러.
_LP_PURPLE = "#8B5CF6"
_LP_PURPLE_HOVER = "#A78BFA"


class _SkillToggleChip(QWidget):
    """Compact slot toggle synced with the global slot_state."""

    def __init__(self, slot_id: str) -> None:
        super().__init__()
        from quickcast.ui.ios_toggle import IOSToggle
        self.slot_id = slot_id
        h = QHBoxLayout(self); h.setContentsMargins(8, 4, 10, 4); h.setSpacing(8)
        self.sw = IOSToggle(width=32, height=18)
        self.sw.set_state(slot_state.is_on(slot_id), animate=False)
        self.txt = QLabel(f"#{slot_id} {slot_state.label(slot_id)}")
        self._restyle()
        h.addWidget(self.sw); h.addWidget(self.txt); h.addStretch(1)

        self.sw.toggled.connect(self._on_local_toggle)
        slot_state.slot_toggled.connect(self._on_global_toggle)

    def _restyle(self) -> None:
        on = slot_state.is_on(self.slot_id)
        self.txt.setStyleSheet(
            f"color:{T.palette.text_primary if on else T.palette.text_tertiary};"
            f" font-size:12px;"
        )

    def _on_local_toggle(self, on: bool) -> None:
        slot_state.set_on(self.slot_id, on)

    def _on_global_toggle(self, sid: str, on: bool) -> None:
        if sid != self.slot_id:
            return
        if self.sw.is_on() != on:
            self.sw.set_state(on, animate=True)
        # Refresh "#id label" so renames on the Slots page show up here.
        self.txt.setText(f"#{sid} {slot_state.label(sid)}")
        self._restyle()


class _AlarmToggleChip(QWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        from quickcast.ui.ios_toggle import IOSToggle
        self.name = name
        h = QHBoxLayout(self); h.setContentsMargins(8, 4, 10, 4); h.setSpacing(8)
        self.sw = IOSToggle(width=32, height=18)
        self.sw.set_state(alarm_state.is_on(name), animate=False)
        self.txt = QLabel(name)
        self._restyle()
        h.addWidget(self.sw); h.addWidget(self.txt); h.addStretch(1)
        self.sw.toggled.connect(self._on_local)
        alarm_state.alarm_toggled.connect(self._on_global)

    def _restyle(self) -> None:
        on = alarm_state.is_on(self.name)
        self.txt.setStyleSheet(
            f"color:{T.palette.text_primary if on else T.palette.text_tertiary};"
            f" font-size:12px;"
        )

    def _on_local(self, on: bool) -> None:
        alarm_state.set_on(self.name, on)

    def _on_global(self, name: str, on: bool) -> None:
        if name != self.name:
            return
        if self.sw.is_on() != on:
            self.sw.set_state(on, animate=True)
        self._restyle()


class _SidebarSkillRow(QWidget):
    """Sidebar row: [toggle] [#id/cd] [name] [kbd or ✕] + bottom cooldown gauge."""
    def __init__(self, slot_id: str) -> None:
        super().__init__()
        from PySide6.QtWidgets import QPushButton, QStackedWidget
        self.slot_id = slot_id
        # 0.0 = ready, 1.0 = just triggered. Drives the id-label text swap
        # (#id ↔ countdown), the bottom progress bar, and the kbd ↔ ✕ swap.
        self._cd_ratio = 0.0
        self.setMinimumHeight(32)
        h = QHBoxLayout(self); h.setContentsMargins(8, 4, 10, 4); h.setSpacing(8)
        self.sw = IOSToggle(width=32, height=18)
        self.sw.set_state(slot_state.is_on(slot_id), animate=False)
        self.sw.toggled.connect(self._on_local)
        h.addWidget(self.sw)
        # #id when ready, becomes a cooldown counter (e.g. "5.3s",
        # "12m30s", "2h46m") while the slot is cooling down. Fixed
        # width keeps the row from dancing as the text length changes.
        self.id_lbl = QLabel(f"#{slot_id}")
        self.id_lbl.setFixedWidth(40)
        h.addWidget(self.id_lbl)
        self.name_lbl = QLabel(slot_state.label(slot_id))
        h.addWidget(self.name_lbl, stretch=1)
        # Right-edge slot: keyboard label normally, ✕ reset button while
        # cooling. QStackedWidget keeps the row width perfectly stable.
        self.kbd = QLabel(slot_state.key(slot_id))
        self.kbd.setAlignment(Qt.AlignCenter)
        self.x_btn = QPushButton("↺")
        self.x_btn.setCursor(Qt.PointingHandCursor)
        self.x_btn.setToolTip("쿨타임 즉시 해제")
        self.x_btn.clicked.connect(self._on_reset_click)
        self.right_stack = QStackedWidget()
        self.right_stack.setFixedSize(36, 22)
        self.right_stack.addWidget(self.kbd)      # index 0 — ready state
        self.right_stack.addWidget(self.x_btn)    # index 1 — cooling state
        self.right_stack.setCurrentIndex(0)
        h.addWidget(self.right_stack)
        from quickcast.ui.design.signals import bus
        bus.theme_changed.connect(self._restyle)
        slot_state.slot_toggled.connect(self._on_global)
        bus.slot_cooldown_tick.connect(self._on_cooldown_tick)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        on = slot_state.is_on(self.slot_id)
        cooling = self._cd_ratio > 0.0
        # While cooling, the id slot doubles as the countdown — uses
        # Lineage Purple so it's visually distinct from PK 위험(주황) /
        # 알림 정지(붉은) accents elsewhere.
        id_color = _LP_PURPLE if cooling else p.text_tertiary
        id_weight = 600 if cooling else 400
        self.id_lbl.setStyleSheet(
            f"color:{id_color}; font-family:{T.type.mono};"
            f" font-size:12px; font-weight:{id_weight};"
        )
        self.name_lbl.setStyleSheet(
            f"color:{p.text_primary if on else p.text_tertiary};"
            f" font-size:13px; font-weight:500;"
        )
        self.kbd.setStyleSheet(
            f"background:{p.bg_input}; color:{p.text_secondary};"
            f" border:1px solid {p.border_default};"
            f" border-radius:4px; padding:2px 7px;"
            f" font-family:{T.type.mono}; font-size:11px;"
        )
        self.x_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{_LP_PURPLE};"
            f" border:none; padding:0;"
            f" font-size:15px; font-weight:700; }}"
            f"QPushButton:hover {{ color:{_LP_PURPLE_HOVER}; }}"
        )

    @staticmethod
    def _fmt_cd(rem: float) -> str:
        """5.3s / 12m30s / 2h46m  — keeps under 6 chars for any duration."""
        if rem >= 3600.0:
            hh = int(rem // 3600)
            mm = int((rem % 3600) // 60)
            return f"{hh}h{mm:02d}m"
        if rem >= 60.0:
            mm = int(rem // 60)
            ss = int(rem % 60)
            return f"{mm}m{ss:02d}s"
        return f"{rem:.1f}s"

    def _on_cooldown_tick(self, payload: dict) -> None:
        data = payload.get(self.slot_id)
        # Back-compat: accept either (rem, total) tuple or bare seconds.
        if isinstance(data, (tuple, list)):
            rem = float(data[0] or 0.0)
            total = float(data[1] or 0.0) if len(data) > 1 else 0.0
        else:
            rem = float(data or 0.0)
            total = 0.0
        # 0.05s 미만은 "준비됨"으로 간주 — 깜빡임 방지.
        cooling = rem > 0.05
        new_text = self._fmt_cd(rem) if cooling else f"#{self.slot_id}"
        new_ratio = (rem / total) if (cooling and total > 0.0) else 0.0
        new_ratio = max(0.0, min(1.0, new_ratio))
        was_cooling = self._cd_ratio > 0.0
        if self.id_lbl.text() != new_text:
            self.id_lbl.setText(new_text)
        if abs(new_ratio - self._cd_ratio) > 0.005 or (cooling != was_cooling):
            self._cd_ratio = new_ratio
            self.update()       # repaint bottom gauge
        if cooling != was_cooling:
            self.right_stack.setCurrentIndex(1 if cooling else 0)
            self._restyle()     # swap id-label color/weight

    def _on_reset_click(self) -> None:
        from quickcast.ui.design.signals import bus
        bus.slot_cooldown_reset_request.emit(self.slot_id)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._cd_ratio <= 0.0:
            return
        color = QColor(_LP_PURPLE)
        # Bar lives strictly between the left toggle and the right
        # stack (kbd/↺) — running edge-to-edge looked off against
        # the row's existing inner padding.
        left = self.sw.geometry().right() + 1
        right = self.right_stack.geometry().left() - 1
        available = max(0, right - left)
        if available <= 0:
            return
        bar_h = 2
        bar_w = int(available * self._cd_ratio)
        if bar_w <= 0:
            return
        p = QPainter(self)
        p.fillRect(left, self.height() - bar_h, bar_w, bar_h, color)
        p.end()

    def _on_local(self, on: bool) -> None:
        slot_state.set_on(self.slot_id, on)

    def _on_global(self, sid: str, on: bool) -> None:
        if sid != self.slot_id:
            return
        if self.sw.is_on() != on:
            self.sw.set_state(on, animate=True)
        # Pull current label + key from state so renames / key changes
        # made on the Slots page propagate to the dashboard sidebar
        # without a restart.
        self.name_lbl.setText(slot_state.label(self.slot_id))
        self.kbd.setText(slot_state.key(self.slot_id))
        self._restyle()


class _SidebarAlarmRow(QWidget):
    """Sidebar row for alarms: [toggle] [name] [time | 정지 button when ringing]."""
    def __init__(self, name: str, time_str: str = "20:50") -> None:
        super().__init__()
        self.name = name
        self._is_ringing = False
        self.setMinimumHeight(34)
        h = QHBoxLayout(self); h.setContentsMargins(8, 4, 10, 4); h.setSpacing(8)
        self.sw = IOSToggle(width=32, height=18)
        self.sw.set_state(alarm_state.is_on(name), animate=False)
        self.sw.toggled.connect(self._on_local)
        h.addWidget(self.sw)
        self.name_lbl = QLabel(name)
        h.addWidget(self.name_lbl, stretch=1)
        self.time_lbl = QLabel(time_str)
        h.addWidget(self.time_lbl)
        # Stop-ringing pill — hidden until alarm_repeat_active(label,True).
        from PySide6.QtWidgets import QPushButton
        self.stop_btn = QPushButton("🔔 정지")
        self.stop_btn.setFixedHeight(22)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.hide()
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        h.addWidget(self.stop_btn)
        from quickcast.ui.design.signals import bus
        bus.theme_changed.connect(self._restyle)
        alarm_state.alarm_toggled.connect(self._on_global)
        bus.alarm_repeat_active.connect(self._on_repeat_state)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        on = alarm_state.is_on(self.name)
        # Highlight ringing alarms with the warning accent so they
        # stand out in the sidebar at a glance.
        ringing = self._is_ringing
        primary_col = (
            p.state_warning if ringing else (p.text_primary if on else p.text_tertiary)
        )
        weight = 700 if ringing else 500
        self.name_lbl.setStyleSheet(
            f"color:{primary_col}; font-size:13px; font-weight:{weight};"
        )
        self.time_lbl.setStyleSheet(
            f"color:{p.text_tertiary}; font-family:{T.type.mono}; font-size:12px;"
        )
        self.stop_btn.setStyleSheet(
            f"QPushButton {{ background:{p.state_warning}; color:white;"
            f" border:none; border-radius:11px; padding:0 10px;"
            f" font-size:11px; font-weight:700; }}"
            f"QPushButton:hover {{ background:{p.state_danger}; }}"
        )

    def _on_local(self, on: bool) -> None:
        alarm_state.set_on(self.name, on)

    def _on_global(self, name: str, on: bool) -> None:
        if name != self.name:
            return
        if self.sw.is_on() != on:
            self.sw.set_state(on, animate=True)
        self._restyle()

    def _on_repeat_state(self, label: str, active: bool) -> None:
        if label != self.name:
            return
        self._is_ringing = active
        # Show the stop button + hide time when ringing; restore when stopped.
        if active:
            self.time_lbl.hide()
            self.stop_btn.show()
        else:
            self.stop_btn.hide()
            self.time_lbl.show()
        self._restyle()

    def _on_stop_clicked(self) -> None:
        from quickcast.ui.design.signals import bus
        bus.alarm_stop_request.emit(self.name)


def attach_recognition_to_statusbar(preview, status_bar) -> None:
    """Wire the synthetic preview's `recognition` signal to the StatusBar
    so HP/MP/PK/Potion/FPS at the bottom reflect real analysis values.

    Called once from preview_shell after the AppShell is built.
    """
    def _on(hp_v: int, mp_v: int, pk_m: bool, po_m: bool, fps_v: float) -> None:
        status_bar.update_hp(hp_v)
        status_bar.update_mp(mp_v)
        status_bar.update_pk(pk_m)
        status_bar.update_potion(po_m)
        status_bar.update_fps(fps_v)
    preview.recognition.connect(_on)


def _sidebar_quick_toggle(label: str, target_obj, attr: str) -> QWidget:
    """One row in the sidebar quick-toggle group: [label] [iOS toggle].

    Two-way sync via `bus.slot_state_refresh` — every toggle bound to
    the same `(target_obj, attr)` re-reads after any of them writes,
    so flipping this dashboard sidebar toggle propagates to the
    Combat tab's "사용" toggle (and vice versa) without piecemeal
    per-control wiring.
    """
    from quickcast.ui.design.signals import bus as _bus
    row = QWidget()
    h = QHBoxLayout(row); h.setContentsMargins(12, 4, 12, 4); h.setSpacing(8)
    lbl = QLabel(label)
    reactive(lbl, lambda: f"color:{T.palette.text_primary}; font-size:13px;")
    sw = IOSToggle(width=36, height=18)
    sw.set_state(bool(getattr(target_obj, attr)), animate=False)
    def _on(on: bool) -> None:
        if getattr(target_obj, attr) != on:
            setattr(target_obj, attr, on)
            _bus.settings_dirty.emit()
            # Broadcast so siblings bound to the same field re-read.
            _bus.slot_state_refresh.emit()
    sw.toggled.connect(_on)

    def _resync() -> None:
        cur = bool(getattr(target_obj, attr))
        if sw.is_on() != cur:
            sw.set_state(cur, animate=True)
    _bus.slot_state_refresh.connect(_resync)

    h.addWidget(lbl); h.addStretch(1); h.addWidget(sw)
    return row


def make_dashboard() -> tuple[QWidget, QWidget]:
    # Local imports — `bus` is referenced repeatedly below for the
    # live-rebuild signals (slot list, alarm list, log, picks).
    from quickcast.ui.design.signals import bus

    # ── Sidebar — quick toggles (response + skill + alarm) ──
    sidebar = QWidget()
    sv = QVBoxLayout(sidebar); sv.setContentsMargins(8, 6, 8, 8); sv.setSpacing(4)

    # ── Top group: PK 대응 / 물약 대응 / 사냥 복귀 ──
    from quickcast.ui.sections._mock_state import mock_settings as _ms
    resp_head = QLabel("자동 대응")
    reactive(resp_head, lambda: (
        f"color:{T.palette.text_primary};"
        f" padding:10px 12px 6px 12px;"
        f" font-size:15px; font-weight:700;"
    ))
    sv.addWidget(resp_head)
    sv.addWidget(_sidebar_quick_toggle("PK 대응",  _ms.pk,       "use"))
    sv.addWidget(_sidebar_quick_toggle("물약 대응", _ms.potion,   "use"))
    sv.addWidget(_sidebar_quick_toggle("사냥 복귀", _ms.recovery, "enabled"))

    sk_head = QLabel("스킬 토글")
    reactive(sk_head, lambda: (
        f"color:{T.palette.text_primary};"
        f" padding:10px 12px 6px 12px;"
        f" font-size:15px; font-weight:700;"
    ))
    sv.addWidget(sk_head)
    # Slot rows live inside a dedicated container so we can rebuild the
    # whole list when the user adds / deletes a slot on the Slots page.
    skill_box = QWidget()
    skill_lay = QVBoxLayout(skill_box)
    skill_lay.setContentsMargins(0, 0, 0, 0); skill_lay.setSpacing(0)
    sv.addWidget(skill_box)

    def _rebuild_skill_rows() -> None:
        # Wipe then repopulate — the row widgets are tiny, this is fine.
        while skill_lay.count():
            item = skill_lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for sid in slot_state.order():
            skill_lay.addWidget(_SidebarSkillRow(sid))

    _rebuild_skill_rows()
    # Subscribe to slot list mutations so deletions on the Slots page
    # propagate to the dashboard sidebar without an app restart.
    bus.slot_list_changed.connect(_rebuild_skill_rows)

    al_head = QLabel("알림 토글")
    reactive(al_head, lambda: (
        f"color:{T.palette.text_primary};"
        f" padding:18px 12px 6px 12px;"
        f" font-size:15px; font-weight:700;"
    ))
    sv.addWidget(al_head)
    times = {"혈던": "20:50", "격전": "20:50", "어질리티 대회": "17:00", "마족신전": "20:50"}
    alarm_box = QWidget()
    alarm_lay = QVBoxLayout(alarm_box)
    alarm_lay.setContentsMargins(0, 0, 0, 0); alarm_lay.setSpacing(0)
    sv.addWidget(alarm_box)

    def _rebuild_alarm_rows() -> None:
        while alarm_lay.count():
            item = alarm_lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        # Pull HH:MM from the live settings alarm list; fall back to
        # the legacy hardcoded `times` map for the original 4 names.
        from quickcast.ui.sections._mock_state import mock_settings as _ms2
        live_times = {al.label: f"{al.hour:02d}:{al.minute:02d}"
                      for al in getattr(_ms2, "alarms", [])}
        for nm in alarm_state.order():
            t = live_times.get(nm) or times.get(nm, "00:00")
            alarm_lay.addWidget(_SidebarAlarmRow(nm, t))

    _rebuild_alarm_rows()
    bus.alarm_list_changed.connect(_rebuild_alarm_rows)

    sv.addStretch(1)

    # ── Main: preview + meters + status ──
    main = QWidget()
    v = QVBoxLayout(main); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(14)

    # Header row with preview controls
    header = QHBoxLayout(); header.setSpacing(8)
    title = QLabel("실시간 모니터링")
    f = QFont(); f.setBold(True); f.setPointSize(18); title.setFont(f)
    header.addWidget(title); header.addStretch(1)

    # (사냥 복귀 빠른 토글은 좌측 사이드바 "자동 대응" 그룹으로 이동됨)

    lock_btn = IconOnlyButton("lock", size="md",
                              tooltip="ROI 잠금 — 켜면 사각형 드래그/리사이즈 비활성화")
    lock_btn.setCheckable(True)
    pause_btn = IconOnlyButton("pause", size="md",
                                tooltip="일시정지 — 매크로 사용 중단 (마스터 OFF 동등)")
    pause_btn.setCheckable(True)
    fs_btn = IconOnlyButton("maximize-2", size="md", tooltip="전체화면 (F11)")
    header.addWidget(lock_btn); header.addWidget(pause_btn); header.addWidget(fs_btn)
    v.addLayout(header)

    # Pause = master OFF (and resume = master ON). Routes through the
    # same handler the title-bar Master toggle uses, so all sources
    # stay in sync (statusbar, floater, controller).
    def _on_pause_toggled(on: bool) -> None:
        from PySide6.QtWidgets import QApplication
        for w in QApplication.topLevelWidgets():
            if hasattr(w, "_on_master_toggled"):
                w._on_master_toggled(not on)
                break
    pause_btn.toggled.connect(_on_pause_toggled)

    # ── Capture preview (top, expanding) ──
    prev_card = Card("", expanding=True)
    preview = _LivePreviewWrap()
    prev_card.add(preview)
    v.addWidget(prev_card, stretch=2)

    # Wire recovery pick-mode requests to the preview's InteractivePreview.
    from quickcast.ui.design.signals import bus as _pick_bus
    def _on_pick_request(idx: int) -> None:
        from quickcast.utils.logger import logger
        logger.debug(f"recovery: pick request step #{idx+1}")
        preview.preview.enter_pick_mode(idx)
        # Ask AppWindow to switch to Dashboard tab so the user can click.
        _pick_bus.activate_section.emit("dashboard")
    _pick_bus.recovery_pick_request.connect(_on_pick_request)
    # When pick completes, write x/y back into mock_settings.recovery.steps
    # and emit settings_dirty so AppWindow saves.
    def _on_pick_done(idx: int, x: int, y: int) -> None:
        from quickcast.utils.logger import logger
        from quickcast.ui.sections._mock_state import mock_settings
        rec = getattr(mock_settings, "recovery", None)
        if rec is None or idx >= len(rec.steps):
            logger.warning(f"recovery: pick_done idx {idx} out of range")
            return
        rec.steps[idx].x = int(x); rec.steps[idx].y = int(y)
        logger.info(f"📍 단계 {idx+1} '{rec.steps[idx].label}' 좌표 ({x},{y})")
        _pick_bus.settings_dirty.emit()
        _pick_bus.activate_section.emit("combat")
    _pick_bus.recovery_pick_done.connect(_on_pick_done)

    # Item-close pick mode — same flow, single coord into Settings.item_close.
    def _on_ic_pick_request() -> None:
        from quickcast.utils.logger import logger
        logger.debug("item-close: pick request")
        preview.preview.enter_item_close_pick_mode()
        _pick_bus.activate_section.emit("dashboard")
    _pick_bus.item_close_pick_request.connect(_on_ic_pick_request)

    def _on_ic_pick_done(x: int, y: int) -> None:
        from quickcast.utils.logger import logger
        from quickcast.ui.sections._mock_state import mock_settings
        mock_settings.item_close.x = int(x)
        mock_settings.item_close.y = int(y)
        logger.info(f"📍 아이템 닫기 좌표 ({x},{y})")
        _pick_bus.settings_dirty.emit()
        _pick_bus.activate_section.emit("capture")
    _pick_bus.item_close_pick_done.connect(_on_ic_pick_done)

    # ── ROI lock toggle wiring ──
    from quickcast.ui.sections._mock_state import mock_settings
    initial_locked = bool(getattr(mock_settings, "roi_locked", False))
    lock_btn.setChecked(initial_locked)
    preview.preview.set_view_only(initial_locked)
    lock_btn.setIcon(Icon.get("lock" if initial_locked else "unlock", 16))

    def _on_lock_toggled(on: bool) -> None:
        preview.preview.set_view_only(on)
        lock_btn.setIcon(Icon.get("lock" if on else "unlock", 16))
        lock_btn.setToolTip(
            "ROI 잠금 해제" if on else
            "ROI 잠금 — 켜면 사각형 드래그/리사이즈 비활성화"
        )
        if mock_settings.roi_locked != on:
            mock_settings.roi_locked = on
            from quickcast.ui.design.signals import bus as _bus
            _bus.settings_dirty.emit()
    lock_btn.toggled.connect(_on_lock_toggled)

    # ── Log below preview ──
    # QPlainTextEdit gives us drag-select + Ctrl+C + native context
    # menu out-of-the-box, which the previous QLabel-per-row layout
    # couldn't do. Read-only flag prevents typing; we use append() so
    # auto-scroll-on-bottom is the default Qt behaviour.
    from PySide6.QtWidgets import QPlainTextEdit
    log_card = Card("")
    log_view = QPlainTextEdit()
    log_view.setReadOnly(True)
    log_view.setMaximumBlockCount(500)        # ring-buffer-style cap
    log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
    log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    log_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    log_view.setFixedHeight(8 * 22 + 14)      # ~8 rows visible
    f = QFont(T.type.mono); f.setPointSize(11); log_view.setFont(f)
    def _log_qss() -> str:
        p = T.palette
        return (
            f"QPlainTextEdit {{ background:{p.bg_input}; color:{p.text_primary};"
            f" border:1px solid {p.border_subtle}; border-radius:4px;"
            f" padding:6px 8px; selection-background-color:{p.accent_subtle};"
            f" selection-color:{p.text_primary}; }}"
        )
    reactive(log_view, _log_qss)
    log_card.add(log_view)
    v.addWidget(log_card)

    import time as _time_mod
    from quickcast.ui.design.signals import bus as _log_bus
    def _on_log_entry(level: str, message: str) -> None:
        try:
            # Capture bottom-state BEFORE append so user-initiated
            # scroll-up doesn't get yanked back to bottom by new lines.
            sb = log_view.verticalScrollBar()
            was_at_bottom = sb.value() >= sb.maximum() - 4
            ts = _time_mod.strftime("%H:%M:%S")
            log_view.appendPlainText(f"{ts}  {message}")
            if was_at_bottom:
                QTimer.singleShot(0, lambda b=sb: b.setValue(b.maximum()))
        except Exception:
            pass
    _log_bus.log_entry.connect(_on_log_entry)

    # Wire fullscreen button — opens a borderless window that MIRRORS
    # the dashboard preview's real captured frames (not a fresh
    # synthetic feed). Subscribes to bus.live_frame so anything the
    # AppWindow sees reaches the fullscreen too.
    def _open_fullscreen() -> None:
        fs = QWidget()
        fs.setWindowTitle("캡처 전체화면 — ESC/F11 종료")
        fs.setStyleSheet(f"background:{T.palette.bg_canvas};")
        fl = QVBoxLayout(fs); fl.setContentsMargins(0, 0, 0, 0)
        fs_wrap = _LivePreviewWrap()
        fs_wrap.set_external_mode(True)   # disable its own synthetic timer
        # Mirror the latest frame from AppWindow's capture bridge.
        from quickcast.ui.design.signals import bus as _bus
        def _mirror(image, analysis, fps):
            try:
                fs_wrap.feed_frame(image, analysis, fps)
            except Exception:
                pass
        _bus.live_frame.connect(_mirror)
        # Also forward whatever the main dashboard preview ALREADY has
        # so the fullscreen isn't blank for the first frame.
        try:
            last = preview.preview._frame_pixmap   # may be None
            if last is not None:
                fs_wrap.preview._frame_pixmap = last
                fs_wrap.preview._frame_size = preview.preview._frame_size
                fs_wrap.preview.update()
        except Exception:
            pass
        fl.addWidget(fs_wrap)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Escape"), fs, activated=fs.close)
        QShortcut(QKeySequence("F11"), fs, activated=fs.close)

        # Floating restore button — child of fs, positioned via resizeEvent.
        restore = IconOnlyButton("minimize-2", size="lg", tooltip="원복 (ESC/F11)")
        restore.setParent(fs)
        restore.setStyleSheet(
            f"QToolButton {{ background:{T.palette.bg_elevated};"
            f" color:{T.palette.text_primary};"
            f" border:1px solid {T.palette.border_default};"
            f" border-radius:6px; padding:6px; }}"
            f"QToolButton:hover {{ background:{T.palette.bg_hover}; }}"
        )
        restore.clicked.connect(fs.close)
        restore.raise_()

        def _layout_restore(_e=None) -> None:
            r = restore.sizeHint()
            restore.setGeometry(fs.width() - r.width() - 16, 16, r.width(), r.height())
        # First placement + repositioning whenever fs resizes
        fs.resizeEvent = lambda e: (_layout_restore(), QWidget.resizeEvent(fs, e))
        fs.showFullScreen()
        _layout_restore()
        main._fs_window = fs
    fs_btn.clicked.connect(_open_fullscreen)

    # (라이브 인식·상태 카드는 StatusBar로 이동 — 여기는 비워둠)

    # Expose the preview so preview_shell.py can wire its `recognition`
    # signal directly into the StatusBar via attach_recognition_to_statusbar().
    main._dashboard_preview = preview   # type: ignore[attr-defined]
    return sidebar, main


def _dim(text: str) -> str:
    """Helper: returns a tiny secondary-coloured label (used as column header)."""
    return text  # kept simple; styling done by setStyleSheet on QLabel where needed


__all__ = ["make_dashboard"]
