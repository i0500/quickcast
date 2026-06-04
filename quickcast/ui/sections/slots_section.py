"""Skill Slots — sidebar list (master) + main editor (detail).

All controls bind two-way to `mock_settings.slots[sid]`. Add/delete
mutates the dict and emits `bus.slot_list_changed` so the dashboard
sidebar rebuilds. Edits emit `bus.settings_dirty` so the host saves.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from quickcast.config import Slot as SlotCfg
from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconButton, IconOnlyButton
from quickcast.ui.components.key_capture_dialog import KeyCaptureDialog
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from PySide6.QtWidgets import QSlider

from quickcast.ui.ios_toggle import IOSToggle
from quickcast.ui.range_slider import RangeSlider
from quickcast.ui.sections._mock_state import mock_settings, slot_state
from quickcast.ui.stepper import Stepper


def _next_slot_id() -> str:
    """Pick a fresh slot id — pad past the 10 default ids (1-9, 0) at 11+."""
    used = set(mock_settings.slots.keys())
    candidate = 11
    while str(candidate) in used:
        candidate += 1
    return str(candidate)


def _ensure_default_slots() -> None:
    """Make sure 1..9, 0 exist as Slot entries — derived from slot_state seed."""
    for sid in slot_state.order():
        if sid not in mock_settings.slots:
            mock_settings.slots[sid] = SlotCfg(
                label=slot_state.label(sid),
                use=slot_state.is_on(sid),
                key=slot_state.key(sid),
            )


class _SlotListRow(QWidget):
    """Sidebar row — clickable, syncs with global slot_state, theme-reactive."""

    clicked = Signal(str)

    def __init__(self, slot_id: str, selected: bool) -> None:
        super().__init__()
        self.slot_id = slot_id
        self._selected = selected
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)

        h = QHBoxLayout(self); h.setContentsMargins(10, 6, 10, 6); h.setSpacing(8)

        self.sw = IOSToggle(width=36, height=18)
        self.sw.set_state(slot_state.is_on(slot_id), animate=False)
        self.sw.toggled.connect(self._on_local_toggle)
        h.addWidget(self.sw)

        self.id_lbl = QLabel(f"#{slot_id}"); self.id_lbl.setMinimumWidth(22)
        h.addWidget(self.id_lbl)

        self.name_lbl = QLabel(slot_state.label(slot_id))
        h.addWidget(self.name_lbl); h.addStretch(1)

        self.kbd = QLabel(slot_state.key(slot_id))
        h.addWidget(self.kbd)

        bus.theme_changed.connect(self._restyle)
        slot_state.slot_toggled.connect(self._on_global_toggle)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        on = slot_state.is_on(self.slot_id)
        self.id_lbl.setStyleSheet(f"color:{p.text_tertiary}; font-family:{T.type.mono};")
        self.name_lbl.setStyleSheet(
            f"color:{p.text_primary if on else p.text_tertiary};"
        )
        self.kbd.setStyleSheet(
            f"background:{p.bg_input}; color:{p.text_secondary};"
            f" border:1px solid {p.border_default};"
            f" border-radius:4px; padding:1px 6px;"
            f" font-family:{T.type.mono}; font-size:10px;"
        )
        if self._selected:
            self.setStyleSheet(
                f"_SlotListRow {{ background:{p.accent_subtle}; border-radius:6px; }}"
            )
        else:
            self.setStyleSheet(
                f"_SlotListRow:hover {{ background:{p.bg_hover}; border-radius:6px; }}"
            )

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        self._restyle()

    def refresh_text(self) -> None:
        """External code (editor) calls this when label/key changed."""
        self.name_lbl.setText(slot_state.label(self.slot_id))
        self.kbd.setText(slot_state.key(self.slot_id))

    def _on_local_toggle(self, on: bool) -> None:
        slot_state.set_on(self.slot_id, on)

    def _on_global_toggle(self, sid: str, on: bool) -> None:
        if sid != self.slot_id:
            return
        if self.sw.is_on() != on:
            self.sw.set_state(on, animate=True)
        self._restyle()

    def mouseReleaseEvent(self, e) -> None:
        if not self.sw.geometry().contains(e.position().toPoint()):
            self.clicked.emit(self.slot_id)
        super().mouseReleaseEvent(e)


def _bind_range(target_obj, lo_attr: str, hi_attr: str, color: str) -> QHBoxLayout:
    row = QHBoxLayout(); row.setSpacing(8)
    lo, hi = int(getattr(target_obj, lo_attr)), int(getattr(target_obj, hi_attr))
    mn = QLineEdit(f"{lo}%"); mn.setFixedSize(64, 30); mn.setAlignment(Qt.AlignCenter)
    mx = QLineEdit(f"{hi}%"); mx.setFixedSize(64, 30); mx.setAlignment(Qt.AlignCenter)
    sep = QLabel("~"); sep.setFixedWidth(10); sep.setAlignment(Qt.AlignCenter)
    sl = RangeSlider(0, 100, lo, hi, fill_color=color); sl.setMinimumWidth(120)
    row.addWidget(mn); row.addWidget(sep); row.addWidget(mx); row.addWidget(sl, stretch=1)

    def _commit(lo_v: int, hi_v: int) -> None:
        setattr(target_obj, lo_attr, lo_v)
        setattr(target_obj, hi_attr, hi_v)
        bus.settings_dirty.emit()

    def _on_slider(lo_v: int, hi_v: int) -> None:
        mn.blockSignals(True); mx.blockSignals(True)
        mn.setText(f"{lo_v}%"); mx.setText(f"{hi_v}%")
        mn.blockSignals(False); mx.blockSignals(False)
        _commit(lo_v, hi_v)

    def _on_text() -> None:
        try:
            lo_v = int(mn.text().rstrip("%").strip() or 0)
            hi_v = int(mx.text().rstrip("%").strip() or 100)
        except ValueError:
            return
        sl.set_values(lo_v, hi_v)
        _commit(lo_v, hi_v)

    sl.rangeChanged.connect(_on_slider)
    mn.editingFinished.connect(_on_text)
    mx.editingFinished.connect(_on_text)
    return row


def _bind_slot_sustain(slot) -> QHBoxLayout:
    """슬롯 인식 유지 시간 — 토글 + 1~5초 슬라이더.

    HP/MP 행과 좌측 라벨/슬라이더 정렬을 맞춘다 (라벨 28px + 토글 + min/max
    너비만큼의 스페이서 + 슬라이더 stretch + 우측 값 라벨). 토글이 꺼져
    있으면 Qt 기본 disabled 처리에 맡겨 슬라이더를 회색으로 흐리게 한다.
    켜두면 HP/MP 범위가 슬라이더 값(초) 동안 연속 만족돼야 발동.
    """
    row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)

    # HP/MP 라벨과 동일 폭(28px) — "인식" 두 글자만 라벨에 두고 토글이
    # 텍스트를 가리지 않도록 토글은 라벨 바깥에 위치.
    lbl = QLabel("인식"); lbl.setFixedWidth(28)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-weight:bold;")
    row.addWidget(lbl)

    tg = IOSToggle(width=36, height=18)
    tg.set_state(bool(getattr(slot, "sustain_enabled", False)), animate=False)
    row.addWidget(tg)

    # HP/MP 행은 [min 64] [~ 10] [max 64] 가 슬라이더 앞에 들어가므로
    # 같은 폭(64+10+64 + 간격 보정)으로 스페이서를 두어 슬라이더 시작
    # 위치를 정렬한다. 우측 값 라벨 폭도 HP/MP 행의 % 입력 + 슬라이더
    # 우측과 시각적으로 비슷한 64px.
    spacer = QLabel("")
    spacer.setFixedWidth(64 + 10 + 64 - 36 - 8)   # 토글 폭과 간격 차감
    row.addWidget(spacer)

    # 1~5초, 0.1초 단위 (10..50)
    cur_sec = float(getattr(slot, "sustain_seconds", 3.0) or 3.0)
    cur_sec = max(1.0, min(5.0, cur_sec))
    cur_tenths = int(round(cur_sec * 10))
    sl = QSlider(Qt.Horizontal)
    sl.setRange(10, 50)
    sl.setValue(cur_tenths)
    sl.setMinimumWidth(120)
    sl.setSingleStep(1); sl.setPageStep(5)
    sl.setTickInterval(10); sl.setTickPosition(QSlider.NoTicks)
    sl.setEnabled(bool(getattr(slot, "sustain_enabled", False)))

    val_lbl = QLabel(f"{cur_sec:.1f}초")
    val_lbl.setFixedWidth(48); val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    reactive(val_lbl, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono}; font-size:11px;")

    def _on_tg(on: bool) -> None:
        if bool(getattr(slot, "sustain_enabled", False)) != on:
            slot.sustain_enabled = on
            bus.settings_dirty.emit()
        sl.setEnabled(on)
    tg.toggled.connect(_on_tg)

    def _on_sl(tenths: int) -> None:
        secs = round(tenths / 10.0, 1)
        val_lbl.setText(f"{secs:.1f}초")
        if float(getattr(slot, "sustain_seconds", 0.0)) != secs:
            slot.sustain_seconds = secs
            bus.settings_dirty.emit()
    sl.valueChanged.connect(_on_sl)

    # PK/물약 인식 유지 슬라이더와 동일 QSS — 일관성 유지.
    def _qss() -> str:
        p = T.palette
        return (
            f"QSlider::groove:horizontal {{ background:{p.bg_input};"
            f" border-radius:3px; height:6px; }}"
            f"QSlider::sub-page:horizontal {{ background:{p.accent_default};"
            f" border-radius:3px; }}"
            f"QSlider::handle:horizontal {{ background:{p.accent_default};"
            f" border:2px solid {p.bg_canvas}; width:14px; height:14px;"
            f" margin:-5px 0; border-radius:8px; }}"
            f"QSlider::handle:horizontal:hover {{ background:{p.accent_hover}; }}"
        )
    reactive(sl, _qss)

    row.addWidget(sl, stretch=1)
    row.addWidget(val_lbl)
    return row


def _build_editor(sid: str, on_label_or_key_change) -> QWidget:
    """Detail editor for slot `sid`. All controls bind to `mock_settings.slots[sid]`."""
    slot = mock_settings.slots[sid]
    w = QWidget()
    v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)

    # Identity row
    id_card = Card("")
    head = QHBoxLayout(); head.setSpacing(10)
    id_lbl = QLabel(f"슬롯 #{sid}")
    f = QFont(); f.setBold(True); f.setPointSize(13); id_lbl.setFont(f)
    head.addWidget(id_lbl)
    name_edit = QLineEdit(slot.label); name_edit.setMinimumWidth(220)
    def _on_name() -> None:
        new = name_edit.text().strip()
        if not new or new == slot.label:
            return
        slot.label = new
        slot_state._label[sid] = new
        slot_state.slot_toggled.emit(sid, slot.use)  # forces sidebar re-restyle
        bus.settings_dirty.emit()
        on_label_or_key_change(sid)
    name_edit.editingFinished.connect(_on_name)
    head.addWidget(name_edit, stretch=1)

    use_lbl = QLabel("활성")
    reactive(use_lbl, lambda: f"color:{T.palette.text_secondary};")
    use = IOSToggle(width=44, height=22)
    use.set_state(bool(slot.use), animate=False)
    def _on_use(on: bool) -> None:
        if slot.use != on:
            slot.use = on
            slot_state.set_on(sid, on)   # mirror to sidebar/dashboard
            bus.settings_dirty.emit()
    use.toggled.connect(_on_use)
    # Listen for sidebar/dashboard changes so the editor toggle stays
    # in sync if the user flips it from elsewhere.
    def _on_external_toggle(_sid: str, on: bool, _sw=use) -> None:
        if _sid != sid:
            return
        if _sw.is_on() != on:
            _sw.set_state(on, animate=True)
    slot_state.slot_toggled.connect(_on_external_toggle)
    head.addWidget(use_lbl); head.addWidget(use)
    id_card.add(head)
    v.addWidget(id_card)

    # Action row card
    actions = Card("동작")
    a = QHBoxLayout(); a.setSpacing(10)
    key_lbl = QLabel("키"); reactive(key_lbl, lambda: f"color:{T.palette.text_secondary};")
    a.addWidget(key_lbl)
    key_btn = QPushButton(slot.key or "0"); key_btn.setFixedSize(48, 30)
    kf = QFont(); kf.setBold(True); kf.setPointSize(12); key_btn.setFont(kf)
    reactive(key_btn, lambda: (
        f"QPushButton {{ background:{T.palette.bg_input};"
        f" color:{T.palette.accent_default};"
        f" border:1px solid {T.palette.border_default};"
        f" border-radius:6px; }}"
        f"QPushButton:hover {{ background:{T.palette.bg_hover}; }}"
    ))
    def _on_key() -> None:
        result = KeyCaptureDialog.get_key(key_btn, current=key_btn.text())
        if result and result != slot.key:
            key_btn.setText(result)
            slot.key = result
            slot_state._key[sid] = result
            slot_state.slot_toggled.emit(sid, slot.use)
            bus.settings_dirty.emit()
            on_label_or_key_change(sid)
    key_btn.clicked.connect(_on_key)
    a.addWidget(key_btn)

    cnt_lbl = QLabel("연사"); reactive(cnt_lbl, lambda: f"color:{T.palette.text_secondary};")
    a.addWidget(cnt_lbl)
    cnt = Stepper(slot.count, 1, 99, 1, 0, "회", width=112)
    def _on_cnt(vv: float) -> None:
        new = int(vv)
        if slot.count != new:
            slot.count = new; bus.settings_dirty.emit()
    cnt.valueChanged.connect(_on_cnt); a.addWidget(cnt)

    int_lbl = QLabel("간격"); reactive(int_lbl, lambda: f"color:{T.palette.text_secondary};")
    a.addWidget(int_lbl)
    interval = Stepper(slot.delay, 0, 10, 0.1, 2, "초", width=140)
    def _on_int(vv: float) -> None:
        if slot.delay != vv:
            slot.delay = vv; bus.settings_dirty.emit()
    interval.valueChanged.connect(_on_int); a.addWidget(interval)

    cd_lbl = QLabel("쿨타임"); reactive(cd_lbl, lambda: f"color:{T.palette.text_secondary};")
    a.addWidget(cd_lbl)
    # Up to 24h — long-cooldown skills (e.g. 3-hour 광폭) need this range.
    cooldown = Stepper(slot.cooltime, 0, 86400, 1.0, 0, "초", width=140)
    def _on_cd(vv: float) -> None:
        if slot.cooltime != vv:
            slot.cooltime = vv; bus.settings_dirty.emit()
    cooldown.valueChanged.connect(_on_cd); a.addWidget(cooldown)

    a.addStretch(1)
    actions.add(a)

    flags = QHBoxLayout(); flags.setSpacing(20)
    rep = QCheckBox("반복 발동"); rep.setChecked(bool(slot.repeat))
    def _on_rep(on: bool) -> None:
        if slot.repeat != on:
            slot.repeat = on; bus.settings_dirty.emit()
    rep.toggled.connect(_on_rep)
    tg = QCheckBox("발동 시 텔레그램 알림"); tg.setChecked(bool(slot.tele_use))
    def _on_tg(on: bool) -> None:
        if slot.tele_use != on:
            slot.tele_use = on; bus.settings_dirty.emit()
    tg.toggled.connect(_on_tg)
    flags.addWidget(rep); flags.addWidget(tg); flags.addStretch(1)
    actions.add(flags)
    v.addWidget(actions)

    # Conditions card — HP + MP ranges
    cond = Card("발동 조건",
                subtitle="HP/MP 두 범위가 모두 만족돼야 발동")
    hp_row = QHBoxLayout(); hp_row.setSpacing(8)
    hp_lbl = QLabel("HP"); hp_lbl.setFixedWidth(28)
    reactive(hp_lbl, lambda: f"color:{T.palette.text_secondary}; font-weight:bold;")
    hp_row.addWidget(hp_lbl)
    hp_row.addLayout(_bind_range(slot.hp, "min", "max", T.palette.hp_fill))
    cond.add(hp_row)
    mp_row = QHBoxLayout(); mp_row.setSpacing(8)
    mp_lbl = QLabel("MP"); mp_lbl.setFixedWidth(28)
    reactive(mp_lbl, lambda: f"color:{T.palette.text_secondary}; font-weight:bold;")
    mp_row.addWidget(mp_lbl)
    mp_row.addLayout(_bind_range(slot.mp, "min", "max", T.palette.mp_fill))
    cond.add(mp_row)
    # 인식 유지 시간 — HP/MP 두 범위가 N초 동안 연속으로 만족돼야 발동.
    # 토글로 활성화하고, 활성화 시 1~5초 슬라이더로 조절. 기본 OFF.
    cond.add(_bind_slot_sustain(slot))
    v.addWidget(cond)

    v.addStretch(1)
    return w


def make_slots() -> tuple[QWidget, QWidget]:
    _ensure_default_slots()
    ids = list(mock_settings.slots.keys()) or slot_state.order()

    # ── Sidebar ──
    sidebar = QWidget()
    sv = QVBoxLayout(sidebar); sv.setContentsMargins(8, 6, 8, 8); sv.setSpacing(2)
    head = QHBoxLayout(); head.setContentsMargins(8, 0, 4, 0)
    title = QLabel(f"슬롯 ({len(ids)})")
    reactive(title, lambda: f"color:{T.palette.text_secondary}; padding:6px 0;")
    add_sb_btn = IconOnlyButton("plus", size="sm", tooltip="새 슬롯 추가")
    head.addWidget(title); head.addStretch(1); head.addWidget(add_sb_btn)
    sv.addLayout(head)

    rows: list[_SlotListRow] = []
    list_layout = QVBoxLayout(); list_layout.setSpacing(2); list_layout.setContentsMargins(0, 0, 0, 0)
    sv.addLayout(list_layout)
    sv.addStretch(1)

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
    title_lbl = QLabel("스킬 슬롯")
    f = QFont(); f.setBold(True); f.setPointSize(18); title_lbl.setFont(f)
    sub = QLabel("좌측 리스트에서 슬롯을 선택하면 여기에 편집 화면이 뜹니다.")
    reactive(sub, lambda: f"color:{T.palette.text_secondary};")
    head_box = QVBoxLayout(); head_box.setContentsMargins(0, 0, 0, 0); head_box.setSpacing(0)
    head_box.addWidget(title_lbl); head_box.addWidget(sub)
    head_row.addLayout(head_box); head_row.addStretch(1)
    add_main_btn = IconButton("새 슬롯 추가", "plus", variant="primary")
    head_row.addWidget(add_main_btn)
    del_btn = IconButton("선택 슬롯 삭제", "trash-2")
    head_row.addWidget(del_btn)
    v.addLayout(head_row)

    stack = QStackedWidget()
    editors: dict[str, QWidget] = {}
    v.addWidget(stack)
    v.addStretch(1)

    selected = {"sid": ids[0] if ids else ""}

    def _on_label_or_key_change(sid: str) -> None:
        # Update the row's text + the title bar.
        for r in rows:
            if r.slot_id == sid:
                r.refresh_text()
        if sid == selected["sid"]:
            title_lbl.setText(f"스킬 슬롯 — #{sid} {slot_state.label(sid)}")

    def _select(sid: str) -> None:
        if sid not in editors:
            return
        stack.setCurrentWidget(editors[sid])
        title_lbl.setText(f"스킬 슬롯 — #{sid} {slot_state.label(sid)}")
        selected["sid"] = sid
        for r in rows:
            r.set_selected(r.slot_id == sid)

    def _rebuild() -> None:
        # Tear down existing rows + editors
        while list_layout.count():
            it = list_layout.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        rows.clear()
        for sid in list(editors.keys()):
            editors[sid].setParent(None)
            editors[sid].deleteLater()
        editors.clear()

        cur_ids = list(mock_settings.slots.keys()) or slot_state.order()
        title.setText(f"슬롯 ({len(cur_ids)})")
        if not cur_ids:
            return
        if selected["sid"] not in cur_ids:
            selected["sid"] = cur_ids[0]

        for sid in cur_ids:
            r = _SlotListRow(sid, selected=(sid == selected["sid"]))
            r.clicked.connect(_select)
            rows.append(r); list_layout.addWidget(r)
            ed = _build_editor(sid, _on_label_or_key_change)
            editors[sid] = ed; stack.addWidget(ed)

        _select(selected["sid"])

    _rebuild()
    # Multi-client: when AppWindow swaps active client, the top-level
    # mock_settings.slots dict is replaced wholesale. Without this
    # subscription the slot rows would stay frozen on the previous tab's
    # data even though add/delete buttons emit the same signal.
    bus.slot_list_changed.connect(_rebuild)

    def _add_slot() -> None:
        sid = _next_slot_id()
        new_slot = SlotCfg(label=f"SLOT-{sid}", key="0")
        mock_settings.slots[sid] = new_slot
        slot_state._on[sid] = False
        slot_state._label[sid] = new_slot.label
        slot_state._key[sid] = new_slot.key
        bus.settings_dirty.emit(); bus.slot_list_changed.emit()
        selected["sid"] = sid
        _rebuild()

    def _delete_selected() -> None:
        sid = selected["sid"]
        if not sid or sid not in mock_settings.slots:
            return
        # Don't allow deleting the last slot — keep at least one for usability.
        if len(mock_settings.slots) <= 1:
            return
        del mock_settings.slots[sid]
        slot_state._on.pop(sid, None)
        slot_state._label.pop(sid, None)
        slot_state._key.pop(sid, None)
        bus.settings_dirty.emit(); bus.slot_list_changed.emit()
        _rebuild()

    add_sb_btn.clicked.connect(_add_slot)
    add_main_btn.clicked.connect(_add_slot)
    del_btn.clicked.connect(_delete_selected)

    return sidebar, main


__all__ = ["make_slots"]
