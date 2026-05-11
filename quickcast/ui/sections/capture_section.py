"""Capture — window picker + ROI coordinate calibration.

ROI coordinates and the chosen capture window are bound directly to
`mock_settings` (which is the production Settings instance after
state_bridge.install). Any edit emits `bus.settings_dirty` so the host
saves with debounce.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget,
)

from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconButton, IconOnlyButton
from quickcast.ui.components.status_dot import StatusDot
from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.sections._mock_state import mock_settings
from quickcast.ui.stepper import Stepper


def _bind_axis(label: str, get_v: Callable[[], int], set_v: Callable[[int], None],
                hi: int) -> QWidget:
    """Single label+stepper bound to a getter/setter pair."""
    ax = QLabel(label)
    reactive(ax, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
    s = Stepper(get_v(), 0, hi, 1, 0, "", width=110)

    def _on(v: float) -> None:
        new = int(v)
        if get_v() != new:
            set_v(new)
            bus.settings_dirty.emit()
    s.valueChanged.connect(_on)

    sub = QHBoxLayout(); sub.setContentsMargins(0, 0, 0, 0); sub.setSpacing(4)
    sub.addWidget(ax); sub.addWidget(s)
    wrap = QWidget(); wrap.setLayout(sub)
    return wrap


def _coord_block(title_text: str,
                  get_x: Callable[[], int], set_x: Callable[[int], None],
                  get_y: Callable[[], int], set_y: Callable[[int], None],
                  get_w: Callable[[], int], set_w: Callable[[int], None],
                  get_h: Callable[[], int], set_h: Callable[[int], None]) -> QWidget:
    """One row: [label]  X[..] Y[..] W[..] H[..] — fully bound."""
    widget = QWidget()
    row = QHBoxLayout(widget); row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)

    title = QLabel(title_text); title.setMinimumWidth(50)
    f = QFont(); f.setBold(True); title.setFont(f)
    reactive(title, lambda: f"color:{T.palette.text_primary};")
    row.addWidget(title)

    row.addWidget(_bind_axis("X", get_x, set_x, 1280))
    row.addWidget(_bind_axis("Y", get_y, set_y, 720))
    row.addWidget(_bind_axis("W", get_w, set_w, 1280))
    row.addWidget(_bind_axis("H", get_h, set_h, 720))
    row.addStretch(1)
    return widget


def _clean_title(raw: str) -> str:
    """Strip noise from a window title for display.

    Cursor / VS Code / terminals append a Braille progress spinner
    (U+2800..U+28FF) at the start while running. Browsers add separators
    (' — ', ' - '). Long titles are clipped with ellipsis.
    """
    s = (raw or "").strip()
    # Drop leading Braille spinner characters and adjacent whitespace.
    out = []
    skipping = True
    for ch in s:
        if skipping and (0x2800 <= ord(ch) <= 0x28FF or ch.isspace()):
            continue
        skipping = False
        out.append(ch)
    cleaned = "".join(out).strip()
    if len(cleaned) > 32:
        cleaned = cleaned[:30] + "…"
    return cleaned or "(이름 없음)"


def _open_window_picker(parent_widget: QWidget) -> None:
    """Show the WindowPicker dialog and persist the user's choice."""
    try:
        from quickcast.ui.window_picker import WindowPicker
    except Exception:
        QMessageBox.warning(parent_widget, "창 선택", "WindowPicker를 불러오지 못했습니다.")
        return
    from PySide6.QtWidgets import QDialog
    dlg = WindowPicker(
        current_title=mock_settings.capture_window_title, parent=parent_widget,
    )
    if dlg.exec() != QDialog.Accepted:
        return
    chosen = dlg.chosen()
    if chosen is None:
        return
    if mock_settings.capture_window_title != chosen.title:
        mock_settings.capture_window_title = chosen.title
        bus.settings_dirty.emit()
        # Notify AppWindow so it hot-swaps the controller's capture source.
        try:
            bus.capture_target_changed.emit()
        except Exception:
            pass


