"""Combat — PK + Potion master-detail.

All controls bind two-way to `mock_settings.pk` / `mock_settings.potion`
(which is the production Settings instance after state_bridge.install).
Any change emits `bus.settings_dirty` so AppWindow saves with debounce.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconButton
from quickcast.ui.components.key_capture_dialog import KeyCaptureDialog
from quickcast.ui.components.level_slider import LevelSlider
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.range_slider import RangeSlider
from quickcast.ui.sections._mock_state import (
    COMBAT_LEVELS, mock_settings, percent_to_threshold,
)
from quickcast.ui.stepper import Stepper


_LABEL_W = 72


# ───────── tiny helpers (no behaviour change beyond binding) ─────────
def _form_row(label: str, *controls: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
    lbl = QLabel(label); lbl.setFixedWidth(_LABEL_W)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    h.addWidget(lbl)
    for c in controls:
        h.addWidget(c)
    h.addStretch(1)
    return w


def _form_row_pair(left_label: str, left_ctrl: QWidget,
                    right_label: str, right_ctrl: QWidget) -> QWidget:
    """Two label+control pairs on one row, separated by spacer."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
    l1 = QLabel(left_label); l1.setFixedWidth(_LABEL_W)
    reactive(l1, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    h.addWidget(l1); h.addWidget(left_ctrl); h.addSpacing(16)
    l2 = QLabel(right_label); l2.setFixedWidth(_LABEL_W)
    reactive(l2, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    h.addWidget(l2); h.addWidget(right_ctrl); h.addStretch(1)
    return w


def _bind_hp_range(target, color: str | None = None) -> QWidget:
    """HP-range row bound to `target.hp.lo` / `target.hp.hi`."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
    lbl = QLabel("HP 범위"); lbl.setFixedWidth(_LABEL_W)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    lo, hi = int(target.hp.min), int(target.hp.max)
    mn = QLineEdit(f"{lo}%"); mn.setFixedSize(64, 30); mn.setAlignment(Qt.AlignCenter)
    mx = QLineEdit(f"{hi}%"); mx.setFixedSize(64, 30); mx.setAlignment(Qt.AlignCenter)
    sep = QLabel("~"); sep.setFixedWidth(10); sep.setAlignment(Qt.AlignCenter)
    reactive(sep, lambda: f"color:{T.palette.text_tertiary};")
    sl = RangeSlider(0, 100, lo, hi, fill_color=color or T.palette.hp_fill)
    sl.setMinimumWidth(80)
    h.addWidget(lbl); h.addWidget(mn); h.addWidget(sep); h.addWidget(mx); h.addWidget(sl, stretch=1)

    def _on_slider(lo_v: int, hi_v: int) -> None:
        mn.blockSignals(True); mx.blockSignals(True)
        mn.setText(f"{lo_v}%"); mx.setText(f"{hi_v}%")
        mn.blockSignals(False); mx.blockSignals(False)
        target.hp.min = lo_v; target.hp.max = hi_v
        bus.settings_dirty.emit()

    def _on_text() -> None:
        try:
            lo_v = int(mn.text().rstrip("%").strip() or 0)
            hi_v = int(mx.text().rstrip("%").strip() or 100)
        except ValueError:
            return
        sl.set_values(lo_v, hi_v)
        target.hp.min = lo_v; target.hp.max = hi_v
        bus.settings_dirty.emit()

    sl.rangeChanged.connect(_on_slider)
    mn.editingFinished.connect(_on_text)
    mx.editingFinished.connect(_on_text)
    return w


def _bind_key(target) -> QPushButton:
    """Clickable key chip bound to `target.key`."""
    btn = QPushButton(target.key or "0")
    btn.setFixedSize(56, 32)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setToolTip("클릭해서 변경 (한 글자 또는 F1~F12 / Enter / Space)")
    f = QFont(); f.setBold(True); f.setPointSize(13); btn.setFont(f)
    reactive(btn, lambda: (
        f"QPushButton {{ background:{T.palette.bg_input}; color:{T.palette.accent_default};"
        f" border:1px solid {T.palette.border_default}; border-radius:6px; }}"
        f"QPushButton:hover {{ background:{T.palette.bg_hover};"
        f" border-color:{T.palette.border_focus}; }}"
        f"QPushButton:pressed {{ background:{T.palette.bg_pressed}; }}"
    ))

    def _on_click() -> None:
        result = KeyCaptureDialog.get_key(btn, current=btn.text())
        if result and result != target.key:
            btn.setText(result)
            target.key = result
            bus.settings_dirty.emit()
    btn.clicked.connect(_on_click)
    return btn


def _bind_stepper(target, attr: str, *step_args, suffix: str = "", width: int = 112) -> Stepper:
    cur = getattr(target, attr)
    s = Stepper(cur, *step_args, suffix, width=width)
    def _on(v: float) -> None:
        # Keep the field's declared type (int vs float) intact.
        cur_attr = getattr(target, attr)
        new_val = int(v) if isinstance(cur_attr, int) and not isinstance(cur_attr, bool) else float(v)
        if cur_attr != new_val:
            setattr(target, attr, new_val)
            bus.settings_dirty.emit()
    s.valueChanged.connect(_on)
    return s


def _bind_check(target, attr: str, label: str) -> QCheckBox:
    cb = QCheckBox(label)
    cb.setChecked(bool(getattr(target, attr)))
    def _on(on: bool) -> None:
        if getattr(target, attr) != on:
            setattr(target, attr, on)
            bus.settings_dirty.emit()
    cb.toggled.connect(_on)
    return cb


def _bind_sensitivity(target, kind: str):
    """Continuous 1-100% sensitivity slider bound to `target.threshold`.

    With NORMED template matching, threshold scales linearly with the
    correlation percent: at 100 the score must equal the template
    perfectly; at 1 nearly any pixel pattern fires. Per-kind max picks
    the same magnitudes as the legacy 5-step preset so saved values
    survive the migration:
      • PK     — 0..5_000_000   (1% = 50_000)
      • Potion — 0..  250_000   (1% =  2_500)
    """
    from PySide6.QtWidgets import QSlider
    scale = 50_000 if kind == "pk" else 2_500     # 1% increment
    max_thr = 5_000_000 if kind == "pk" else 250_000

    sens_lbl = QLabel("감지 민감도"); sens_lbl.setFixedWidth(_LABEL_W)
    reactive(sens_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    sens_row = QHBoxLayout(); sens_row.setContentsMargins(0, 0, 0, 0); sens_row.setSpacing(8)
    sens_row.addWidget(sens_lbl)

    cur_thr = int(target.threshold)
    initial_pct = max(1, min(100, round(cur_thr / scale)))

    sl = QSlider(Qt.Horizontal)
    sl.setRange(1, 100)
    sl.setValue(initial_pct)
    sl.setMinimumWidth(120)
    sl.setSingleStep(1); sl.setPageStep(5)
    sl.setTickInterval(10); sl.setTickPosition(QSlider.NoTicks)

    val_lbl = QLabel(f"{initial_pct}% ({cur_thr:,})")
    val_lbl.setFixedWidth(124); val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    reactive(val_lbl, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono}; font-size:11px;")

    def _on(pct: int) -> None:
        new_thr = pct * scale
        val_lbl.setText(f"{pct}% ({new_thr:,})")
        if target.threshold != new_thr:
            target.threshold = new_thr
            bus.settings_dirty.emit()
    sl.valueChanged.connect(_on)

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

    sens_row.addWidget(sl, stretch=1); sens_row.addWidget(val_lbl)
    return sens_row, sl


def _bind_sustain(target) -> QHBoxLayout:
    """PK/물약 인식 유지 시간 — 감지 상태가 N초 이상 연속될 때만 발동.

    펫 오버레이 자동닫기와 동일한 오탐 방지 패턴. 0초면 즉시 발동(예전
    동작), 1~5초가 일반적인 사용 구간. 사용자가 캡을 더 키울 수 있도록
    상한은 10초로 둔다.
    """
    from PySide6.QtWidgets import QSlider
    row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
    lbl = QLabel("인식 유지"); lbl.setFixedWidth(_LABEL_W)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    row.addWidget(lbl)

    cur = float(getattr(target, "sustain_seconds", 0.0) or 0.0)
    cur_tenths = max(0, min(100, round(cur * 10)))    # 0.0~10.0초 → 0..100 (0.1초 단위)
    sl = QSlider(Qt.Horizontal)
    sl.setRange(0, 100)
    sl.setValue(cur_tenths)
    sl.setMinimumWidth(120)
    sl.setSingleStep(1); sl.setPageStep(10)
    sl.setTickInterval(10); sl.setTickPosition(QSlider.NoTicks)

    val_lbl = QLabel("즉시" if cur <= 0 else f"{cur:.1f}초")
    val_lbl.setFixedWidth(124); val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    reactive(val_lbl, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono}; font-size:11px;")

    def _on(tenths: int) -> None:
        secs = round(tenths / 10.0, 1)
        val_lbl.setText("즉시" if secs <= 0 else f"{secs:.1f}초")
        if float(target.sustain_seconds) != secs:
            target.sustain_seconds = secs
            bus.settings_dirty.emit()
    sl.valueChanged.connect(_on)

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


# ───────── card builders ─────────
def _bind_ios(target_obj, attr: str, label: str) -> QHBoxLayout:
    """Compact label + iOS toggle pair for booleans (사용/반복).

    The toggle re-syncs from `target_obj.attr` on `bus.slot_state_refresh`
    so when the controller auto-disables a one-shot (e.g. potion.use
    flips False after a fire) the visual toggle catches up.
    """
    from quickcast.ui.ios_toggle import IOSToggle
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

    def _resync() -> None:
        cur = bool(getattr(target_obj, attr))
        if sw.is_on() != cur:
            sw.set_state(cur, animate=True)
    bus.slot_state_refresh.connect(_resync)

    row.addWidget(lbl); row.addWidget(sw)
    return row


def _build_card(target, *, title: str, subtitle: str, kind: str) -> Card:
    card = Card(title, subtitle=subtitle)
    card.body.setSpacing(10)

    top = QHBoxLayout(); top.setSpacing(16)
    top.addLayout(_bind_ios(target, "use", "사용"))
    if hasattr(target, "repeat"):
        top.addLayout(_bind_ios(target, "repeat", "반복"))
    score = QLabel("점수 0 / 임계 0")
    reactive(score, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
    top.addStretch(1); top.addWidget(score)
    card.add(top)
    card._score_lbl = score    # exposed for live updates

    # Row 1: 입력 키 + 연사 횟수 한 줄
    card.add(_form_row_pair(
        "입력 키",   _bind_key(target),
        "연사 횟수", _bind_stepper(target, "count", 1, 99, 1, 0, suffix="회", width=120),
    ))
    # Row 2: 연사 간격 + 쿨타임 한 줄 (PK는 cooltime 있고 물약도 있음)
    if hasattr(target, "cooltime"):
        card.add(_form_row_pair(
            "연사 간격", _bind_stepper(target, "delay",    0,   10, 0.1, 2, suffix="초", width=148),
            "쿨타임",    _bind_stepper(target, "cooltime", 0, 86400, 1.0, 0, suffix="초", width=148),
        ))
    else:
        card.add(_form_row(
            "연사 간격", _bind_stepper(target, "delay", 0, 10, 0.1, 2, suffix="초", width=148),
        ))
    card.add(_bind_hp_range(target))
    sens_row, _sl = _bind_sensitivity(target, kind)
    card.add(sens_row)
    if hasattr(target, "sustain_seconds"):
        card.add(_bind_sustain(target))
    return card


def make_combat() -> tuple[QWidget, QWidget]:
    # No sidebar — the PK/Potion/Recovery cards are visually
    # self-explanatory and a list of dummy navigation entries on the
    # left was just clutter. AppShell collapses the sidebar when we
    # return None.
    sidebar = None

    # ── Main: PK + Potion side by side ──
    # Wrap content in a QScrollArea so the recovery card with N click
    # steps doesn't get clipped at the bottom of the window.
    from PySide6.QtWidgets import QScrollArea, QFrame
    main = QScrollArea()
    main.setWidgetResizable(True)
    main.setFrameShape(QFrame.NoFrame)
    main.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    main.setWidget(inner)
    v = QVBoxLayout(inner); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(14)
    title = QLabel("전투 대응")
    f = QFont(); f.setBold(True); f.setPointSize(18); title.setFont(f)
    v.addWidget(title)

    pk_card = _build_card(mock_settings.pk,     title="PK 대응",        subtitle="전투 감지 시 자동 키 입력",   kind="pk")
    po_card = _build_card(mock_settings.potion, title="물약 부족 대응", subtitle="! 표시 감지 시 귀환 키 자동 입력", kind="potion")
    # QGridLayout with equal column stretches is the most reliable way
    # to enforce true 50/50 — each column gets exactly half the parent
    # width regardless of the cards' content minimum widths.
    from PySide6.QtWidgets import QGridLayout, QSizePolicy
    pk_card.setMinimumWidth(0); po_card.setMinimumWidth(0)
    pk_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    po_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
    grid_lay = QGridLayout(); grid_lay.setSpacing(14); grid_lay.setContentsMargins(0, 0, 0, 0)
    grid_lay.setColumnStretch(0, 1); grid_lay.setColumnStretch(1, 1)
    grid_lay.addWidget(pk_card, 0, 0)
    grid_lay.addWidget(po_card, 0, 1)
    v.addLayout(grid_lay)

    # ── Recovery sequence (마을 귀환 → 자동 사냥 복귀) ──
    from quickcast.ui.sections.recovery_section import make_recovery_card
    v.addWidget(make_recovery_card())
    v.addStretch(1)

    # Live score wiring — Dashboard preview broadcasts via bus.live_scores.
    _diag = {"po_last": -1.0}    # closure box for once-only logging
    def _on_scores(hp, mp, pk_score, po_score, pk_det, po_emp, fps):
        col_pk = T.palette.state_danger if pk_det else T.palette.text_tertiary
        col_po = T.palette.state_warning if po_emp else T.palette.text_tertiary
        pk_thr = int(mock_settings.pk.threshold)
        po_thr = int(mock_settings.potion.threshold)
        pk_on = bool(getattr(mock_settings.pk, "use", True))
        po_on = bool(getattr(mock_settings.potion, "use", True))
        if hasattr(pk_card, "_score_lbl"):
            if pk_on:
                pk_card._score_lbl.setText(f"점수 {int(pk_score):,} / 임계 {pk_thr:,}")
            else:
                pk_card._score_lbl.setText(f"감지 OFF · 임계 {pk_thr:,}")
            pk_card._score_lbl.setStyleSheet(
                f"color:{col_pk}; font-family:{T.type.mono};"
                f" font-weight:{700 if pk_det else 400};"
            )
        if hasattr(po_card, "_score_lbl"):
            if po_on:
                po_card._score_lbl.setText(f"점수 {int(po_score):,} / 임계 {po_thr:,}")
            else:
                po_card._score_lbl.setText(f"감지 OFF · 임계 {po_thr:,}")
            po_card._score_lbl.setStyleSheet(
                f"color:{col_po}; font-family:{T.type.mono};"
                f" font-weight:{700 if po_emp else 400};"
            )
        # Diagnostic: log when potion score changes by >5% so we can see
        # in the dashboard log whether the value is genuinely 0 (template
        # mismatch) or whether it's updating but the label isn't.
        if abs(po_score - _diag["po_last"]) > max(100.0, abs(_diag["po_last"]) * 0.05):
            from quickcast.utils.logger import logger
            logger.debug(
                f"combat _on_scores: po={po_score:.0f} thr={po_thr} "
                f"empty={po_emp} pk={pk_score:.0f}"
            )
            _diag["po_last"] = po_score
    bus.live_scores.connect(_on_scores)

    return sidebar, main


__all__ = ["make_combat"]
