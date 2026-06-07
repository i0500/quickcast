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
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
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


# OCR feature is parked for now — accuracy work in progress. Flip
# this to True to bring the whole OCR card (mode toggle, auto-detect,
# per-region text ROIs + train buttons, manage dialog, PK reposition
# inside OCR card) back. All OCR code paths stay compiled so re-enabling
# is one-line; setting also forces ocr_mode False on every section
# rebuild so any stale userdata.json doesn't quietly leave it on.
OCR_FEATURE_ENABLED = False


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
    """Single label+stepper bound to a getter/setter pair.

    Reactive: bus.settings_dirty / bus.client_changed re-pull the getter
    value into the stepper so a tab swap or external edit (e.g. ROI
    drag in the preview, reset button) updates the displayed coord
    without needing per-call-site wiring.
    """
    ax = QLabel(label)
    reactive(ax, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
    s = Stepper(get_v(), 0, hi, 1, 0, "", width=110)

    def _on(v: float) -> None:
        new = int(v)
        if get_v() != new:
            set_v(new)
            bus.settings_dirty.emit()
    s.valueChanged.connect(_on)

    def _resync() -> None:
        try:
            cur = int(get_v())
            if int(s.value()) != cur:
                s.setValue(cur)
        except RuntimeError:
            try:
                bus.settings_dirty.disconnect(_resync)
                bus.client_changed.disconnect(_resync_via_cid)
            except Exception:
                pass
    def _resync_via_cid(_cid: str) -> None:
        _resync()
    bus.settings_dirty.connect(_resync)
    bus.client_changed.connect(_resync_via_cid)

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


def _set_client_capture_title(cid: str, title: str) -> None:
    """Write capture_window_title onto a specific client's profile.

    Updates the per-client ClientProfile directly (not the top-level
    active mirror) so picking a window for client2 while viewing client1
    doesn't leak into client1's capture target. When the target client
    happens to be active, the top-level mirror is updated too so
    AppWindow's _hot_swap_capture sees the new value on its next read.
    """
    prof = mock_settings.clients.get(cid)
    if prof is None:
        return
    if prof.capture_window_title == title:
        return
    prof.capture_window_title = title
    if cid == mock_settings.active_client_id:
        mock_settings.capture_window_title = title
    bus.settings_dirty.emit()
    try:
        bus.capture_target_changed.emit()
    except Exception:
        pass


def _open_window_picker_for(cid: str, parent_widget: QWidget) -> None:
    """Show the WindowPicker dialog and persist the choice on client `cid`.

    This writes onto the named client's ClientProfile rather than always
    on the active client — so the user can configure both tabs from a
    single Capture page without juggling tab swaps.
    """
    try:
        from quickcast.ui.window_picker import WindowPicker
    except Exception:
        QMessageBox.warning(parent_widget, "창 선택", "WindowPicker를 불러오지 못했습니다.")
        return
    from PySide6.QtWidgets import QDialog
    prof = mock_settings.clients.get(cid)
    current = prof.capture_window_title if prof is not None else ""
    dlg = WindowPicker(current_title=current, parent=parent_widget)
    if dlg.exec() != QDialog.Accepted:
        return
    chosen = dlg.chosen()
    if chosen is None:
        return
    _set_client_capture_title(cid, chosen.title)


def _open_window_picker(parent_widget: QWidget) -> None:
    """Legacy entry point — picks for the currently-active client."""
    _open_window_picker_for(mock_settings.active_client_id, parent_widget)


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
    # Multi-client: tab swap 시 active 클라의 capture_window_title이
    # mock_settings에 미러되므로 사이드바도 새 클라 게임창 이름으로 갱신.
    bus.client_changed.connect(lambda _cid: _refresh_sidebar())

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
    # Word-wrap + Ignored horizontal policy so the long subtitle doesn't
    # push the scroll area's inner widget past viewport width.
    sub.setWordWrap(True)
    sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    reactive(sub, lambda: f"color:{T.palette.text_secondary};")
    box = QVBoxLayout(); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(0)
    box.addWidget(title); box.addWidget(sub)
    header.addLayout(box, stretch=1)
    pick_btn = IconButton("게임창 선택", "crosshair", variant="primary")
    pick_btn.clicked.connect(lambda: _open_window_picker(main))
    header.addWidget(pick_btn)
    v.addLayout(header)

    # Connection summary — one row PER CLIENT so the user can configure
    # both tabs from this page without tab-swapping. Each row writes
    # ONLY onto its own ClientProfile (see _set_client_capture_title) so
    # picking a window for 클라2 never bleeds into 클라1.
    conn = Card("현재 캡처 대상")

    def _add_client_row(cid: str) -> None:
        prof = mock_settings.clients.get(cid)
        if prof is None:
            return
        label = prof.label or cid
        row = QHBoxLayout(); row.setSpacing(12)
        sd = StatusDot(label)
        pick = IconButton("선택", "crosshair", size="sm")
        pick.clicked.connect(
            lambda _=False, _cid=cid: _open_window_picker_for(_cid, main)
        )
        clear = IconButton("지우기", "x", size="sm")
        def _do_clear(_=False, _cid=cid) -> None:
            p = mock_settings.clients.get(_cid)
            if p is None or not p.capture_window_title:
                return
            _set_client_capture_title(_cid, "")
        clear.clicked.connect(_do_clear)
        row.addWidget(sd); row.addStretch(1)
        row.addWidget(pick); row.addWidget(clear)
        conn.add(row)

        def _refresh(_cid=cid, _sd=sd) -> None:
            p = mock_settings.clients.get(_cid)
            raw = (p.capture_window_title if p is not None else "") or ""
            _sd.set(bool(raw), _clean_title(raw) if raw else "선택되지 않음")
        _refresh()
        bus.capture_target_changed.connect(_refresh)
        bus.theme_changed.connect(_refresh)

    for _cid in mock_settings.clients.keys():
        _add_client_row(_cid)

    # Source-size + DPI diagnostic — surfaces the "캡처 1/4 좌상단" symptom
    # at a glance. 1280×720 is the recognition pipeline's normalised
    # canvas; if the source is smaller, letterbox upscales (blurry) and
    # if the DPI ≠ 96 the GetDpiForWindow correction kicked in.
    src_lbl = QLabel("원본: -")
    reactive(src_lbl, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
    conn.add(src_lbl)

    def _on_src_info(w: int, h: int, dpi: int) -> None:
        if w <= 0 or h <= 0:
            src_lbl.setText("원본: - (캡처 대기)")
            return
        scale = (dpi / 96.0) if dpi > 0 else 1.0
        scale_pct = int(round(scale * 100))
        warn = ""
        if w < 1280 or h < 720:
            warn = "  ⚠ 1280×720 미만 — 인식률 저하 가능"
        elif scale_pct != 100:
            warn = f"  (보정됨 — 윈도우 배율 {scale_pct}%)"
        src_lbl.setText(
            f"원본: {w}×{h} · 윈도우 DPI {dpi} ({scale_pct}%)"
            f" → 1280×720 정규화{warn}"
        )
    bus.capture_source_info.connect(_on_src_info)

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

    # Aspect-profile lock — when ON (default), the same ROI calibration is
    # used regardless of detected source aspect. Combined with the
    # letterbox normalisation in core/capture.py this means a single
    # calibration works in fullscreen and windowed mode.
    lock_row = QHBoxLayout(); lock_row.setSpacing(8)
    lock_cb = QCheckBox("Aspect 프로파일 잠금 (전체화면/창모드 동일 ROI)")
    lock_cb.setChecked(bool(getattr(mock_settings, "lock_aspect_profile", True)))
    lock_cb.setToolTip(
        "ON: 소스 aspect가 바뀌어도 ROI 보정값을 그대로 사용 (letterbox 정규화).\n"
        "OFF: aspect별로 별도의 ROI 프로파일을 자동 스왑 (예전 동작)."
    )
    def _on_lock_aspect(on: bool) -> None:
        if getattr(mock_settings, "lock_aspect_profile", True) != on:
            mock_settings.lock_aspect_profile = on
            bus.settings_dirty.emit()
    lock_cb.toggled.connect(_on_lock_aspect)
    lock_row.addWidget(lock_cb); lock_row.addStretch(1)
    opts.add(lock_row)
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
    # Buff count (top-left "75") — feeds the recovery 마을 대기 trigger
    roi.add(_coord_block(
        "버프 카운트",
        lambda: mock_settings.buff.cap.x, lambda v: setattr(mock_settings.buff.cap, "x", v),
        lambda: mock_settings.buff.cap.y, lambda v: setattr(mock_settings.buff.cap, "y", v),
        lambda: mock_settings.buff.cap_w, lambda v: setattr(mock_settings.buff, "cap_w", v),
        lambda: mock_settings.buff.cap_h, lambda v: setattr(mock_settings.buff, "cap_h", v),
        reset_kind="buff",
    ))
    v.addWidget(roi)

    # ────────────────── OCR (텍스트 기반 인식) ──────────────────
    # Feature flag — see OCR_FEATURE_ENABLED at the top of this file.
    # When False we still BUILD the OCR card (all the wiring stays
    # alive so it can be flipped back on without code surgery) but
    # immediately hide it. We also force ocr_mode off so any user who
    # enabled it in a previous build falls back cleanly to the
    # colour/template detectors.
    if not OCR_FEATURE_ENABLED:
        try:
            if getattr(mock_settings, "ocr_mode", False):
                mock_settings.ocr_mode = False
                bus.settings_dirty.emit()
        except Exception:
            pass

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
        domain: Optional[str] = None,
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
                                          parent=main,
                                          domain=domain)
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
        domain="hp",
    ))
    ocr_card.add(_ocr_region_row(
        "MP",
        lambda: mock_settings.mp_text_cap.x, lambda v: setattr(mock_settings.mp_text_cap, "x", v),
        lambda: mock_settings.mp_text_cap.y, lambda v: setattr(mock_settings.mp_text_cap, "y", v),
        lambda: mock_settings.mp_text_cap_w, lambda v: setattr(mock_settings, "mp_text_cap_w", v),
        lambda: mock_settings.mp_text_cap_h, lambda v: setattr(mock_settings, "mp_text_cap_h", v),
        "",
        reset_kind="mp_text",
        domain="mp",
    ))
    ocr_card.add(_ocr_region_row(
        "물약",
        lambda: mock_settings.potion_text_cap.x, lambda v: setattr(mock_settings.potion_text_cap, "x", v),
        lambda: mock_settings.potion_text_cap.y, lambda v: setattr(mock_settings.potion_text_cap, "y", v),
        lambda: mock_settings.potion_text_cap_w, lambda v: setattr(mock_settings, "potion_text_cap_w", v),
        lambda: mock_settings.potion_text_cap_h, lambda v: setattr(mock_settings, "potion_text_cap_h", v),
        "",
        reset_kind="potion_text",
        domain="potion",
    ))

    v.addWidget(ocr_card)
    if not OCR_FEATURE_ENABLED:
        ocr_card.setVisible(False)

    # ────────────────── 버프 카운트 OCR ──────────────────
    # 마을 대기 트리거 전용. HP/MP/물약 OCR이 비활성이어도 항상 노출 —
    # 사냥터 복귀 매크로의 town_idle 트리거에 꼭 필요한 입력.
    buff_card = _build_buff_ocr_card(state, main)
    v.addWidget(buff_card)

    # ────────────────── 오버레이 자동 닫기 ──────────────────
    overlay_card = _build_overlay_close_card()
    v.addWidget(overlay_card)

    v.addStretch(1)
    return sidebar, main


