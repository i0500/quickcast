"""Alerts — alarm list + popup options.

Reads `mock_settings.alarms` (the production Settings instance after
state_bridge.install) and renders one row per alarm. All edits write
back to the Alarm model and emit `bus.settings_dirty` for save.
Add / delete operations also update `alarm_state` so the dashboard
sidebar stays in sync.
"""
from __future__ import annotations

import uuid

from PySide6.QtCore import QTime, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTimeEdit, QVBoxLayout, QWidget,
)

from quickcast.config import Alarm
from quickcast.ui.components.card import Card
from quickcast.ui.components.empty_state import EmptyState
from quickcast.ui.components.icon_button import IconButton, IconOnlyButton
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.ios_toggle import IOSToggle
from quickcast.ui.sections._mock_state import alarm_state, mock_settings
from quickcast.ui.stepper import Stepper

DAYS = ["일", "월", "화", "수", "목", "금", "토"]


class _AlarmSidebarRow(QWidget):
    def __init__(self, name: str, time_str: str, selected: bool = False) -> None:
        super().__init__()
        self.name = name
        self._selected = selected
        self.setMinimumHeight(36)
        h = QHBoxLayout(self); h.setContentsMargins(10, 6, 10, 6); h.setSpacing(8)
        self.dot = QLabel("●")
        h.addWidget(self.dot)
        self.name_lbl = QLabel(name)
        h.addWidget(self.name_lbl, stretch=1)
        self.time_lbl = QLabel(time_str)
        h.addWidget(self.time_lbl)
        bus.theme_changed.connect(self._restyle)
        alarm_state.alarm_toggled.connect(self._on_global)
        self._restyle()

    def _on_global(self, name: str, _on: bool) -> None:
        if name == self.name:
            self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        on = alarm_state.is_on(self.name)
        self.dot.setStyleSheet(
            f"color:{p.state_success if on else p.text_tertiary}; font-size:11px;"
        )
        self.name_lbl.setStyleSheet(
            f"color:{p.text_primary if on else p.text_tertiary};"
        )
        self.time_lbl.setStyleSheet(
            f"color:{p.text_tertiary}; font-family:{T.type.mono};"
        )
        if self._selected:
            self.setStyleSheet(
                f"_AlarmSidebarRow {{ background:{p.accent_subtle}; border-radius:6px; }}"
            )
        else:
            self.setStyleSheet(
                f"_AlarmSidebarRow:hover {{ background:{p.bg_hover}; border-radius:6px; }}"
            )


