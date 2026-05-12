"""Capture — window picker + ROI coordinate calibration.

ROI coordinates and the chosen capture window are bound directly to
`mock_settings` (which is the production Settings instance after
state_bridge.install). Any edit emits `bus.settings_dirty` so the host
saves with debounce.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
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


def _make_reset_button() -> QPushButton:
    """Compact "리셋" button used inline next to every ROI coord row.

    Text-only (no icon) and slightly tinted so it doesn't compete with
    the primary action buttons. The earlier "↺" arrow glyph wasn't
    present in the bundled UI font and rendered as the missing-glyph
    box on most setups.
    """
    b = QPushButton("리셋")
    b.setFixedHeight(26)
    b.setMinimumWidth(46)
    b.setToolTip("이 ROI를 초기 위치로 복원합니다")
    b.setCursor(Qt.PointingHandCursor)
    f = QFont(); f.setPointSize(9); b.setFont(f)
    # Subtle ghost style — lighter than the main action buttons.
    reactive(b, lambda: (
        f"QPushButton {{"
        f" color:{T.palette.text_secondary};"
        f" background:transparent;"
        f" border:1px solid {T.palette.border_default};"
        f" border-radius:4px; padding:2px 8px;"
        f"}}"
        f"QPushButton:hover {{ color:{T.palette.text_primary};"
        f" border-color:{T.palette.border_strong}; }}"
    ))
    return b


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
                  get_h: Callable[[], int], set_h: Callable[[int], None],
                  reset_kind: Optional[str] = None) -> QWidget:
    """One row: [label]  X[..] Y[..] W[..] H[..]  [↺]   — fully bound.

    When ``reset_kind`` is one of the ROI_DEFAULTS keys ("hp" / "mp" /
    "pk" / "potion" / "hp_text" / "mp_text" / "potion_text") a small
    "↺" reset button is added after the W/H stepper; clicking it
    restores the built-in default placement so a ROI that ended up
    off-screen can be recovered without manually retyping coords.
    """
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

    if reset_kind is not None:
        reset_btn = _make_reset_button()
        def _do_reset() -> None:
            if mock_settings.reset_roi(reset_kind):
                bus.settings_dirty.emit()
        reset_btn.clicked.connect(_do_reset)
        row.addWidget(reset_btn)

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
        reset_kind="hp",
    ))
    # MP
    roi.add(_coord_block(
        "MP",
        lambda: mock_settings.mp_cap.x, lambda v: setattr(mock_settings.mp_cap, "x", v),
        lambda: mock_settings.mp_cap.y, lambda v: setattr(mock_settings.mp_cap, "y", v),
        lambda: mock_settings.mp_cap_w, lambda v: setattr(mock_settings, "mp_cap_w", v),
        lambda: mock_settings.mp_cap_h, lambda v: setattr(mock_settings, "mp_cap_h", v),
        reset_kind="mp",
    ))
    # PK
    roi.add(_coord_block(
        "PK",
        lambda: mock_settings.pk.cap.x, lambda v: setattr(mock_settings.pk.cap, "x", v),
        lambda: mock_settings.pk.cap.y, lambda v: setattr(mock_settings.pk.cap, "y", v),
        lambda: mock_settings.pk.cap_w, lambda v: setattr(mock_settings.pk, "cap_w", v),
        lambda: mock_settings.pk.cap_h, lambda v: setattr(mock_settings.pk, "cap_h", v),
        reset_kind="pk",
    ))
    # Potion
    roi.add(_coord_block(
        "물약",
        lambda: mock_settings.potion.cap.x, lambda v: setattr(mock_settings.potion.cap, "x", v),
        lambda: mock_settings.potion.cap.y, lambda v: setattr(mock_settings.potion.cap, "y", v),
        lambda: mock_settings.potion.cap_w, lambda v: setattr(mock_settings.potion, "cap_w", v),
        lambda: mock_settings.potion.cap_h, lambda v: setattr(mock_settings.potion, "cap_h", v),
        reset_kind="potion",
    ))
    v.addWidget(roi)

    # ────────────────── OCR (텍스트 기반 인식) ──────────────────
    ocr_card = Card(
        "OCR — 텍스트 기반 인식 (베타)",
        subtitle=(
            "HP/MP/물약 숫자를 텍스트로 직접 읽습니다. 화면 크기 어디서나 자동 추적.\n"
            "1) [자동 영역 검출] → 2) 각 행마다 [학습] 여러 번 (서로 다른 값으로) "
            "→ 3) [OCR 모드 사용] 체크. 학습 누적할수록 정확도 향상."
        ),
    )

    # Latest frame stash — bus.live_frame keeps it fresh.
    state = {"frame": None}    # type: dict[str, Optional[np.ndarray]]
    def _on_live_frame(image, _analysis, _fps):
        state["frame"] = image
    bus.live_frame.connect(_on_live_frame)

    # OCR-mode toggle (handler defined after rows below so refresh
    # closures see all widgets).
    mode_row = QHBoxLayout(); mode_row.setSpacing(8)
    mode_cb = QCheckBox("OCR 모드 사용 (학습된 글자 + 텍스트 영역으로 인식)")
    mode_cb.setChecked(bool(getattr(mock_settings, "ocr_mode", False)))
    mode_row.addWidget(mode_cb); mode_row.addStretch(1)
    ocr_card.add(mode_row)

    # ── PK position inside the OCR card ──
    # When OCR mode is on the legacy ROI card is hidden, so PK (which
    # has no OCR equivalent — it's an icon match, not a digit) needs a
    # home here so the user can still position it.
    ocr_pk_row = _coord_block(
        "PK 위치",
        lambda: mock_settings.pk.cap.x, lambda v: setattr(mock_settings.pk.cap, "x", v),
        lambda: mock_settings.pk.cap.y, lambda v: setattr(mock_settings.pk.cap, "y", v),
        lambda: mock_settings.pk.cap_w, lambda v: setattr(mock_settings.pk, "cap_w", v),
        lambda: mock_settings.pk.cap_h, lambda v: setattr(mock_settings.pk, "cap_h", v),
        reset_kind="pk",
    )
    ocr_card.add(ocr_pk_row)

    # Default text ROI seeds. Used when the user enables OCR mode for
    # the first time without running auto-detect (or auto-detect missed
    # a region) — gives a visible draggable rectangle at a sensible
    # spot so MP/HP/POTION text boxes don't end up hidden at (0,0,0,0).
    _DEFAULT_TEXT_ROIS: dict[str, tuple[int, int, int, int]] = {
        "hp": (60, 18, 200, 18),
        "mp": (60, 40, 200, 18),
        "potion": (560, 600, 64, 28),
    }

    def _ensure_text_rois_have_defaults() -> bool:
        """Seed any zero-sized text ROI with a default placement.

        Returns True when at least one ROI was filled in (caller emits
        settings_dirty + repaint).
        """
        from quickcast.config import Point
        changed = False
        if mock_settings.hp_text_cap_w <= 0 or mock_settings.hp_text_cap_h <= 0:
            x, y, w, h = _DEFAULT_TEXT_ROIS["hp"]
            mock_settings.hp_text_cap = Point(x=x, y=y)
            mock_settings.hp_text_cap_w = w; mock_settings.hp_text_cap_h = h
            changed = True
        if mock_settings.mp_text_cap_w <= 0 or mock_settings.mp_text_cap_h <= 0:
            x, y, w, h = _DEFAULT_TEXT_ROIS["mp"]
            mock_settings.mp_text_cap = Point(x=x, y=y)
            mock_settings.mp_text_cap_w = w; mock_settings.mp_text_cap_h = h
            changed = True
        if mock_settings.potion_text_cap_w <= 0 or mock_settings.potion_text_cap_h <= 0:
            x, y, w, h = _DEFAULT_TEXT_ROIS["potion"]
            mock_settings.potion_text_cap = Point(x=x, y=y)
            mock_settings.potion_text_cap_w = w; mock_settings.potion_text_cap_h = h
            changed = True
        return changed

    # Mode-driven visibility: in OCR mode the legacy ROI card disappears
    # entirely (PK is exposed inside this OCR card via ocr_pk_row instead),
    # and the ocr_pk_row shows up. In legacy mode the legacy card is
    # back and ocr_pk_row hides.
    def _refresh_mode_visibility() -> None:
        ocr = bool(getattr(mock_settings, "ocr_mode", False))
        roi.setVisible(not ocr)
        ocr_pk_row.setVisible(ocr)
    _refresh_mode_visibility()

    def _on_mode(checked: bool) -> None:
        mock_settings.ocr_mode = bool(checked)
        if checked and _ensure_text_rois_have_defaults():
            # Defaults filled in — let the preview redraw and disk save.
            pass
        _refresh_mode_visibility()
        bus.settings_dirty.emit()
    mode_cb.toggled.connect(_on_mode)

    # Auto-detect button (fills HP/MP/POTION text ROIs from the live frame)
    # + "학습 관리" button to open the per-glyph instance manager.
    detect_row = QHBoxLayout(); detect_row.setSpacing(8)
    detect_btn = IconButton("자동 영역 검출", "crosshair", variant="primary")
    manage_btn = IconButton("학습 관리", "settings", variant="secondary")
    def _open_manage() -> None:
        from quickcast.ui.components.ocr_manage import OcrManageDialog
        OcrManageDialog(parent=main).exec()
    manage_btn.clicked.connect(_open_manage)
    detect_status = QLabel("")
    reactive(detect_status, lambda: f"color:{T.palette.text_secondary};")

    def _autodetect() -> None:
        frame = state.get("frame")
        if frame is None:
            QMessageBox.information(main, "자동 검출",
                "캡처가 활성화된 후 시도해주세요. (게임창 선택 + 마스터 시작)")
            return
        from quickcast.core.hud_detect import auto_detect_all
        results = auto_detect_all(frame)
        applied: list[str] = []
        if "hp" in results:
            g = results["hp"]
            mock_settings.hp_text_cap.x, mock_settings.hp_text_cap.y = g.x, g.y
            mock_settings.hp_text_cap_w = g.w; mock_settings.hp_text_cap_h = g.h
            applied.append("HP")
        if "mp" in results:
            g = results["mp"]
            mock_settings.mp_text_cap.x, mock_settings.mp_text_cap.y = g.x, g.y
            mock_settings.mp_text_cap_w = g.w; mock_settings.mp_text_cap_h = g.h
            applied.append("MP")
        if "potion" in results:
            g = results["potion"]
            mock_settings.potion_text_cap.x, mock_settings.potion_text_cap.y = g.x, g.y
            mock_settings.potion_text_cap_w = g.w; mock_settings.potion_text_cap_h = g.h
            applied.append("물약")
        if applied:
            detect_status.setText(f"검출 적용: {', '.join(applied)} — 미세조정은 아래 박스 X/Y/W/H로")
            bus.settings_dirty.emit()
        else:
            detect_status.setText("자동 검출 실패 — 아래 박스 X/Y/W/H를 수동 입력해주세요")
    detect_btn.clicked.connect(_autodetect)
    detect_row.addWidget(detect_btn)
    detect_row.addWidget(manage_btn)
    detect_row.addWidget(detect_status, 1)
    ocr_card.add(detect_row)

    # Per-region calibration block builder
    def _ocr_region_row(
        title: str,
        get_x: Callable[[], int], set_x: Callable[[int], None],
        get_y: Callable[[], int], set_y: Callable[[int], None],
        get_w: Callable[[], int], set_w: Callable[[int], None],
        get_h: Callable[[], int], set_h: Callable[[int], None],
        suggested_truth: str,
        reset_kind: Optional[str] = None,
    ) -> QWidget:
        """One row: [HP] X[..] Y[..] W[..] H[..]  [↺]  [학습]."""
        wrap = QWidget()
        row = QHBoxLayout(wrap); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(10)

        head = QLabel(title); head.setMinimumWidth(50)
        f = QFont(); f.setBold(True); head.setFont(f)
        reactive(head, lambda: f"color:{T.palette.text_primary};")
        row.addWidget(head)

        row.addWidget(_bind_axis("X", get_x, set_x, 1280))
        row.addWidget(_bind_axis("Y", get_y, set_y, 720))
        row.addWidget(_bind_axis("W", get_w, set_w, 1280))
        row.addWidget(_bind_axis("H", get_h, set_h, 720))

        if reset_kind is not None:
            reset_btn = _make_reset_button()
            def _do_reset() -> None:
                if mock_settings.reset_roi(reset_kind):
                    bus.settings_dirty.emit()
            reset_btn.clicked.connect(_do_reset)
            row.addWidget(reset_btn)

        train = IconButton("학습", "settings", variant="secondary", size="sm")
        def _train() -> None:
            frame = state.get("frame")
            if frame is None:
                QMessageBox.information(main, "OCR 학습",
                    "캡처가 활성화된 후 시도해주세요. (게임창 선택 + 마스터 시작)")
                return
            w, h = get_w(), get_h()
            if w <= 0 or h <= 0:
                QMessageBox.information(main, "OCR 학습",
                    "텍스트 영역(W/H)이 0입니다. 먼저 [자동 영역 검출]을 누르거나 W/H를 입력해주세요.")
                return
            x, y = get_x(), get_y()
            try:
                roi = frame[y : y + h, x : x + w]
            except Exception:
                roi = None
            if roi is None or roi.size == 0:
                QMessageBox.warning(main, "OCR 학습",
                    "영역 잘라내기에 실패했습니다. X/Y/W/H가 화면 범위 내인지 확인해주세요.")
                return
            from quickcast.ui.components.ocr_calibration import OcrCalibrationDialog
            dlg = OcrCalibrationDialog(np.ascontiguousarray(roi),
                                          suggested_truth=suggested_truth,
                                          parent=main)
            if dlg.exec():
                # CRITICAL: persist the threshold the user just settled
                # on. Without this, inference falls back to the auto
                # percentile while the saved glyph masks were binarised
                # at the user's manual value — masks differ, OCR fails
                # to match its own templates 1:1. (0 == auto sentinel.)
                thr = dlg.chosen_threshold()
                mock_settings.ocr_threshold = int(thr) if thr is not None else 0
                bus.settings_dirty.emit()
                # Recognizer picks up the new templates from disk via
                # the bus subscription wired in AppWindow.
                try:
                    bus.digit_templates_changed.emit()
                except Exception:
                    pass
                # Show per-label sample counts + total so the user sees
                # the cumulative training progress and is nudged to
                # keep adding passes for better accuracy.
                try:
                    from quickcast.ui.components.notification_center import NotificationCenter
                    from quickcast.core.digit_store import instance_counts
                    added = dlg.added_summary()
                    counts = instance_counts()
                    if added:
                        # "1: 2개 (총 5), 2: 1개 (총 3) ..."
                        bits = []
                        for ch, n in sorted(added.items()):
                            bits.append(f"{ch} +{n} (총 {counts.get(ch, n)})")
                        msg = "🔤 학습 추가됨 — " + ", ".join(bits)
                        NotificationCenter.toast(msg, level="success", duration_ms=3200)
                except Exception:
                    pass
        train.clicked.connect(_train)
        row.addWidget(train)
        row.addStretch(1)
        return wrap

    ocr_card.add(_ocr_region_row(
        "HP",
        lambda: mock_settings.hp_text_cap.x, lambda v: setattr(mock_settings.hp_text_cap, "x", v),
        lambda: mock_settings.hp_text_cap.y, lambda v: setattr(mock_settings.hp_text_cap, "y", v),
        lambda: mock_settings.hp_text_cap_w, lambda v: setattr(mock_settings, "hp_text_cap_w", v),
        lambda: mock_settings.hp_text_cap_h, lambda v: setattr(mock_settings, "hp_text_cap_h", v),
        "",
        reset_kind="hp_text",
    ))
    ocr_card.add(_ocr_region_row(
        "MP",
        lambda: mock_settings.mp_text_cap.x, lambda v: setattr(mock_settings.mp_text_cap, "x", v),
        lambda: mock_settings.mp_text_cap.y, lambda v: setattr(mock_settings.mp_text_cap, "y", v),
        lambda: mock_settings.mp_text_cap_w, lambda v: setattr(mock_settings, "mp_text_cap_w", v),
        lambda: mock_settings.mp_text_cap_h, lambda v: setattr(mock_settings, "mp_text_cap_h", v),
        "",
        reset_kind="mp_text",
    ))
    ocr_card.add(_ocr_region_row(
        "물약",
        lambda: mock_settings.potion_text_cap.x, lambda v: setattr(mock_settings.potion_text_cap, "x", v),
        lambda: mock_settings.potion_text_cap.y, lambda v: setattr(mock_settings.potion_text_cap, "y", v),
        lambda: mock_settings.potion_text_cap_w, lambda v: setattr(mock_settings, "potion_text_cap_w", v),
        lambda: mock_settings.potion_text_cap_h, lambda v: setattr(mock_settings, "potion_text_cap_h", v),
        "",
        reset_kind="potion_text",
    ))

    v.addWidget(ocr_card)

    v.addStretch(1)
    return sidebar, main


__all__ = ["make_capture"]
