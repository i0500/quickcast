"""Settings — Arduino, Telegram, Input backend, Theme, Data import/export.

All controls bind to `mock_settings` (the production Settings instance
after state_bridge.install). Theme / accessibility prefs are also
applied immediately for live feedback.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

from quickcast.config import Settings
from quickcast.ui.components.card import Card
from quickcast.ui.components.icon_button import IconButton
from quickcast.ui.components.status_dot import StatusDot
from quickcast.ui.design import themes as design_themes
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T
from quickcast.ui.sections._mock_state import mock_settings
from quickcast.utils.logger import logger


_BACKENDS = [
    ("arduino",     "Arduino HID", "USB 보드 필요 · HW 레벨 입력 (안티치트 회피 최강)"),
    ("postmessage", "PostMessage", "보드 없이 SW 입력 · 게임 포커스 불필요"),
]


def _form_row(label: str, *controls: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
    lbl = QLabel(label); lbl.setMinimumWidth(100)
    reactive(lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    h.addWidget(lbl)
    for c in controls:
        h.addWidget(c)
    h.addStretch(1)
    return w


def _panel_connection() -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    conn = Card("연결")

    # Arduino row
    a_dot = StatusDot("Arduino")
    a_dot.set(False, mock_settings.arduino_port or "미연결")
    a_port = QLineEdit(mock_settings.arduino_port)
    a_port.setPlaceholderText("COM3"); a_port.setMaximumWidth(120)
    def _on_port() -> None:
        new = a_port.text().strip()
        if mock_settings.arduino_port != new:
            mock_settings.arduino_port = new
            bus.settings_dirty.emit()
    a_port.editingFinished.connect(_on_port)
    a_baud = QComboBox(); a_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
    a_baud.setCurrentText(str(mock_settings.arduino_baud))
    def _on_baud(text: str) -> None:
        try:
            new = int(text)
        except ValueError:
            return
        if mock_settings.arduino_baud != new:
            mock_settings.arduino_baud = new
            bus.settings_dirty.emit()
    a_baud.currentTextChanged.connect(_on_baud)
    a_connect_btn = IconButton("연결", "plug", size="sm", variant="primary")
    a_connect_btn.clicked.connect(lambda: bus.arduino_connect_request.emit())
    # Live state from AppWindow
    def _on_arduino_state(ok: bool, label: str) -> None:
        a_dot.set(ok, label)
        a_connect_btn.setText("해제" if ok else "연결")
    bus.arduino_state_changed.connect(_on_arduino_state)
    conn.add(_form_row("Arduino", a_dot, a_port, QLabel("baud"), a_baud, a_connect_btn))

    # Telegram row
    t_dot = StatusDot("Telegram")
    t_dot.set(False, "미연결")
    t_token_btn = IconButton("토큰 설정", "send", size="sm")
    def _set_token() -> None:
        cur = mock_settings.telegram_token or ""
        text, ok = QInputDialog.getText(
            w, "Telegram 토큰", "Bot 토큰을 붙여넣기:", QLineEdit.Normal, cur,
        )
        if ok and text != mock_settings.telegram_token:
            mock_settings.telegram_token = text.strip()
            bus.settings_dirty.emit()
    t_token_btn.clicked.connect(_set_token)
    t_chat_btn = IconButton("채팅 ID", "send", size="sm")
    def _set_chat() -> None:
        cur = mock_settings.telegram_chat_id or ""
        text, ok = QInputDialog.getText(
            w, "Telegram 채팅 ID", "채팅 ID:", QLineEdit.Normal, cur,
        )
        if ok and text != mock_settings.telegram_chat_id:
            mock_settings.telegram_chat_id = text.strip()
            bus.settings_dirty.emit()
    t_chat_btn.clicked.connect(_set_chat)
    t_connect_btn = IconButton("연결", "send", size="sm", variant="primary")
    t_connect_btn.clicked.connect(lambda: bus.telegram_connect_request.emit())
    def _on_telegram_state(ok: bool, label: str) -> None:
        t_dot.set(ok, label)
        t_connect_btn.setText("해제" if ok else "연결")
    bus.telegram_state_changed.connect(_on_telegram_state)
    conn.add(_form_row("Telegram", t_dot, t_token_btn, t_chat_btn, t_connect_btn))

    # Capture FPS
    fps = QComboBox(); fps.addItems(["5", "10", "15", "20", "30"])
    # Cap any legacy 60-fps setting to 30 — beyond 30 the PrintWindow
    # path becomes the bottleneck on most machines, and low-end PCs
    # struggle to keep up regardless of our zero-alloc capture pool.
    cur_fps = min(int(mock_settings.capture_fps or 10), 30)
    fps.setCurrentText(str(cur_fps))
    if cur_fps != mock_settings.capture_fps:
        mock_settings.capture_fps = cur_fps
    def _on_fps(text: str) -> None:
        try:
            new = int(text)
        except ValueError:
            return
        if mock_settings.capture_fps != new:
            mock_settings.capture_fps = new
            bus.settings_dirty.emit()
    fps.currentTextChanged.connect(_on_fps)
    conn.add(_form_row("캡처 FPS", fps))

    # 인식 multi-scale 단계 — 1=고정 1.00× / 10=0.60×~1.60× 촘촘.
    # 단계↑ ⇒ 인식률↑ + CPU↑. 13×13 템플릿 한 프레임당 비용은
    # 1단계 ≈ 0.3ms, 10단계 ≈ 2.5ms 수준.
    steps = QComboBox(); steps.addItems([str(n) for n in range(1, 11)])
    cur_steps = max(1, min(10, int(getattr(mock_settings, "scale_steps", 3) or 3)))
    steps.setCurrentText(str(cur_steps))
    if cur_steps != getattr(mock_settings, "scale_steps", 3):
        mock_settings.scale_steps = cur_steps
    def _on_steps(text: str) -> None:
        try:
            new = int(text)
        except ValueError:
            return
        if mock_settings.scale_steps != new:
            mock_settings.scale_steps = new
            bus.settings_dirty.emit()
    steps.currentTextChanged.connect(_on_steps)
    steps.setToolTip(
        "인식 multi-scale 단계 수. 단계가 늘수록 작은/큰 게임창에서도 "
        "잘 잡지만 CPU 부담이 늘어남.\n"
        "기본 3 (1.00× ±8%) — 같은 해상도 사용자 권장\n"
        "7~9 — 창 크기를 자주 바꾸는 경우"
    )
    conn.add(_form_row("인식 스케일 단계", steps))

    v.addWidget(conn); v.addStretch(1)
    return w


def _panel_input_backend() -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    card = Card("입력 방식", subtitle="키 입력을 게임에 전달하는 방법")
    grp_row = QVBoxLayout(); grp_row.setSpacing(6)
    grp = QButtonGroup(w)
    cur = mock_settings.input_backend if hasattr(mock_settings, "input_backend") else "arduino"
    for backend_id, name, desc in _BACKENDS:
        r = QWidget(); rh = QHBoxLayout(r); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(10)
        rb = QRadioButton(name)
        rb.setChecked(backend_id == cur)
        grp.addButton(rb)
        d = QLabel(f"— {desc}")
        reactive(d, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")
        rh.addWidget(rb); rh.addWidget(d); rh.addStretch(1)
        grp_row.addWidget(r)

        def _on_toggle(checked: bool, _bid=backend_id) -> None:
            if not checked:
                return
            if hasattr(mock_settings, "input_backend"):
                if mock_settings.input_backend != _bid:
                    mock_settings.input_backend = _bid
                    bus.settings_dirty.emit()
                    # Tell AppWindow to hot-swap controller.input so the
                    # NEXT slot fire goes through the new backend (no
                    # need to restart the app).
                    bus.input_backend_changed.emit(_bid)
        rb.toggled.connect(_on_toggle)

    card.add(grp_row)
    v.addWidget(card)

    # ── Test panel — verify the game actually receives the key ──
    test = Card(
        "키 입력 테스트",
        subtitle="게임창이 활성 상태에서 클릭하면 선택된 백엔드로 키를 한 번 전송",
    )
    test_row = QHBoxLayout(); test_row.setSpacing(8)
    test_lbl = QLabel("테스트 키")
    reactive(test_lbl, lambda: f"color:{T.palette.text_secondary}; font-size:12px;")
    test_key = QLineEdit("1")
    test_key.setMaximumWidth(80)
    test_key.setPlaceholderText("키 (예: 1, F8, Enter)")
    test_row.addWidget(test_lbl); test_row.addWidget(test_key); test_row.addSpacing(12)

    for backend_id, name, _ in _BACKENDS:
        btn = IconButton(f"{name} 테스트", "play", size="sm")
        def _on_test(_=False, _bid=backend_id, _entry=test_key) -> None:
            key = _entry.text().strip() or "1"
            bus.test_input_request.emit(_bid, key)
        btn.clicked.connect(_on_test)
        test_row.addWidget(btn)
    test_row.addStretch(1)
    test.add(test_row)
    v.addWidget(test)

    v.addStretch(1)
    return w


def _panel_theme() -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    card = Card("테마", subtitle="라이트/다크 전환은 즉시 반영됩니다")
    grp = QButtonGroup(w)
    th_row = QVBoxLayout(); th_row.setSpacing(8)

    options = [
        ("auto",     "Auto",    "시스템 다크모드를 따라갑니다"),
        ("graphite", "Graphite","다크 — cobalt blue 액센트"),
        ("paper",    "Paper",   "라이트 — 같은 액센트의 라이트 버전"),
    ]
    cur_theme = mock_settings.theme.lower() if mock_settings.theme else "graphite"
    if cur_theme not in {"auto", "graphite", "paper"}:
        cur_theme = "graphite"
    rbs: dict[str, QRadioButton] = {}
    for tid, label, desc in options:
        row = QWidget(); rh = QHBoxLayout(row); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(10)
        rb = QRadioButton(label); rb.setChecked(tid == cur_theme); grp.addButton(rb)
        d = QLabel(f"— {desc}")
        reactive(d, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")
        rh.addWidget(rb); rh.addWidget(d); rh.addStretch(1)
        th_row.addWidget(row)
        rbs[tid] = rb

    def _apply(tid: str) -> None:
        app = QApplication.instance()
        if app is not None:
            design_themes.apply_theme(app, tid)
        if mock_settings.theme != tid:
            mock_settings.theme = tid
            bus.settings_dirty.emit()

    for tid, rb in rbs.items():
        rb.toggled.connect(lambda checked, _t=tid: _apply(_t) if checked else None)

    card.add(th_row)
    v.addWidget(card); v.addStretch(1)
    return w


def _panel_accessibility() -> QWidget:
    from quickcast.ui.design.prefs import prefs

    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    card = Card("접근성", subtitle="모든 항목 즉시 반영됩니다")

    cb_hc = QCheckBox("고대비 (테두리 굵게 + 텍스트 강하게)")
    cb_hc.setChecked(prefs.high_contrast)
    cb_lf = QCheckBox("큰 글꼴 (140%)")
    cb_lf.setChecked(prefs.large_font)
    cb_ma = QCheckBox("단색 액센트 (R/G 색맹 보조)")
    cb_ma.setChecked(prefs.mono_accent)
    cb_rm = QCheckBox("애니메이션 줄이기")
    cb_rm.setChecked(prefs.reduce_motion)

    def _refresh() -> None:
        app = QApplication.instance()
        if app is not None:
            design_themes.reapply_current(app)

    cb_hc.toggled.connect(lambda v: (prefs.set(high_contrast=v), _refresh()))
    cb_lf.toggled.connect(lambda v: (prefs.set(large_font=v), _refresh()))
    cb_ma.toggled.connect(lambda v: (prefs.set(mono_accent=v), _refresh()))
    cb_rm.toggled.connect(lambda v: (prefs.set(reduce_motion=v), _refresh()))

    card.add(cb_hc); card.add(cb_lf); card.add(cb_ma); card.add(cb_rm)
    v.addWidget(card); v.addStretch(1)
    return w


def _panel_data() -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    dt = Card("데이터", subtitle="설정과 슬롯/알람을 파일로 백업·복원")
    dt_row = QHBoxLayout(); dt_row.setSpacing(10)
    import_btn = IconButton("JSON 파일에서 가져오기", "folder-open")
    export_btn = IconButton("현재 설정 내보내기", "save", variant="primary")

    def _do_import() -> None:
        path, _ = QFileDialog.getOpenFileName(
            w, "설정 가져오기", "", "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            new_settings = Settings.load(Path(path))
        except Exception as exc:
            QMessageBox.warning(w, "가져오기 실패", str(exc))
            return
        # Mutate mock_settings IN PLACE so all sections see the new values.
        for fname in type(new_settings).model_fields:
            setattr(mock_settings, fname, getattr(new_settings, fname))
        # ── slot_state 동기화 ──
        # 사이드바 / 대시보드 슬롯 행은 mock_settings.slots 가 아니라
        # 전역 slot_state 캐시에서 라벨/키/사용여부를 읽기 때문에, import
        # 직후 slot_state 도 함께 새 값으로 다시 채워야 슬롯 이름이 바뀐다.
        try:
            from quickcast.ui.sections._mock_state import slot_state
            slot_state._on.clear()
            slot_state._label.clear()
            slot_state._key.clear()
            for sid, slot in mock_settings.slots.items():
                slot_state._on[sid] = bool(getattr(slot, "use", False))
                slot_state._label[sid] = str(getattr(slot, "label", sid))
                slot_state._key[sid] = str(getattr(slot, "key", "0"))
        except Exception:
            logger.exception("import: slot_state 동기화 실패")
        # ── 즉시 디스크 저장 ──
        # 평소 settings_dirty 는 debounced 저장이지만, import 결과는
        # 사용자가 재시작을 선택할 가능성이 높으므로 곧바로 디스크에
        # 반영해서 재시작 후에도 같은 상태로 부팅되도록 한다.
        try:
            mock_settings.save()
        except Exception:
            logger.exception("import: 즉시 저장 실패 — debounce 경로로 폴백")
        bus.settings_dirty.emit()
        bus.slot_list_changed.emit(); bus.alarm_list_changed.emit()
        # ── 재시작 프롬프트 ──
        # 대다수 위젯은 빌드 시점의 값을 캐시하고 있어 import 만으로는
        # 슬라이더/스테퍼/체크박스가 갱신되지 않는다 (데이터는 정확히
        # 반영되지만 화면이 옛 값). 재시작이 가장 확실하므로 사용자에게
        # 명시적으로 안내한다.
        ret = QMessageBox.question(
            w, "가져오기 완료",
            "설정 파일을 정상적으로 가져왔습니다.\n"
            "(슬롯/복귀/PK/물약/ROI/마을/오버레이 등 모든 값이 디스크에 저장됨)\n\n"
            "현재 실행 중인 위젯에 전부 반영하려면 프로그램을 다시 시작해야 합니다.\n"
            "지금 다시 시작하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ret == QMessageBox.Yes:
            _restart_app()

    def _restart_app() -> None:
        """현재 프로세스를 종료하고 동일 인자로 새로 띄운다."""
        try:
            import sys
            from PySide6.QtCore import QProcess
            # frozen(.exe)이면 sys.executable 자체가 quickcast.exe.
            # dev 실행이면 sys.executable + sys.argv 조합으로 재실행.
            if getattr(sys, "frozen", False):
                QProcess.startDetached(sys.executable, sys.argv[1:])
            else:
                QProcess.startDetached(sys.executable, sys.argv)
            QApplication.quit()
        except Exception:
            logger.exception("auto-restart 실패 — 수동으로 재시작 필요")
            QMessageBox.warning(w, "재시작 실패",
                "자동 재시작이 실패했습니다. 프로그램을 수동으로 다시 시작해주세요.")

    def _do_export() -> None:
        path, _ = QFileDialog.getSaveFileName(
            w, "설정 내보내기", "quickcast_export.json", "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            mock_settings.save(Path(path))
            QMessageBox.information(w, "내보내기 완료", f"저장 위치:\n{path}")
        except Exception as exc:
            QMessageBox.warning(w, "내보내기 실패", str(exc))

    import_btn.clicked.connect(_do_import)
    export_btn.clicked.connect(_do_export)
    dt_row.addWidget(import_btn); dt_row.addWidget(export_btn); dt_row.addStretch(1)
    dt.add(dt_row)

    info = QLabel("userdata.json 위치: quickcast/data/userdata.json")
    reactive(info, lambda: f"color:{T.palette.text_tertiary}; font-size:12px;")
    dt.add(info)
    v.addWidget(dt); v.addStretch(1)
    return w


_BMC_URL = "https://buymeacoffee.com/snjdevs"


def _panel_about() -> QWidget:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from quickcast import __version__ as _ver

    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)
    card = Card("정보")
    title = QLabel("QuickCast")
    f = QFont(); f.setBold(True); f.setPointSize(15); title.setFont(f)
    card.add(title)
    ver_lbl = QLabel(f"버전 v{_ver}")
    reactive(ver_lbl, lambda: f"color:{T.palette.text_secondary};")
    card.add(ver_lbl)
    card.add(QLabel("Python · PySide6 · OpenCV · mss · pyserial"))
    copy = QLabel("\xa9 2026 S&J Devs")
    reactive(copy, lambda: f"color:{T.palette.text_secondary};")
    card.add(copy)

    # ── 업데이트 확인 ──
    # 자동 6시간 폴링과 별개로 사용자가 수동 트리거할 수 있게. 결과는
    # UpdateChecker가 emit하는 check_finished 시그널로 상태 라벨에 표시.
    upd_row = QHBoxLayout(); upd_row.setSpacing(10)
    upd_btn = IconButton("업데이트 확인", "refresh-cw", size="sm")
    upd_status = QLabel("")
    reactive(upd_status, lambda: f"color:{T.palette.text_tertiary}; font-size:12px;")

    def _do_check() -> None:
        from quickcast.utils.update_check import UpdateChecker, is_newer
        from quickcast import __version__ as cur
        # 일회용 체커 — 메인 윈도우의 폴링과 별개로 사용자 클릭에 응답.
        # only_in_frozen=False 로 dev 모드에서도 동작 (단 1.0.3 같은
        # tagged 버전을 박아두지 않으면 비교 결과가 의미 없음).
        checker = UpdateChecker(w, only_in_frozen=False)
        upd_status.setText("확인 중…")
        def _done(ok: bool, msg: str) -> None:
            if ok and msg.startswith("새 버전"):
                upd_status.setText(f"✓ {msg}")
            elif ok:
                upd_status.setText(f"✓ 최신 버전 (v{cur})")
            else:
                upd_status.setText(f"✗ 확인 실패 — {msg}")
            try:
                checker.deleteLater()
            except Exception:
                pass
        def _found(c: str, latest: str, url: str) -> None:
            ret = QMessageBox.question(
                w, "QuickCast 업데이트",
                f"새 버전 {latest}가 공개되었습니다.\n"
                f"현재 버전: v{c}\n\n"
                "다운로드 페이지를 지금 열까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if ret == QMessageBox.Yes and url:
                QDesktopServices.openUrl(QUrl(url))
        checker.check_finished.connect(_done)
        checker.update_available.connect(_found)
        checker.check_now()
    upd_btn.clicked.connect(_do_check)
    upd_row.addWidget(upd_btn); upd_row.addWidget(upd_status); upd_row.addStretch(1)
    card.add(upd_row)

    v.addWidget(card)

    donate = Card("후원")
    msg = QLabel("이 매크로가 도움이 되셨다면 커피 한 잔 어떠세요? ☕")
    reactive(msg, lambda: f"color:{T.palette.text_secondary};")
    donate.add(msg)

    btn = QPushButton("☕  Buy Me a Coffee")
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(36)
    reactive(btn, lambda: (
        f"QPushButton {{ background:#FFDD00; color:#000; border:none; "
        f"border-radius:8px; font-weight:600; padding:6px 16px; }}"
        f"QPushButton:hover {{ background:#FFE54C; }}"
    ))
    btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(_BMC_URL)))
    btn_row = QHBoxLayout(); btn_row.setContentsMargins(0, 0, 0, 0); btn_row.setSpacing(8)
    btn_row.addWidget(btn); btn_row.addStretch(1)
    donate.add(btn_row)

    link = QLabel(_BMC_URL)
    link.setTextInteractionFlags(Qt.TextSelectableByMouse)
    reactive(link, lambda: f"color:{T.palette.text_tertiary}; font-size:12px;")
    donate.add(link)

    v.addWidget(donate); v.addStretch(1)
    return w


PANELS = [
    ("connection", "연결",      _panel_connection),
    ("input",      "입력 방식", _panel_input_backend),
    ("theme",      "테마",      _panel_theme),
    ("a11y",       "접근성",    _panel_accessibility),
    ("data",       "데이터",    _panel_data),
    ("about",      "정보",      _panel_about),
]


def make_settings() -> tuple[QWidget, QWidget]:
    from PySide6.QtWidgets import QScrollArea, QFrame
    main = QScrollArea()
    main.setWidgetResizable(True)
    main.setFrameShape(QFrame.NoFrame)
    main.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    main.setWidget(inner)
    v = QVBoxLayout(inner); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(14)

    title_lbl = QLabel("연결")
    f = QFont(); f.setBold(True); f.setPointSize(18); title_lbl.setFont(f)
    sub_lbl = QLabel("Arduino / Telegram 연결 상태와 토큰 설정")
    reactive(sub_lbl, lambda: f"color:{T.palette.text_secondary};")
    head_box = QVBoxLayout(); head_box.setContentsMargins(0, 0, 0, 0); head_box.setSpacing(0)
    head_box.addWidget(title_lbl); head_box.addWidget(sub_lbl)
    v.addLayout(head_box)

    stack = QStackedWidget()
    panels_by_id: dict[str, QWidget] = {}
    for pid, name, factory in PANELS:
        panel = factory()
        stack.addWidget(panel)
        panels_by_id[pid] = panel
    v.addWidget(stack, stretch=1)

    sub_titles = {
        "connection": ("연결",      "Arduino / Telegram 연결 상태와 토큰 설정"),
        "input":      ("입력 방식", "키 입력을 게임에 전달하는 방법 선택"),
        "theme":      ("테마",      "라이트/다크 즉시 전환"),
        "a11y":       ("접근성",    "큰 글꼴 · 색맹 보조 · 애니메이션 줄이기"),
        "data":       ("데이터",    "설정·슬롯·알람 파일 백업/복원"),
        "about":      ("정보",      "버전 및 라이선스"),
    }

    sidebar = QWidget()
    sv = QVBoxLayout(sidebar); sv.setContentsMargins(8, 6, 8, 8); sv.setSpacing(2)
    head = QLabel("설정")
    reactive(head, lambda: f"color:{T.palette.text_secondary}; padding:6px 10px;")
    sv.addWidget(head)

    rows: dict[str, QPushButton] = {}
    selected_id = {"v": "connection"}

    def _refresh_rows() -> None:
        for rid, btn in rows.items():
            btn.setStyleSheet(_row_qss(rid == selected_id["v"]))

    def _select(pid: str) -> None:
        selected_id["v"] = pid
        stack.setCurrentWidget(panels_by_id[pid])
        t, s = sub_titles[pid]
        title_lbl.setText(t); sub_lbl.setText(s)
        _refresh_rows()

    bus.theme_changed.connect(_refresh_rows)

    for pid, name, _ in PANELS:
        row = QPushButton(name)
        row.setMinimumHeight(32)
        row.setCursor(Qt.PointingHandCursor)
        row.setFlat(True)
        row.setStyleSheet(_row_qss(pid == "connection"))
        row.clicked.connect(lambda _checked=False, _p=pid: _select(_p))
        rows[pid] = row
        sv.addWidget(row)
    sv.addStretch(1)

    return sidebar, main


def _row_qss(selected: bool) -> str:
    p = T.palette
    if selected:
        return (
            f"QPushButton {{ text-align:left; padding:6px 10px; border:none;"
            f" background:{p.accent_subtle}; color:{p.text_primary};"
            f" border-radius:6px; font-weight:600; }}"
        )
    return (
        f"QPushButton {{ text-align:left; padding:6px 10px; border:none;"
        f" background:transparent; color:{p.text_primary}; border-radius:6px; }}"
        f"QPushButton:hover {{ background:{p.bg_hover}; }}"
    )


__all__ = ["make_settings"]