# ────────── buff-count OCR card ──────────
def _build_buff_ocr_card(state: dict, parent: "QWidget") -> "QWidget":
    """버프 카운트(좌상단 75) OCR — 마을 대기 복귀 트리거의 입력 채널.

    HP/MP/물약 OCR 카드와 같은 학습 파이프라인을 쓰지만, 그쪽은 일시
    숨김(`OCR_FEATURE_ENABLED=False`) 상태라 별도 카드로 노출. 사용자가
    여기서 ROI/임계값을 보정하고 [학습] 버튼으로 0~9 글자를 한 번 학습.

    마을 대기 ON/OFF 토글 + 임계값/지속 시간/학습 등 모든 town-idle 설정이
    이 카드 한 곳에 모입니다. 전투대응 탭 recovery 카드에는 이 항목들이
    중복 노출되지 않습니다(단일 진실 원천).
    """
    from quickcast.ui.ios_toggle import IOSToggle

    rec = mock_settings.recovery
    buff_obj = getattr(mock_settings, "buff", None)

    card = Card(
        "버프 카운트 OCR — 마을 대기 인식",
        subtitle=(
            "좌상단 버프 갯수(예: 75)를 텍스트로 인식. 인식값이 임계값 미만으로"
            " ‘지속 시간’ 이상 유지되면 사냥터 복귀 시퀀스가 자동 실행됩니다.\n"
            "1) 토글 ON → 2) ROI 박스 위치 맞추기 → 3) [학습]으로 0~9 글자 학습."
        ),
        inline_subtitle=False,
    )

    # ── 마을 대기 ON/OFF 토글 (오버레이 자동 닫기 카드와 동일 패턴) ──
    # 한 토글이 recovery.trigger_town_idle + buff.enabled 두 플래그를
    # 동시에 켜고 끔. 인식 파이프라인이 OCR 스캔 자체를 시작하려면
    # buff.enabled, 복귀 트리거 조건으로 쓰려면 trigger_town_idle 둘 다 필요.
    toggle_row = QHBoxLayout(); toggle_row.setSpacing(8); toggle_row.setContentsMargins(0, 0, 0, 0)
    toggle = IOSToggle(width=44, height=22)
    initial_on = bool(rec.trigger_town_idle) and bool(buff_obj is not None and buff_obj.enabled)
    toggle.set_state(initial_on, animate=False)
    toggle_lbl = QLabel("마을 대기 사용")
    fnt = QFont(); fnt.setBold(True); toggle_lbl.setFont(fnt)
    reactive(toggle_lbl, lambda: f"color:{T.palette.text_primary};")
    toggle_hint = QLabel("(버프 카운트 OCR로 마을 체류 감지 → 사냥터 복귀)")
    reactive(toggle_hint, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")

    def _on_toggle(on: bool) -> None:
        from quickcast.config import ROI_DEFAULTS
        changed = False
        if rec.trigger_town_idle != on:
            rec.trigger_town_idle = on; changed = True
        if buff_obj is not None and bool(buff_obj.enabled) != on:
            buff_obj.enabled = on; changed = True
        # Auto-seed ROI on first ON so the box appears immediately.
        if on and int(getattr(mock_settings, "buff_text_cap_w", 0) or 0) <= 0:
            x, y, w, h = ROI_DEFAULTS.get("buff_text", (8, 66, 28, 24))
            mock_settings.buff_text_cap.x = int(x)
            mock_settings.buff_text_cap.y = int(y)
            mock_settings.buff_text_cap_w = int(w)
            mock_settings.buff_text_cap_h = int(h)
            changed = True
        if changed:
            bus.settings_dirty.emit()
    toggle.toggled.connect(_on_toggle)

    # Two-way sync — sidebar "마을 대기" / external writes to
    # rec.trigger_town_idle or buff.enabled keep this toggle in lockstep.
    def _resync_buff_toggle() -> None:
        try:
            cur = bool(rec.trigger_town_idle) and bool(
                buff_obj is not None and buff_obj.enabled
            )
            if toggle.is_on() != cur:
                toggle.set_state(cur, animate=True)
        except RuntimeError:
            try:
                bus.settings_dirty.disconnect(_resync_buff_toggle)
            except Exception:
                pass
    bus.settings_dirty.connect(_resync_buff_toggle)

    toggle_row.addWidget(toggle)
    toggle_row.addWidget(toggle_lbl)
    toggle_row.addWidget(toggle_hint)
    toggle_row.addStretch(1)
    card.add(toggle_row)

    # ROI 좌표 행
    coord = _coord_block(
        "ROI",
        lambda: mock_settings.buff_text_cap.x,
        lambda v: setattr(mock_settings.buff_text_cap, "x", v),
        lambda: mock_settings.buff_text_cap.y,
        lambda v: setattr(mock_settings.buff_text_cap, "y", v),
        lambda: mock_settings.buff_text_cap_w,
        lambda v: setattr(mock_settings, "buff_text_cap_w", v),
        lambda: mock_settings.buff_text_cap_h,
        lambda v: setattr(mock_settings, "buff_text_cap_h", v),
        reset_kind="buff_text",
    )
    card.add(coord)

    # 임계값 + 학습 행
    actions = QHBoxLayout(); actions.setSpacing(10)
    thr_lbl = QLabel("임계값 (이 값 미만이면 마을):")
    reactive(thr_lbl, lambda: f"color:{T.palette.text_secondary};")
    actions.addWidget(thr_lbl)
    from quickcast.ui.stepper import Stepper
    thr_in = Stepper(
        mock_settings.recovery.town_idle_threshold, 1, 999, 1, 0, "", width=100,
    )
    def _on_thr(val: float) -> None:
        new = int(val)
        if mock_settings.recovery.town_idle_threshold != new:
            mock_settings.recovery.town_idle_threshold = new
            bus.settings_dirty.emit()
    thr_in.valueChanged.connect(_on_thr)
    actions.addWidget(thr_in)
    actions.addStretch(1)

    # Multi-client: tab swap 시 mock_settings.recovery 객체의 in-place
    # mutate된 새 클라 값을 Stepper visual에도 반영. settings_dirty +
    # client_changed 둘 다 구독 — 어느 한쪽에서 emit돼도 갱신.
    def _resync_thr() -> None:
        try:
            cur = int(mock_settings.recovery.town_idle_threshold)
            if int(thr_in.value()) != cur:
                thr_in.setValue(cur)
        except RuntimeError:
            try:
                bus.settings_dirty.disconnect(_resync_thr)
                bus.client_changed.disconnect(_resync_thr_via_cid)
            except Exception:
                pass
    def _resync_thr_via_cid(_cid: str) -> None:
        _resync_thr()
    bus.settings_dirty.connect(_resync_thr)
    bus.client_changed.connect(_resync_thr_via_cid)

    learn_btn = IconButton("학습 (0~9)", "settings", variant="primary", size="sm")
    learn_btn.setToolTip("현재 캡처 프레임에서 버프 ROI를 잘라 OCR 글자 학습 다이얼로그 실행")
    def _train() -> None:
        frame = state.get("frame")
        if frame is None:
            QMessageBox.information(parent, "버프 OCR 학습",
                "캡처가 활성화된 후 시도해주세요. (게임창 선택 + 마스터 시작)")
            return
        w, h = mock_settings.buff_text_cap_w, mock_settings.buff_text_cap_h
        if w <= 0 or h <= 0:
            QMessageBox.information(parent, "버프 OCR 학습",
                "ROI W/H가 0입니다. 먼저 ROI 좌표 행에서 박스 크기를 입력하거나 [리셋] 누르세요.")
            return
        x, y = mock_settings.buff_text_cap.x, mock_settings.buff_text_cap.y
        try:
            roi = frame[y : y + h, x : x + w]
        except Exception:
            roi = None
        if roi is None or roi.size == 0:
            QMessageBox.warning(parent, "버프 OCR 학습",
                "ROI 잘라내기 실패. 좌표가 1280×720 범위 안인지 확인해주세요.")
            return
        # Mirror the 2× upsample that recognition.py applies to the
        # buff_text ROI before OCR — keeps training segmentation and
        # inference segmentation consistent so the templates the user
        # labels here are the same shape glyphs the matcher sees later.
        import cv2 as _cv2
        rh, rw = roi.shape[:2]
        roi = _cv2.resize(roi, (rw * 2, rh * 2),
                            interpolation=_cv2.INTER_CUBIC)
        from quickcast.ui.components.ocr_calibration import OcrCalibrationDialog
        dlg = OcrCalibrationDialog(np.ascontiguousarray(roi),
                                      suggested_truth="",
                                      parent=parent,
                                      domain="buff")
        if dlg.exec():
            thr = dlg.chosen_threshold()
            mock_settings.ocr_threshold = int(thr) if thr is not None else 0
            bus.settings_dirty.emit()
            try:
                bus.digit_templates_changed.emit()
            except Exception:
                pass
            from quickcast.ui.components.notification_center import NotificationCenter
            from quickcast.core.digit_store import instance_counts
            added = dlg.added_summary()
            counts = instance_counts(domain="buff")
            if added:
                bits = [f"{ch} +{n} (총 {counts.get(ch, n)})" for ch, n in sorted(added.items())]
                NotificationCenter.toast("🔤 버프 OCR 학습 추가 — " + ", ".join(bits),
                                          level="success", duration_ms=3200)
    learn_btn.clicked.connect(_train)
    actions.addWidget(learn_btn)

    # 학습 초기화 버튼 — 누적된 버프 글자 템플릿 + 임계값 + canonical
    # 메타파일까지 한 번에 삭제. ROI 좌표 리셋과는 별개 (그건 "리셋").
    reset_learn_btn = IconButton("학습 초기화", "trash-2", variant="ghost", size="sm")
    reset_learn_btn.setToolTip(
        "지금까지 학습한 버프 0~9 글자 + 임계값 + canonical 크기를 모두 삭제.\n"
        "ROI 좌표는 그대로 유지됩니다. 되돌릴 수 없으니 주의."
    )
    def _reset_learning() -> None:
        from quickcast.core.digit_store import instance_counts, clear_templates
        cur = instance_counts(domain="buff")
        total = sum(cur.values())
        if total == 0:
            QMessageBox.information(parent, "버프 학습 초기화",
                "초기화할 학습 데이터가 없습니다.")
            return
        ret = QMessageBox.question(
            parent, "버프 학습 초기화",
            f"버프 도메인에 학습된 글자 {total}개를 모두 삭제할까요?\n"
            f"({', '.join(f'{k}={v}' for k, v in sorted(cur.items()))})\n\n"
            "ROI 좌표/임계값 슬라이더 설정은 유지되지만, "
            "도메인의 .threshold/.canonical 메타파일도 함께 삭제됩니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        deleted = clear_templates(domain="buff")
        try:
            bus.digit_templates_changed.emit()
        except Exception:
            pass
        from quickcast.ui.components.notification_center import NotificationCenter
        NotificationCenter.toast(
            f"🧹 버프 학습 데이터 초기화됨 ({deleted}개 파일 삭제)",
            level="info", duration_ms=2800,
        )
    reset_learn_btn.clicked.connect(_reset_learning)
    actions.addWidget(reset_learn_btn)
    card.add(actions)

    # ── 지속 시간 (마을 대기 시간) — 분 단위 ──
    # OCR 값이 임계값 미만으로 이 시간 이상 유지되면 복귀 시퀀스 발동.
    # 사냥터 복귀의 "귀환 후 시작 대기"(start_delay_seconds)와는 다른 개념:
    #   • town_idle_seconds  → 마을 상태로 "판정"하기 위한 누적 시간 (입력 측)
    #   • start_delay_seconds → 트리거 후 클릭 시퀀스 시작까지 대기 (출력 측)
    dur_row = QHBoxLayout(); dur_row.setSpacing(10)
    dur_lbl = QLabel("지속 시간 (이 시간 이상 마을이면 복귀):")
    reactive(dur_lbl, lambda: f"color:{T.palette.text_secondary};")
    dur_row.addWidget(dur_lbl)
    dur_in = Stepper(rec.town_idle_seconds / 60.0, 1.0, 30.0, 1.0, 0, "분", width=120)
    dur_in.setToolTip("OCR로 읽은 버프 갯수가 임계값 미만으로 이 시간 동안 유지되면 복귀 시퀀스 실행")
    def _on_dur(v: float) -> None:
        new = int(round(max(1.0, v) * 60))
        if rec.town_idle_seconds != new:
            rec.town_idle_seconds = new
            bus.settings_dirty.emit()
    dur_in.valueChanged.connect(_on_dur)
    dur_row.addWidget(dur_in)
    dur_row.addStretch(1)
    card.add(dur_row)

    # ── 오탐 범위 (OCR 신뢰도 하한) ──
    # 한 프레임의 OCR conf가 이 값 미만이면 "오탐"으로 간주해 마을 대기
    # 타이머를 진행시키지 않음. 기본 0.60. 올리면 더 엄격(오탐 ↓ / 진짜
    # 마을 상태도 한두 프레임 놓칠 수 있음), 내리면 더 민감(잡음에 약함).
    conf_row = QHBoxLayout(); conf_row.setSpacing(10)
    conf_lbl = QLabel("오탐 범위 (OCR 신뢰도 하한):")
    reactive(conf_lbl, lambda: f"color:{T.palette.text_secondary};")
    conf_row.addWidget(conf_lbl)
    conf_in = Stepper(
        float(getattr(rec, "town_idle_min_confidence", 0.60)),
        0.0, 1.0, 0.05, 2, "", width=120,
    )
    conf_in.setToolTip(
        "한 프레임의 OCR 신뢰도가 이 값 미만이면 마을 대기로 판정하지 않음.\n"
        "기본 0.60. 높일수록 오탐이 줄지만 인식이 깐깐해지고, 낮출수록 민감해짐."
    )
    def _on_conf(v: float) -> None:
        new = max(0.0, min(1.0, float(v)))
        cur = float(getattr(rec, "town_idle_min_confidence", 0.60))
        if abs(cur - new) > 1e-6:
            rec.town_idle_min_confidence = new
            bus.settings_dirty.emit()
    conf_in.valueChanged.connect(_on_conf)
    conf_row.addWidget(conf_in)
    conf_row.addStretch(1)
    card.add(conf_row)

    # 실시간 인식 결과 표시
    live = QLabel("OCR 결과: -")
    reactive(live, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
    card.add(live)

    def _on_analysis(_image, analysis, _fps):
        if analysis is None:
            return
        cnt = getattr(analysis, "buff_count", None)
        conf = getattr(analysis, "buff_confidence", 0.0)
        text = getattr(analysis, "buff_text", "")
        thr_n = mock_settings.recovery.town_idle_threshold
        min_c = float(getattr(rec, "town_idle_min_confidence", 0.60))
        if cnt is None:
            live.setText(f"OCR 결과: text={text!r}  conf={conf:.2f}  (값 미확정)")
        else:
            if conf < min_c:
                tag = f"오탐 무시 (conf<{min_c:.2f})"
            else:
                tag = "마을 대기 ▼" if cnt < thr_n else "정상"
            live.setText(f"OCR 결과: {cnt}  conf={conf:.2f}  [{tag}, 임계={thr_n}]")
    bus.live_frame.connect(_on_analysis)

    return card


# ────────── overlay-close card ──────────
# Friendly labels for the known overlay ids. Adding a new overlay only
# needs (a) a template png under data/targets/ and (b) an entry here
# (plus a Settings.overlay_closes default if it should ship enabled).
_OVERLAY_LABELS: dict[str, str] = {
    "pet_whistle": "펫 호루라기 (교감 성공 발바닥)",
    "item_acquired": "아이템 획득 (보상 상자)",
    "blood_pledge": "혈맹 축복 활성화",
}


def _build_overlay_close_card() -> "QWidget":
    """오버레이 자동 닫기 — 펫 호루라기 / 아이템 획득 등 중앙 팝업 감지 + ESC."""
    from quickcast.config import OverlayClose, Point
    from quickcast.core.recognition import TARGETS_DIR
    from quickcast.ui.ios_toggle import IOSToggle

    card = Card(
        "오버레이 자동 닫기",
        subtitle=(
            "펫 호루라기 발바닥이 3초 이상 지속되면 ESC로 자동 닫아서 "
            "슬롯 스킬이 다시 동작하도록 합니다."
        ),
        inline_subtitle=False,
    )

    # Live overlay scores feed from the capture loop via bus.live_frame
    # → state_bridge → bus.overlay_scores (emitted as a dict).
    score_labels: dict[str, QLabel] = {}

    def _ensure_default_roi(ov: "OverlayClose") -> None:
        """Seed sane defaults if userdata.json shipped zero rectangle."""
        if ov.cap_w <= 0 or ov.cap_h <= 0:
            ov.cap = Point(x=591, y=100)
            ov.cap_w = 114
            ov.cap_h = 93

    def _make_row(ov_id: str) -> QWidget:
        # Pull/create the OverlayClose entry — gracefully handles a
        # legacy userdata.json that predates this feature. When a new
        # overlay is being added (e.g. blood_pledge introduced after
        # the user already calibrated pet/item), seed it from the
        # existing item_acquired / pet_whistle so it lines up with
        # the centered-popup area the user has already dialed in.
        oc_dict = getattr(mock_settings, "overlay_closes", None)
        if oc_dict is None:
            mock_settings.overlay_closes = {}    # type: ignore[attr-defined]
            oc_dict = mock_settings.overlay_closes
        if ov_id not in oc_dict:
            seed = oc_dict.get("item_acquired") or oc_dict.get("pet_whistle")
            if seed is not None:
                oc_dict[ov_id] = OverlayClose(
                    cap=Point(x=seed.cap.x, y=seed.cap.y),
                    cap_w=int(seed.cap_w),
                    cap_h=int(seed.cap_h),
                    threshold=int(seed.threshold),
                    close_key=seed.close_key,
                    cooldown_seconds=float(seed.cooldown_seconds),
                    sustain_seconds=float(seed.sustain_seconds),
                )
            else:
                oc_dict[ov_id] = OverlayClose()
        ov = oc_dict[ov_id]
        _ensure_default_roi(ov)

        template_path = TARGETS_DIR / f"{ov_id}.png"
        has_template = template_path.exists()

        wrap = QWidget()
        col = QVBoxLayout(wrap); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(6)

        # Top row: [toggle] [label] [상태] [임계값] [테스트]
        top = QHBoxLayout(); top.setSpacing(8); top.setContentsMargins(0, 0, 0, 0)
        tg = IOSToggle(width=44, height=24)
        tg.set_state(bool(ov.enabled), animate=False)

        name = QLabel(_OVERLAY_LABELS.get(ov_id, ov_id))
        f = QFont(); f.setBold(True); name.setFont(f)
        reactive(name, lambda: f"color:{T.palette.text_primary};")

        badge = QLabel("[템플릿 없음]" if not has_template else "")
        reactive(
            badge,
            lambda has=has_template: (
                f"color:{T.palette.text_tertiary}; font-size:11px;"
                if has else
                f"color:{T.palette.state_warning}; font-size:11px;"
            ),
        )

        score = QLabel("점수: -")
        reactive(score, lambda: f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
        score_labels[ov_id] = score

        # Sensitivity slider — 1..100 % maps linearly to 0..5_000_000
        # legacy threshold magnitude (same scale as PK so users get the
        # same mental model: low %=느슨, 높을수록 엄격).
        from PySide6.QtWidgets import QSlider
        _OV_SCALE = 50_000   # 1 % step = 50,000 legacy units (PK parity)
        sens_lbl = QLabel("감지 민감도")
        reactive(sens_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
        cur_pct = max(1, min(100, round(int(ov.threshold) / _OV_SCALE)))
        thr = QSlider(Qt.Horizontal)
        thr.setRange(1, 100)
        thr.setValue(cur_pct)
        thr.setMinimumWidth(120)
        thr.setSingleStep(1); thr.setPageStep(5)
        thr.setTickInterval(10); thr.setTickPosition(QSlider.NoTicks)
        val_lbl = QLabel(f"{cur_pct}% ({int(ov.threshold):,})")
        val_lbl.setFixedWidth(124); val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        reactive(
            val_lbl,
            lambda: f"color:{T.palette.text_tertiary}; "
                     f"font-family:{T.type.mono}; font-size:11px;",
        )

        def _on_thr(pct: int, _ov=ov, _lbl=val_lbl) -> None:
            new = int(pct) * _OV_SCALE
            _lbl.setText(f"{pct}% ({new:,})")
            if int(_ov.threshold) != new:
                _ov.threshold = new
                bus.settings_dirty.emit()
        thr.valueChanged.connect(_on_thr)

        def _slider_qss() -> str:
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
        reactive(thr, _slider_qss)

        test_btn = IconButton("테스트 ESC", "x", size="sm")
        test_btn.setToolTip("게임창에 close_key (기본 ESC)을 한 번 보냅니다.")

        def _on_test(_=None, _id=ov_id, _ov=ov) -> None:
            try:
                bus.overlay_close_test.emit(_id, (_ov.close_key or "esc"))
            except Exception:
                pass
        test_btn.clicked.connect(_on_test)

        def _on_toggle(on: bool, _ov=ov, _has=has_template) -> None:
            if _ov.enabled == on:
                return
            if on and not _has:
                # Re-check at runtime in case the user just dropped the
                # template png. Cheap stat call; far better than letting
                # the toggle silently do nothing on first session.
                if not template_path.exists():
                    from quickcast.utils.logger import logger
                    logger.warning(
                        f"⚠️ {ov_id} 템플릿 미존재 — data/targets/{ov_id}.png 필요"
                    )
                    tg.set_state(False, animate=False)
                    return
            _ov.enabled = on
            bus.settings_dirty.emit()
        tg.toggled.connect(_on_toggle)

        # Two-way sync — dashboard sidebar (펫호루라기/아이템획득/혈맹축복
        # 토글) writes _ov.enabled and emits settings_dirty. Without
        # this _resync, this card's toggle stays stale until next tab swap.
        def _resync_ov(_ov=ov, _tg=tg) -> None:
            try:
                cur = bool(_ov.enabled)
                if _tg.is_on() != cur:
                    _tg.set_state(cur, animate=True)
            except RuntimeError:
                try:
                    bus.settings_dirty.disconnect(_resync_ov)
                except Exception:
                    pass
        bus.settings_dirty.connect(_resync_ov)

        top.addWidget(tg)
        top.addWidget(name)
        if badge.text():
            top.addWidget(badge)
        top.addStretch(1)
        top.addWidget(score)
        top.addSpacing(8)
        top.addWidget(test_btn)
        col.addLayout(top)

        # Sub-row: sensitivity slider gets its own line so it has enough
        # width to drag cleanly (the row above is already busy).
        sens_row = QHBoxLayout(); sens_row.setContentsMargins(0, 0, 0, 0); sens_row.setSpacing(8)
        sens_row.addWidget(sens_lbl)
        sens_row.addWidget(thr, stretch=1)
        sens_row.addWidget(val_lbl)
        col.addLayout(sens_row)

        # Coord row — search ROI in 1280×720 normalised space.
        def _gx(_ov=ov) -> int: return _ov.cap.x
        def _sx(v: int, _ov=ov) -> None: _ov.cap.x = v
        def _gy(_ov=ov) -> int: return _ov.cap.y
        def _sy(v: int, _ov=ov) -> None: _ov.cap.y = v
        def _gw(_ov=ov) -> int: return _ov.cap_w
        def _sw(v: int, _ov=ov) -> None: _ov.cap_w = v
        def _gh(_ov=ov) -> int: return _ov.cap_h
        def _sh(v: int, _ov=ov) -> None: _ov.cap_h = v
        coord = _coord_block("ROI", _gx, _sx, _gy, _sy, _gw, _sw, _gh, _sh)
        col.addWidget(coord)

        return wrap

    # 펫 호루라기 + 아이템 획득 + 혈맹 축복 셋 다 노출. 같은 ROI(중앙 팝업
    # 영역)와 임계값 기본값을 공유하므로 UI 형태는 완전히 동일.
    for ov_id in ("pet_whistle", "item_acquired", "blood_pledge"):
        card.add(_make_row(ov_id))

    # Wire live overlay scores from the analysis stream into the score
    # labels. Throttled by the capture loop's fps already; no extra
    # debounce needed at the UI layer.
    def _on_overlay_scores(scores: dict) -> None:
        for ov_id, lbl in score_labels.items():
            entry = scores.get(ov_id) if scores else None
            if entry is None:
                lbl.setText("점수: -")
                lbl.setStyleSheet(
                    f"color:{T.palette.text_tertiary}; font-family:{T.type.mono};")
                continue
            s = int(entry.get("score", 0) or 0)
            hit = bool(entry.get("detected"))
            lbl.setText(f"점수: {s:,}{'  감지 ✓' if hit else ''}")
            # detected → 주황색 강조 (전투 PK/물약 카드와 동일 패턴).
            lbl.setStyleSheet(
                f"color:{T.palette.state_warning if hit else T.palette.text_tertiary};"
                f" font-family:{T.type.mono};"
                f" font-weight:{700 if hit else 400};"
            )
    try:
        bus.overlay_scores.connect(_on_overlay_scores)
    except Exception:
        # bus.overlay_scores may not exist yet on hot-reload; controller
        # registers it on first emit anyway.
        pass

    return card


__all__ = ["make_capture"]