def make_capture() -> tuple[QWidget, QWidget]:
    # ── Sidebar: capture target list ──
    sidebar = QWidget()
    sv = QVBoxLayout(sidebar); sv.setContentsMargins(8, 6, 8, 8); sv.setSpacing(4)
    head = QLabel("캡처 소스")
    reactive(head, lambda: f"color:{T.palette.text_secondary}; padding:6px 10px;")
    sv.addWidget(head)

    # Sidebar shows ONE row — the currently saved capture window.
    # All visuals refresh from the LIVE setting on bus.capture_target_changed.
    row = QWidget()
    h = QHBoxLayout(row); h.setContentsMargins(8, 6, 8, 6); h.setSpacing(8)
    dot = QLabel("●")
    text = QLabel("")
    h.addWidget(dot); h.addWidget(text); h.addStretch(1)
    sv.addWidget(row)

    def _refresh_sidebar() -> None:
        new_raw = mock_settings.capture_window_title or ""
        has = bool(new_raw)
        text.setText(_clean_title(new_raw) if has else "선택되지 않음")
        text.setToolTip(new_raw or "")
        # Theme-respecting colours, computed fresh each refresh.
        p = T.palette
        dot.setStyleSheet(
            f"color:{p.accent_default if has else p.text_tertiary}; font-size:11px;"
        )
        text.setStyleSheet(f"color:{p.text_primary};")
        if has:
            row.setStyleSheet(
                f"background:{p.accent_subtle}; border-radius:6px;"
            )
        else:
            row.setStyleSheet("")

    _refresh_sidebar()
    bus.capture_target_changed.connect(_refresh_sidebar)
    bus.theme_changed.connect(_refresh_sidebar)

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

    # Header
    header = QHBoxLayout(); header.setSpacing(8)
    title = QLabel("캡처")
    f = QFont(); f.setBold(True); f.setPointSize(18); title.setFont(f)
    sub = QLabel("게임창 선택 + ROI 좌표 미세조정. 미리보기 위에서 사각형 드래그도 가능")
    reactive(sub, lambda: f"color:{T.palette.text_secondary};")
    box = QVBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(0)
    box.addWidget(title); box.addWidget(sub)
    header.addLayout(box); header.addStretch(1)
    pick_btn = IconButton("게임창 선택", "crosshair", variant="primary")
    pick_btn.clicked.connect(lambda: _open_window_picker(main))
    header.addWidget(pick_btn)
    v.addLayout(header)

    # Connection summary — refreshed when the user picks a new window.
    conn = Card("현재 캡처 대상")
    conn_row = QHBoxLayout(); conn_row.setSpacing(16)
    sd = StatusDot("창")

    def _refresh_current_target() -> None:
        raw = mock_settings.capture_window_title or ""
        sd.set(bool(raw), _clean_title(raw) if raw else "선택되지 않음")
    _refresh_current_target()
    bus.capture_target_changed.connect(_refresh_current_target)

    clear_btn = IconButton("지우기", "x", size="sm")
    def _clear_target() -> None:
        if not mock_settings.capture_window_title:
            return
        mock_settings.capture_window_title = ""
        bus.settings_dirty.emit()
        bus.capture_target_changed.emit()
    clear_btn.clicked.connect(_clear_target)

    conn_row.addWidget(sd); conn_row.addStretch(1); conn_row.addWidget(clear_btn)
    conn.add(conn_row)
    v.addWidget(conn)

    # Monitor fallback option
    opts = Card("옵션")
    fallback = QHBoxLayout(); fallback.setSpacing(8)
    fallback_lbl = QLabel("창이 없을 때 폴백:")
    reactive(fallback_lbl, lambda: f"color:{T.palette.text_secondary};")
    cb = QComboBox(); cb.addItems(["모니터 1 전체", "모니터 2 전체"])
    cb.setCurrentIndex(max(0, min(1, mock_settings.capture_monitor_index - 1)))

    def _on_monitor(idx: int) -> None:
        new_idx = idx + 1
        if mock_settings.capture_monitor_index != new_idx:
            mock_settings.capture_monitor_index = new_idx
            bus.settings_dirty.emit()
    cb.currentIndexChanged.connect(_on_monitor)
    cb.setMinimumWidth(160)
    fallback.addWidget(fallback_lbl); fallback.addWidget(cb); fallback.addStretch(1)
    opts.add(fallback)
    v.addWidget(opts)

    # ROI coordinates — fully bound to settings.
    roi = Card("ROI 좌표 — 1280×720 기준",
                subtitle="미리보기 위에서 사각형 드래그가 더 직관적")

    # HP
    roi.add(_coord_block(
        "HP",
        lambda: mock_settings.hp_cap.x, lambda v: setattr(mock_settings.hp_cap, "x", v),
        lambda: mock_settings.hp_cap.y, lambda v: setattr(mock_settings.hp_cap, "y", v),
        lambda: mock_settings.hp_cap_w, lambda v: setattr(mock_settings, "hp_cap_w", v),
        lambda: mock_settings.hp_cap_h, lambda v: setattr(mock_settings, "hp_cap_h", v),
    ))
    # MP
    roi.add(_coord_block(
        "MP",
        lambda: mock_settings.mp_cap.x, lambda v: setattr(mock_settings.mp_cap, "x", v),
        lambda: mock_settings.mp_cap.y, lambda v: setattr(mock_settings.mp_cap, "y", v),
        lambda: mock_settings.mp_cap_w, lambda v: setattr(mock_settings, "mp_cap_w", v),
        lambda: mock_settings.mp_cap_h, lambda v: setattr(mock_settings, "mp_cap_h", v),
    ))
    # PK
    roi.add(_coord_block(
        "PK",
        lambda: mock_settings.pk.cap.x, lambda v: setattr(mock_settings.pk.cap, "x", v),
        lambda: mock_settings.pk.cap.y, lambda v: setattr(mock_settings.pk.cap, "y", v),
        lambda: mock_settings.pk.cap_w, lambda v: setattr(mock_settings.pk, "cap_w", v),
        lambda: mock_settings.pk.cap_h, lambda v: setattr(mock_settings.pk, "cap_h", v),
    ))
    # Potion
    roi.add(_coord_block(
        "물약",
        lambda: mock_settings.potion.cap.x, lambda v: setattr(mock_settings.potion.cap, "x", v),
        lambda: mock_settings.potion.cap.y, lambda v: setattr(mock_settings.potion.cap, "y", v),
        lambda: mock_settings.potion.cap_w, lambda v: setattr(mock_settings.potion, "cap_w", v),
        lambda: mock_settings.potion.cap_h, lambda v: setattr(mock_settings.potion, "cap_h", v),
    ))
    v.addWidget(roi)

    v.addStretch(1)
    return sidebar, main


__all__ = ["make_capture"]