def _bind_alarm_row(alarm: Alarm, on_delete) -> Card:
    """Build one editable alarm row bound to `alarm` (an Alarm instance)."""
    card = Card("")
    card.body.setContentsMargins(14, 10, 14, 10)
    card.body.setSpacing(8)

    # ── Row 1: enable / name / time / mode / repeat-min / delete ──
    head = QHBoxLayout(); head.setSpacing(8)

    sw = IOSToggle(width=40, height=22)
    sw.set_state(bool(alarm.enabled), animate=False)
    def _on_enabled(on: bool) -> None:
        if alarm.enabled != on:
            alarm.enabled = on
            alarm_state.set_on(alarm.label, on)   # mirror to dashboard
            bus.settings_dirty.emit()
    sw.toggled.connect(_on_enabled)
    head.addWidget(sw)

    name = QLineEdit(alarm.label); name.setMinimumWidth(180); name.setFixedHeight(32)
    def _on_name() -> None:
        new = name.text().strip()
        if not new or new == alarm.label:
            return
        old = alarm.label
        alarm.label = new
        # Migrate the alarm_state entry to the new label.
        was_on = alarm_state.is_on(old)
        alarm_state._on.pop(old, None)
        alarm_state._on[new] = was_on
        alarm_state.alarm_toggled.emit(new, was_on)
        bus.settings_dirty.emit()
        bus.alarm_list_changed.emit()
    name.editingFinished.connect(_on_name)
    head.addWidget(name, stretch=1)

    time = QTimeEdit(QTime(alarm.hour, alarm.minute))
    time.setDisplayFormat("HH:mm"); time.setFixedSize(86, 32)
    def _time_qss() -> str:
        p = T.palette
        return (
            f"QTimeEdit {{ background:{p.bg_input}; color:{p.text_primary};"
            f" border:1px solid {p.border_default}; border-radius:4px;"
            f" padding:2px 6px; }}"
            f"QTimeEdit:focus {{ border-color:{p.border_focus}; }}"
            f"QTimeEdit::up-button, QTimeEdit::down-button"
            f" {{ width:0; height:0; border:none; }}"
        )
    reactive(time, _time_qss)
    def _on_time(t: QTime) -> None:
        if alarm.hour != t.hour() or alarm.minute != t.minute():
            alarm.hour = t.hour(); alarm.minute = t.minute()
            bus.settings_dirty.emit()
            # Also notify list-rebuilders so the dashboard sidebar
            # updates the displayed HH:MM beside this alarm name.
            bus.alarm_list_changed.emit()
    time.timeChanged.connect(_on_time)
    head.addWidget(time)

    mode_cb = QComboBox(); mode_cb.addItems(["반복", "1회"])
    mode_cb.setCurrentIndex(0 if alarm.mode == "repeat" else 1)
    mode_cb.setFixedSize(76, 32)
    def _on_mode(idx: int) -> None:
        new = "repeat" if idx == 0 else "once"
        if alarm.mode != new:
            alarm.mode = new
            bus.settings_dirty.emit()
            bus.alarm_list_changed.emit()
    mode_cb.currentIndexChanged.connect(_on_mode)
    head.addWidget(mode_cb)

    repeat_lbl = QLabel("재알림")
    reactive(repeat_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    head.addWidget(repeat_lbl)
    rep = Stepper(alarm.repeat_minutes, 0, 1440, 1, 0, "분", width=112)
    def _on_rep(v: float) -> None:
        new = int(v)
        if alarm.repeat_minutes != new:
            alarm.repeat_minutes = new
            bus.settings_dirty.emit()
            bus.alarm_list_changed.emit()
    rep.valueChanged.connect(_on_rep)
    head.addWidget(rep)

    del_btn = IconOnlyButton("trash-2", size="md", tooltip="삭제")
    del_btn.clicked.connect(lambda: on_delete(alarm))
    head.addWidget(del_btn)
    card.add(head)

    # ── Row 2: weekday toggles ──
    day_row = QHBoxLayout(); day_row.setSpacing(4); day_row.setContentsMargins(48, 0, 0, 0)
    day_lbl = QLabel("요일")
    reactive(day_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    day_row.addWidget(day_lbl); day_row.addSpacing(4)

    def _day_qss() -> str:
        p = T.palette
        return (
            f"QPushButton {{ background:{p.bg_input}; color:{p.text_secondary};"
            f" border:1px solid {p.border_default}; border-radius:6px;"
            f" font-size:12px; font-weight:600; padding:0; }}"
            f"QPushButton:hover {{ background:{p.bg_hover}; color:{p.text_primary}; }}"
            f"QPushButton:checked {{ background:{p.accent_default}; color:{p.text_inverse};"
            f" border-color:{p.accent_default}; }}"
        )

    day_buttons: list[QPushButton] = []
    for i, name_d in enumerate(DAYS):
        b = QPushButton(name_d); b.setCheckable(True); b.setFixedSize(34, 28)
        # Empty `days` = every day → all checked.
        b.setChecked((not alarm.days) or (i in alarm.days))
        reactive(b, _day_qss)

        def _on_day(_checked: bool, _i=i) -> None:
            new_days: list[int] = sorted([
                idx for idx, btn in enumerate(day_buttons) if btn.isChecked()
            ])
            # All-7 collapses to empty list (= "every day") — matches HTML behaviour.
            if len(new_days) == 7:
                new_days = []
            if alarm.days != new_days:
                alarm.days = new_days
                bus.settings_dirty.emit()
        b.toggled.connect(_on_day)
        day_buttons.append(b)
        day_row.addWidget(b)
    day_row.addStretch(1)
    card.add(day_row)
    return card


def make_alerts() -> tuple[QWidget, QWidget]:
    # ── Sidebar: alarm list summary ──
    sidebar = QWidget()
    sv = QVBoxLayout(sidebar); sv.setContentsMargins(8, 6, 8, 8); sv.setSpacing(2)
    head = QHBoxLayout(); head.setContentsMargins(8, 0, 4, 0)
    title = QLabel(f"알람 ({len(mock_settings.alarms)})")
    reactive(title, lambda: f"color:{T.palette.text_secondary}; padding:6px 0;")
    add_sb_btn = IconOnlyButton("plus", size="sm", tooltip="새 알람 추가")
    head.addWidget(title); head.addStretch(1); head.addWidget(add_sb_btn)
    sv.addLayout(head)

    # Sidebar list lives in its own container so we can wipe + repaint
    # whenever the user adds / deletes / edits an alarm. Without this
    # the left list freezes at boot-time state and the right-hand
    # detail edits don't propagate back to the sidebar (label / time).
    sb_list = QWidget()
    sb_list_lay = QVBoxLayout(sb_list)
    sb_list_lay.setContentsMargins(0, 0, 0, 0); sb_list_lay.setSpacing(2)
    sv.addWidget(sb_list)
    sv.addStretch(1)

    def _rebuild_sidebar_list() -> None:
        # Update the count in the header.
        title.setText(f"알람 ({len(mock_settings.alarms)})")
        # Wipe existing rows.
        while sb_list_lay.count():
            item = sb_list_lay.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for i, al in enumerate(mock_settings.alarms):
            sb_list_lay.addWidget(_AlarmSidebarRow(
                al.label, f"{al.hour:02d}:{al.minute:02d}", selected=(i == 0)
            ))

    _rebuild_sidebar_list()
    bus.alarm_list_changed.connect(_rebuild_sidebar_list)

    # ── Main ──
    from PySide6.QtWidgets import QScrollArea, QFrame
    main = QScrollArea()
    main.setWidgetResizable(True)
    main.setFrameShape(QFrame.NoFrame)
    main.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    main.setWidget(inner)
    v = QVBoxLayout(inner); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(14)

    head_row = QHBoxLayout(); head_row.setSpacing(10)
    title = QLabel("알람")
    f = QFont(); f.setBold(True); f.setPointSize(18); title.setFont(f)
    sub = QLabel("Windows 토스트 + Telegram + 인앱 팝업으로 동시 알림")
    reactive(sub, lambda: f"color:{T.palette.text_secondary};")
    head_box = QVBoxLayout(); head_box.setContentsMargins(0, 0, 0, 0); head_box.setSpacing(0)
    head_box.addWidget(title); head_box.addWidget(sub)
    head_row.addLayout(head_box); head_row.addStretch(1)
    add_btn = IconButton("새 알람 추가", "plus", variant="primary")
    head_row.addWidget(add_btn)
    v.addLayout(head_row)

    # Global options — single-row condensed layout
    opts = Card("팝업 동작")
    o = QVBoxLayout(); o.setContentsMargins(0, 0, 0, 0); o.setSpacing(8)
    pop = QCheckBox("Windows 알림 + 인앱 토스트 표시 (사운드 포함)")
    pop.setChecked(bool(mock_settings.alarm_popup_enabled))
    def _on_pop(on: bool) -> None:
        if mock_settings.alarm_popup_enabled != on:
            mock_settings.alarm_popup_enabled = on
            bus.settings_dirty.emit()
    pop.toggled.connect(_on_pop)
    o.addWidget(pop)

    # Single row: 자동 닫기 / 재알림 간격 / 사운드 / 미리듣기 / 볼륨
    main_widget_ref: dict = {"w": main}
    row = QHBoxLayout(); row.setSpacing(8)

    ac_lbl = QLabel("반복 종료"); reactive(ac_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    row.addWidget(ac_lbl)
    auto_close = Stepper(mock_settings.alarm_auto_close_minutes, 1, 60, 1, 0, "분", width=84)
    def _on_ac(v: float) -> None:
        new = int(v)
        if mock_settings.alarm_auto_close_minutes != new:
            mock_settings.alarm_auto_close_minutes = new
            bus.settings_dirty.emit()
    auto_close.valueChanged.connect(_on_ac)
    row.addWidget(auto_close); row.addSpacing(12)

    rp_lbl = QLabel("재알림"); reactive(rp_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    row.addWidget(rp_lbl)
    repeat = Stepper(mock_settings.alarm_repeat_minutes, 0, 60, 1, 0, "분", width=84)
    def _on_rp(v: float) -> None:
        new = int(v)
        if mock_settings.alarm_repeat_minutes != new:
            mock_settings.alarm_repeat_minutes = new
            bus.settings_dirty.emit()
    repeat.valueChanged.connect(_on_rp)
    row.addWidget(repeat); row.addSpacing(12)

    sound_lbl = QLabel("사운드"); reactive(sound_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    row.addWidget(sound_lbl)
    sound_cb = QComboBox()
    # Pull built-in presets from notify.sound so a single source of
    # truth drives both the UI list and what's playable. The trailing
    # "사용자 파일…" entry triggers a QFileDialog for custom .wav.
    from quickcast.notify.sound import SOUND_PRESETS as _PRESETS
    _PRESET_IDS = [sid for sid, _ in _PRESETS]
    for _, label in _PRESETS:
        sound_cb.addItem(label)
    sound_cb.addItem("사용자 파일…")
    _CUSTOM_IDX = sound_cb.count() - 1
    cur_sound = mock_settings.alarm_sound or "default"
    if cur_sound in _PRESET_IDS:
        sound_cb.setCurrentIndex(_PRESET_IDS.index(cur_sound))
    else:
        sound_cb.setCurrentIndex(_CUSTOM_IDX)
    sound_path_lbl = QLabel(cur_sound if cur_sound not in _PRESET_IDS else "")
    reactive(sound_path_lbl, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")

    def _pick_file() -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            main_widget_ref["w"], "사운드 파일 선택", "", "WAV files (*.wav)",
        )
        if path:
            mock_settings.alarm_sound = path
            sound_path_lbl.setText(path)
            bus.settings_dirty.emit()
        else:
            # Cancel — revert combo to current setting
            cur = mock_settings.alarm_sound
            if cur in _PRESET_IDS:
                sound_cb.setCurrentIndex(_PRESET_IDS.index(cur))
            else:
                sound_cb.setCurrentIndex(_CUSTOM_IDX)

    def _on_sound(idx: int) -> None:
        if 0 <= idx < len(_PRESET_IDS):
            new = _PRESET_IDS[idx]
        else:
            # Custom file slot — keep existing custom path or prompt.
            cur = mock_settings.alarm_sound
            if cur and cur not in _PRESET_IDS:
                new = cur
            else:
                _pick_file()
                return
        if mock_settings.alarm_sound != new:
            mock_settings.alarm_sound = new
            sound_path_lbl.setText(new if new not in _PRESET_IDS else "")
            bus.settings_dirty.emit()
    sound_cb.currentIndexChanged.connect(_on_sound)
    row.addWidget(sound_cb)

    test_btn = IconButton("미리듣기", "play", size="sm")
    def _do_test() -> None:
        from quickcast.notify.sound import play_once
        play_once(mock_settings.alarm_sound or "default",
                  mock_settings.alarm_sound_volume)
    test_btn.clicked.connect(_do_test)
    row.addWidget(test_btn); row.addSpacing(12)

    vol_lbl = QLabel("볼륨"); reactive(vol_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    row.addWidget(vol_lbl)
    vol = Stepper(mock_settings.alarm_sound_volume, 0, 100, 5, 0, "%", width=92)
    def _on_vol(vv: float) -> None:
        new = int(vv)
        if mock_settings.alarm_sound_volume != new:
            mock_settings.alarm_sound_volume = new
            bus.settings_dirty.emit()
    vol.valueChanged.connect(_on_vol)
    row.addWidget(vol); row.addStretch(1)
    o.addLayout(row)

    # Custom file path indicator (separate line — only shows when custom)
    o.addWidget(sound_path_lbl)
    opts.add(o)
    v.addWidget(opts)

    # Alarm rows
    list_wrap = QVBoxLayout(); list_wrap.setSpacing(10)
    main._alarm_list_layout = list_wrap   # for add/delete handlers

    def _delete_alarm(target: Alarm) -> None:
        try:
            mock_settings.alarms.remove(target)
        except ValueError:
            return
        # alarm_state migration
        alarm_state._on.pop(target.label, None)
        bus.settings_dirty.emit()
        bus.alarm_list_changed.emit()
        _rebuild_list()

    def _rebuild_list() -> None:
        # Clear children
        while list_wrap.count():
            it = list_wrap.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        if not mock_settings.alarms:
            empty = EmptyState(
                icon="bell",
                title="등록된 알람이 없습니다",
                hint="아래 ‘새 알람 추가’ 버튼으로 시간/요일/모드를 설정하세요.",
                cta_text="새 알람 추가",
                cta_icon="plus",
                on_cta=lambda: _add_alarm(),
            )
            list_wrap.addWidget(empty)
            return
        for al in mock_settings.alarms:
            list_wrap.addWidget(_bind_alarm_row(al, _delete_alarm))

    _rebuild_list()
    v.addLayout(list_wrap)

    def _add_alarm() -> None:
        new_label = f"새 알람 {len(mock_settings.alarms) + 1}"
        al = Alarm(
            id=str(uuid.uuid4())[:8],
            label=new_label,
            hour=20, minute=0, enabled=True,
            repeat_minutes=0, days=[], mode="repeat",
        )
        mock_settings.alarms.append(al)
        alarm_state._on[new_label] = True
        bus.settings_dirty.emit()
        bus.alarm_list_changed.emit()
        _rebuild_list()

    add_btn.clicked.connect(_add_alarm)
    add_sb_btn.clicked.connect(_add_alarm)

    v.addStretch(1)
    return sidebar, main


__all__ = ["make_alerts"]
