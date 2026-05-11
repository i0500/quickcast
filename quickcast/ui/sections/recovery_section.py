"""복귀 — town-return recovery sequence.

Lets the user define a list of click points (in game-window coords) that
the controller will run after a forced return event (potion empty / PK /
HP zero). The sequence waits `start_delay_seconds` first so the auto-
return animation can finish, then clicks each step with its own
`delay_after_ms` between them.

Position picking: clicking "위치 지정" arms the dashboard preview's
InteractivePreview into "pick" mode — the next click on the preview
captures (x, y) into the step.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from quickcast.config import RecoveryStep
from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconButton, IconOnlyButton
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.ios_toggle import IOSToggle
from quickcast.ui.sections._mock_state import mock_settings
from quickcast.ui.stepper import Stepper


def _bind_check(target_obj, attr: str, label: str) -> QCheckBox:
    cb = QCheckBox(label)
    cb.setChecked(bool(getattr(target_obj, attr)))
    def _on(on: bool) -> None:
        if getattr(target_obj, attr) != on:
            setattr(target_obj, attr, on)
            bus.settings_dirty.emit()
    cb.toggled.connect(_on)
    return cb


def _bind_toggle_pair(target_obj, attr: str, label: str) -> QHBoxLayout:
    """[label] [iOS toggle] horizontal pair, bound to bool attribute."""
    row = QHBoxLayout(); row.setSpacing(6); row.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    sw = IOSToggle(width=36, height=18)
    sw.set_state(bool(getattr(target_obj, attr)), animate=False)
    def _on(on: bool) -> None:
        if getattr(target_obj, attr) != on:
            setattr(target_obj, attr, on)
            bus.settings_dirty.emit()
    sw.toggled.connect(_on)
    row.addWidget(lbl); row.addWidget(sw)
    return row


def _build_step_row(step: RecoveryStep, idx: int, on_delete, on_pick) -> Card:
    """Card representing one recovery step — label / x / y / delay / pick / delete."""
    card = Card("")
    card.body.setContentsMargins(12, 8, 12, 8)
    card.body.setSpacing(8)
    row = QHBoxLayout(); row.setSpacing(8)

    n_lbl = QLabel(f"{idx + 1}")
    f = QFont(); f.setBold(True); f.setPointSize(13); n_lbl.setFont(f)
    n_lbl.setMinimumWidth(20)
    reactive(n_lbl, lambda: f"color:{T.palette.accent_default};")
    row.addWidget(n_lbl)

    name = QLineEdit(step.label or f"단계 {idx + 1}")
    name.setMinimumWidth(120); name.setFixedHeight(28)
    name.setPlaceholderText("이름 (예: 메뉴, 던전, 입장)")
    def _on_name() -> None:
        if step.label != name.text():
            step.label = name.text()
            bus.settings_dirty.emit()
    name.editingFinished.connect(_on_name)
    row.addWidget(name, stretch=1)

    x_lbl = QLabel("X"); reactive(x_lbl, lambda: f"color:{T.palette.text_tertiary};")
    row.addWidget(x_lbl)
    x_in = Stepper(step.x, 0, 4096, 1, 0, "", width=84)
    def _on_x(v: float) -> None:
        new = int(v)
        if step.x != new:
            step.x = new; bus.settings_dirty.emit()
    x_in.valueChanged.connect(_on_x); row.addWidget(x_in)

    y_lbl = QLabel("Y"); reactive(y_lbl, lambda: f"color:{T.palette.text_tertiary};")
    row.addWidget(y_lbl)
    y_in = Stepper(step.y, 0, 4096, 1, 0, "", width=84)
    def _on_y(v: float) -> None:
        new = int(v)
        if step.y != new:
            step.y = new; bus.settings_dirty.emit()
    y_in.valueChanged.connect(_on_y); row.addWidget(y_in)

    # Key input — KeyCaptureDialog (same UX as slot keys). When a key
    # is set, the step uses send_key instead of click_at, and the
    # X/Y/pick controls grey out so it's obvious which mode applies.
    from quickcast.ui.components.key_capture_dialog import KeyCaptureDialog
    key_lbl = QLabel("키"); reactive(key_lbl, lambda: f"color:{T.palette.text_tertiary};")
    row.addWidget(key_lbl)
    key_btn = QPushButton(step.key or "—")
    key_btn.setFixedSize(64, 28)
    key_btn.setToolTip(
        "클릭 후 아무 키 누르기 (esc/enter/1/F1 등). 우클릭으로 해제.\n"
        "키가 설정되면 좌표 클릭 대신 키 입력으로 동작."
    )
    def _key_qss() -> str:
        p = T.palette
        col = p.accent_default if step.key else p.text_tertiary
        return (
            f"QPushButton {{ background:{p.bg_input}; color:{col};"
            f" border:1px solid {p.border_default}; border-radius:6px;"
            f" font-family:{T.type.mono}; font-weight:600; }}"
            f"QPushButton:hover {{ background:{p.bg_hover};"
            f" border-color:{p.border_focus}; }}"
        )
    reactive(key_btn, _key_qss)
    row.addWidget(key_btn)

    d_lbl = QLabel("후 대기"); reactive(d_lbl, lambda: f"color:{T.palette.text_tertiary};")
    row.addWidget(d_lbl)
    d_in = Stepper(step.delay_after_ms / 1000.0, 0.0, 60.0, 0.1, 1, "초", width=140)
    def _on_d(v: float) -> None:
        new = int(round(v * 1000))
        if step.delay_after_ms != new:
            step.delay_after_ms = new; bus.settings_dirty.emit()
    d_in.valueChanged.connect(_on_d); row.addWidget(d_in)

    pick_btn = IconButton("위치 지정", "target", size="sm")
    pick_btn.setToolTip("좌표 클릭 단계용 — 키 입력 단계는 비활성")
    pick_btn.clicked.connect(lambda: on_pick(step, idx, name, x_in, y_in))
    pick_btn.setEnabled(not bool(step.key))
    row.addWidget(pick_btn)

    def _apply_mode() -> None:
        is_key = bool(step.key)
        x_in.setEnabled(not is_key); y_in.setEnabled(not is_key)
        pick_btn.setEnabled(not is_key)
        key_btn.setText(step.key or "—")
        key_btn.setStyleSheet(_key_qss())

    def _on_key_btn() -> None:
        result = KeyCaptureDialog.get_key(key_btn, current=step.key or "")
        new = (result or "").strip()
        if step.key != new:
            step.key = new
            bus.settings_dirty.emit()
            _apply_mode()
    key_btn.clicked.connect(_on_key_btn)

    # Right-click on the key button = clear (back to coord-click mode).
    def _on_key_context(_pos) -> None:
        if step.key:
            step.key = ""
            bus.settings_dirty.emit()
            _apply_mode()
    key_btn.setContextMenuPolicy(Qt.CustomContextMenu)
    key_btn.customContextMenuRequested.connect(_on_key_context)
    _apply_mode()

    test_btn = IconButton("테스트", "play", size="sm")
    test_btn.setToolTip("이 단계 1회 실행 — 키 입력이면 키, 좌표면 클릭")
    test_btn.clicked.connect(lambda: bus.recovery_step_test.emit(idx))
    row.addWidget(test_btn)

    del_btn = IconOnlyButton("trash-2", size="md", tooltip="이 단계 삭제")
    del_btn.clicked.connect(lambda: on_delete(idx))
    row.addWidget(del_btn)

    card.add(row)
    return card


def make_recovery_card() -> QWidget:
    """Embeddable recovery widget — designed to live inside Combat tab.

    Returns a single QWidget with the full UI (toggles + triggers +
    timing + steps list). Caller adds it to a parent layout.
    """
    rec = mock_settings.recovery

    main = QWidget()
    v = QVBoxLayout(main); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)

    # Header card — title + enable + triggers
    head_card = Card("사냥터 복귀 매크로",
                      subtitle="트리거 발동 → 대기 → 미리 정한 좌표 순차 클릭")
    head_card.body.setSpacing(8)

    enable_row = QHBoxLayout(); enable_row.setSpacing(10)
    enable_lbl = QLabel("사용")
    reactive(enable_lbl, lambda: f"color:{T.palette.text_secondary};")
    enable_sw = IOSToggle(width=44, height=22)
    enable_sw.set_state(bool(rec.enabled), animate=False)
    def _on_enable(on: bool) -> None:
        if rec.enabled != on:
            rec.enabled = on; bus.settings_dirty.emit()
    enable_sw.toggled.connect(_on_enable)
    enable_row.addWidget(enable_lbl); enable_row.addWidget(enable_sw); enable_row.addSpacing(20)
    trig_lbl = QLabel("트리거"); reactive(trig_lbl, lambda: f"color:{T.palette.text_secondary};")
    enable_row.addWidget(trig_lbl)
    cb1 = _bind_check(rec, "trigger_potion",  "물약 부족")
    cb2 = _bind_check(rec, "trigger_pk",      "PK 감지")
    enable_row.addWidget(cb1); enable_row.addWidget(cb2)
    enable_row.addStretch(1)
    head_card.add(enable_row)

    # ── Slot trigger row — label + slot buttons on one compact row ──
    # HP 0% (death) trigger removed: death recovery has different
    # game logic (revival selection) and would need a separate flow.
    from quickcast.ui.sections._mock_state import slot_state
    btns_box = QWidget()
    btns_row = QHBoxLayout(btns_box); btns_row.setSpacing(6)
    btns_row.setContentsMargins(0, 0, 0, 0)
    slot_lbl = QLabel("슬롯 트리거")
    reactive(slot_lbl, lambda: f"color:{T.palette.text_secondary};")
    btns_row.addWidget(slot_lbl)

    def _btn_qss() -> str:
        p = T.palette
        return (
            f"QPushButton {{ background:{p.bg_input}; color:{p.text_secondary};"
            f" border:1px solid {p.border_default}; border-radius:4px;"
            f" font-size:12px; font-weight:600; padding:0; }}"
            f"QPushButton:hover {{ background:{p.bg_hover}; color:{p.text_primary}; }}"
            f"QPushButton:checked {{ background:{p.accent_default};"
            f" color:{p.text_inverse}; border-color:{p.accent_default}; }}"
        )

    def _rebuild_slot_trigger_btns() -> None:
        # Drop deleted slot ids from trigger_slot_ids so they don't
        # silently keep firing recovery against a slot that no longer
        # exists.
        valid = set(slot_state.order())
        before = list(rec.trigger_slot_ids)
        rec.trigger_slot_ids[:] = [s for s in before if s in valid]
        if rec.trigger_slot_ids != before:
            bus.settings_dirty.emit()

        # Wipe everything EXCEPT the leading label (index 0) which is
        # static. Reverse iteration so removing items by index stays
        # consistent.
        while btns_row.count() > 1:
            item = btns_row.takeAt(btns_row.count() - 1)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for sid in slot_state.order():
            b = QPushButton(f"#{sid}")
            b.setCheckable(True); b.setFixedSize(40, 26)
            b.setChecked(sid in rec.trigger_slot_ids)
            reactive(b, _btn_qss)
            def _on_slot_btn(checked: bool, _sid=sid) -> None:
                if checked and _sid not in rec.trigger_slot_ids:
                    rec.trigger_slot_ids.append(_sid)
                    bus.settings_dirty.emit()
                elif not checked and _sid in rec.trigger_slot_ids:
                    rec.trigger_slot_ids.remove(_sid)
                    bus.settings_dirty.emit()
            b.toggled.connect(_on_slot_btn)
            btns_row.addWidget(b)
        btns_row.addStretch(1)

    _rebuild_slot_trigger_btns()
    bus.slot_list_changed.connect(_rebuild_slot_trigger_btns)
    head_card.add(btns_box)

    # Timing row — only the start-delay survives. Each step has its
    # own per-step delay, and recovery is now edge-triggered (one fire
    # per trigger event) so the global re-fire cooldown is gone.
    t_row = QHBoxLayout(); t_row.setSpacing(10)
    sd_lbl = QLabel("귀환 후 시작 대기"); reactive(sd_lbl, lambda: f"color:{T.palette.text_secondary};")
    t_row.addWidget(sd_lbl)
    sd = Stepper(rec.start_delay_seconds, 0, 1800, 5, 0, "초", width=120)
    def _on_sd(v: float) -> None:
        new = int(v)
        if rec.start_delay_seconds != new:
            rec.start_delay_seconds = new; bus.settings_dirty.emit()
    sd.valueChanged.connect(_on_sd)
    t_row.addWidget(sd)
    t_row.addStretch(1)
    head_card.add(t_row)
    v.addWidget(head_card)

    # Steps card with add button + list
    steps_card = Card("클릭 순서")
    steps_card.body.setSpacing(8)

    head_row = QHBoxLayout(); head_row.setSpacing(8)
    hint = QLabel("순서대로 클릭됩니다 — 위치 지정 시 대시보드 미리보기 위에서 클릭하세요")
    reactive(hint, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")
    head_row.addWidget(hint, stretch=1)
    add_btn = IconButton("+ 단계 추가", "plus", variant="primary")
    head_row.addWidget(add_btn)
    steps_card.add(head_row)

    # Steps list
    list_wrap = QVBoxLayout(); list_wrap.setSpacing(6)
    steps_card.body.addLayout(list_wrap)
    v.addWidget(steps_card)

    def _delete_step(idx: int) -> None:
        if 0 <= idx < len(rec.steps):
            del rec.steps[idx]
            bus.settings_dirty.emit()
            _rebuild()

    def _pick_step(step: RecoveryStep, idx: int, name_w, x_w, y_w) -> None:
        bus.recovery_pick_request.emit(idx)

    def _rebuild() -> None:
        while list_wrap.count():
            it = list_wrap.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        if not rec.steps:
            empty = QLabel("아직 단계가 없습니다 — ‘+ 단계 추가’를 누르세요")
            reactive(empty, lambda: f"color:{T.palette.text_tertiary}; padding:20px;")
            empty.setAlignment(Qt.AlignCenter)
            list_wrap.addWidget(empty)
            return
        for i, step in enumerate(rec.steps):
            list_wrap.addWidget(_build_step_row(step, i, _delete_step, _pick_step))

    def _add_step() -> None:
        # Place new step at preview center (1280x720 → 640,360) so it's
        # immediately visible as a marker. User can then drag with
        # "위치 지정" to refine.
        n = len(rec.steps) + 1
        rec.steps.append(RecoveryStep(
            label=f"단계 {n}",
            x=640 + (n - 1) * 20,    # slight stagger so multiple stacked steps don't overlap
            y=360 + (n - 1) * 20,
        ))
        bus.settings_dirty.emit()
        _rebuild()
    add_btn.clicked.connect(_add_step)

    _rebuild()
    bus.recovery_pick_done.connect(lambda *_: _rebuild())

    return main


__all__ = ["make_recovery_card"]
