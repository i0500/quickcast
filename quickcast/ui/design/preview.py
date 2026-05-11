"""Design system preview — all tokens/icons/sample components in one window.

Run with the venv to iterate on design without touching the macro app:

    .venv\\Scripts\\python.exe -m quickcast.ui.design.preview

Hotkeys:
    Ctrl+T  — toggle Graphite ↔ Paper
    Ctrl+R  — re-apply current theme (use after editing tokens.py / qss_template.py)
    Ctrl+Q  — quit
"""
from __future__ import annotations

import importlib
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

# Lazy imports of design system so reload works
def _design_modules():
    """Re-import all design modules; return refreshed handles."""
    from quickcast.ui.design import tokens as t_mod
    from quickcast.ui.design import qss_template as q_mod
    from quickcast.ui.design import themes as th_mod
    from quickcast.ui.design import icons as ic_mod
    importlib.reload(t_mod)
    importlib.reload(q_mod)
    importlib.reload(th_mod)
    importlib.reload(ic_mod)
    return t_mod, q_mod, th_mod, ic_mod


def _hex_swatch(label: str, color: str) -> QWidget:
    """Color chip + label + hex value."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
    chip = QFrame(); chip.setFixedSize(28, 28)
    chip.setStyleSheet(
        f"background:{color}; border:1px solid rgba(255,255,255,0.10);"
        " border-radius:4px;"
    )
    text = QLabel(f"<b>{label}</b><br><span style='color:#9aa4b0; font-family:monospace'>{color}</span>")
    text.setTextFormat(Qt.RichText)
    h.addWidget(chip); h.addWidget(text); h.addStretch(1)
    return w


def _section(title: str) -> QLabel:
    lbl = QLabel(title)
    f = QFont(); f.setBold(True); f.setPointSize(13); lbl.setFont(f)
    lbl.setProperty("role", "section")
    lbl.setStyleSheet("padding-top:12px;")
    return lbl


def _card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame(); card.setObjectName("card")
    lay = QVBoxLayout(card); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(10)
    if title:
        h = QLabel(title); f = QFont(); f.setBold(True); f.setPointSize(12); h.setFont(f)
        lay.addWidget(h)
    return card, lay


class PreviewWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("QuickCast — Design Preview")
        self.resize(1180, 860)
        self._theme_id = "graphite"
        self._build()
        self._apply_theme(self._theme_id)
        self._wire_shortcuts()

    # ───────── shortcuts ─────────
    def _wire_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._toggle_theme)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self._reload_design)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)

    def _toggle_theme(self) -> None:
        self._theme_id = "paper" if self._theme_id == "graphite" else "graphite"
        self._apply_theme(self._theme_id)

    def _apply_theme(self, theme_id: str) -> None:
        _, _, themes_mod, _ = _design_modules()
        themes_mod.apply_theme(QApplication.instance(), theme_id)
        self.setWindowTitle(f"QuickCast — Design Preview ({theme_id})")
        # Re-render anything that reads tokens directly (custom-paint preview)
        self._refresh_swatches()

    def _reload_design(self) -> None:
        """Re-import tokens/qss/themes/icons, then re-apply current theme.
        Use this after editing tokens.py or qss_template.py to see changes."""
        self._apply_theme(self._theme_id)
        self.statusBar().showMessage("design system reloaded — Ctrl+T to flip theme", 2500)

    # ───────── ui ─────────
    def _build(self) -> None:
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget()
        root = QVBoxLayout(inner); root.setContentsMargins(20, 20, 20, 20); root.setSpacing(14)

        # Header
        title = QLabel("Design System Preview")
        f = QFont(); f.setBold(True); f.setPointSize(20); title.setFont(f)
        sub = QLabel(
            "Ctrl+T: 테마 토글  ·  Ctrl+R: tokens.py / qss_template.py 재로드  ·  Ctrl+Q: 종료"
        )
        sub.setStyleSheet("color:#9aa4b0;")
        root.addWidget(title); root.addWidget(sub)

        # Color swatches
        root.addWidget(_section("Color tokens"))
        self._swatch_card, self._swatch_lay = _card("")
        self._build_swatches()
        root.addWidget(self._swatch_card)

        # Buttons
        root.addWidget(_section("Buttons"))
        btn_card, btn_lay = _card("")
        b_row = QHBoxLayout(); b_row.setSpacing(8)
        for label, variant in [("Default", None), ("Primary", "primary"),
                                ("Ghost", "ghost"), ("Danger", "danger")]:
            b = QPushButton(label)
            if variant:
                b.setProperty("variant", variant)
            b_row.addWidget(b)
        b_row.addStretch(1)
        btn_lay.addLayout(b_row)
        # Tool buttons (icon-style)
        from quickcast.ui.design.icons import Icon
        ic_row = QHBoxLayout(); ic_row.setSpacing(6)
        for nm in ["plus", "minus", "x", "search", "settings", "bell", "save",
                   "trash-2", "play", "pause", "moon", "sun"]:
            tb = QToolButton()
            tb.setIcon(Icon.get(nm, 16))
            tb.setIconSize(self._icon_size(16))
            tb.setToolTip(nm)
            ic_row.addWidget(tb)
        ic_row.addStretch(1)
        btn_lay.addLayout(ic_row)
        root.addWidget(btn_card)

        # Inputs
        root.addWidget(_section("Inputs"))
        in_card, in_lay = _card("")
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        # row 0: line edit
        grid.addWidget(QLabel("LineEdit:"), 0, 0)
        le = QLineEdit("리니지W | 캐릭명"); le.setMinimumWidth(220); grid.addWidget(le, 0, 1)
        # row 1: spin
        grid.addWidget(QLabel("SpinBox:"), 1, 0)
        sb = QSpinBox(); sb.setRange(0, 100); sb.setValue(75); sb.setSuffix("%"); grid.addWidget(sb, 1, 1)
        # row 2: combo
        grid.addWidget(QLabel("ComboBox:"), 2, 0)
        cb = QComboBox(); cb.addItems(["Arduino HID", "PostMessage", "AttachInput"]); grid.addWidget(cb, 2, 1)
        # row 3: checkbox
        grid.addWidget(QLabel("CheckBox:"), 3, 0)
        ck = QCheckBox("수라 모드 (HP/MP 좌표 자동 보정)"); ck.setChecked(True); grid.addWidget(ck, 3, 1)
        # row 4: progress
        grid.addWidget(QLabel("Progress:"), 4, 0)
        pb = QProgressBar(); pb.setRange(0, 100); pb.setValue(64); pb.setFormat("64%"); grid.addWidget(pb, 4, 1)
        # row 5: slider
        grid.addWidget(QLabel("Slider:"), 5, 0)
        sl = QSlider(Qt.Horizontal); sl.setRange(0, 100); sl.setValue(33); grid.addWidget(sl, 5, 1)
        in_lay.addLayout(grid)
        root.addWidget(in_card)

        # Existing custom widgets
        root.addWidget(_section("Custom widgets (existing, will be tokenised in Phase 4)"))
        cw_card, cw_lay = _card("")
        try:
            from quickcast.ui.ios_toggle import IOSToggle
            from quickcast.ui.range_slider import RangeSlider
            from quickcast.ui.stepper import Stepper
            row1 = QHBoxLayout(); row1.setSpacing(12)
            row1.addWidget(QLabel("IOSToggle:"))
            t1 = IOSToggle(width=52, height=28); t1.set_state(True, animate=False)
            t2 = IOSToggle(width=52, height=28)
            row1.addWidget(t1); row1.addWidget(t2); row1.addStretch(1)
            cw_lay.addLayout(row1)

            row2 = QHBoxLayout(); row2.setSpacing(12)
            row2.addWidget(QLabel("Stepper:"))
            row2.addWidget(Stepper(75, 0, 100, 1, 0, "%", width=110))
            row2.addWidget(Stepper(0.5, 0, 10, 0.1, 2, "초", width=120))
            row2.addStretch(1)
            cw_lay.addLayout(row2)

            row3 = QHBoxLayout(); row3.setSpacing(12)
            row3.addWidget(QLabel("RangeSlider:"))
            rs = RangeSlider(0, 100, 20, 80, fill_color="#5B8DEF")
            rs.setMinimumWidth(280)
            row3.addWidget(rs); row3.addStretch(1)
            cw_lay.addLayout(row3)
        except ImportError as e:
            cw_lay.addWidget(QLabel(f"(custom widgets not loadable: {e})"))
        root.addWidget(cw_card)

        # Icon gallery
        root.addWidget(_section("Icon library (Lucide subset)"))
        ic_card, ic_lay = _card("")
        gal = QGridLayout(); gal.setHorizontalSpacing(8); gal.setVerticalSpacing(8)
        names = Icon.names()
        for idx, nm in enumerate(names):
            r, c = divmod(idx, 8)
            cell = QWidget(); v = QVBoxLayout(cell); v.setContentsMargins(4, 4, 4, 4); v.setSpacing(4)
            tb = QToolButton(); tb.setIcon(Icon.get(nm, 24)); tb.setIconSize(self._icon_size(24))
            tb.setEnabled(True)
            v.addWidget(tb, alignment=Qt.AlignCenter)
            lbl = QLabel(nm); lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("font-size:11px;")  # let theme color it
            v.addWidget(lbl)
            gal.addWidget(cell, r, c)
        ic_lay.addLayout(gal)
        root.addWidget(ic_card)

        root.addStretch(1)
        scroll.setWidget(inner)
        self.setCentralWidget(scroll)
        self.statusBar().showMessage("Ready — Ctrl+T 테마 토글, Ctrl+R 재로드", 2500)

    def _icon_size(self, n: int):
        from PySide6.QtCore import QSize
        return QSize(n, n)

    def _build_swatches(self) -> None:
        # Cleared+rebuilt on theme change so colours match active palette
        from quickcast.ui.design.tokens import T
        layout = self._swatch_lay
        # Clear
        while layout.count():
            it = layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()

        p = T.palette
        groups = [
            ("Surfaces", [
                ("bg.canvas", p.bg_canvas), ("bg.surface", p.bg_surface),
                ("bg.elevated", p.bg_elevated), ("bg.input", p.bg_input),
            ]),
            ("Text", [
                ("text.primary", p.text_primary), ("text.secondary", p.text_secondary),
                ("text.tertiary", p.text_tertiary), ("text.disabled", p.text_disabled),
            ]),
            ("Accent / state", [
                ("accent.default", p.accent_default), ("state.success", p.state_success),
                ("state.warning", p.state_warning), ("state.danger", p.state_danger),
            ]),
            ("Domain", [
                ("hp.fill", p.hp_fill), ("mp.fill", p.mp_fill),
                ("pk.accent", p.pk_accent), ("potion.accent", p.potion_accent),
            ]),
        ]
        for name, items in groups:
            layout.addWidget(_section(name))
            row = QGridLayout(); row.setHorizontalSpacing(20); row.setVerticalSpacing(6)
            for idx, (lbl, col) in enumerate(items):
                row.addWidget(_hex_swatch(lbl, col), idx // 4, idx % 4)
            container = QWidget(); container.setLayout(row)
            layout.addWidget(container)

    def _refresh_swatches(self) -> None:
        self._build_swatches()


def main() -> None:
    from quickcast.utils.logger import setup as setup_logging
    setup_logging()
    app = QApplication(sys.argv)
    # Register bundled fonts if available
    try:
        from quickcast.ui.design import fonts as design_fonts
        design_fonts.register()
    except Exception:
        pass
    w = PreviewWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
