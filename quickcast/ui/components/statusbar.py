"""Bottom status bar — connection dots + HP/MP minibar + master + ⌘K hint.

Always visible. Reads the latest snapshot via `update_*` calls.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from quickcast.ui.components.status_dot import StatusDot
from quickcast.ui.design.signals import bus
from quickcast.ui.design.tokens import T


HEIGHT = 28


class _MiniMeter(QWidget):
    """Tiny inline HP/MP indicator — colored block + label + value."""

    def __init__(self, label: str, fill_token: str = "hp_fill") -> None:
        super().__init__()
        self._label = label
        self._fill_token = fill_token
        self._value = 0
        self.setFixedHeight(HEIGHT)
        self.setFixedWidth(110)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        bus.theme_changed.connect(self.update)

    def set_value(self, v: int) -> None:
        v = max(0, min(100, int(v)))
        if v != self._value:
            self._value = v
            self.update()

    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QBrush, QColor, QPainter
        from PySide6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = T.palette
        # Track
        track = QRectF(38, 9, self.width() - 46, 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(pal.bg_input)))
        p.drawRoundedRect(track, 5, 5)
        # Fill
        fill_color = QColor(getattr(pal, self._fill_token, pal.accent_default))
        fill_w = (track.width()) * (self._value / 100.0)
        p.setBrush(QBrush(fill_color))
        p.drawRoundedRect(QRectF(track.x(), track.y(), fill_w, track.height()), 5, 5)
        # Label + value — match the rest of the StatusBar (11px equivalent).
        p.setPen(QColor(pal.text_secondary))
        f = QFont(T.type.mono); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(0, 0, 36, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self._label)
        p.setPen(QColor(pal.text_primary))
        p.drawText(QRectF(0, 0, self.width() - 4, self.height()),
                   Qt.AlignVCenter | Qt.AlignRight, f"{self._value}%")


class StatusBar(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusBar")
        self.setFixedHeight(HEIGHT)

        h = QHBoxLayout(self); h.setContentsMargins(12, 0, 12, 0); h.setSpacing(16)

        # Left — connection dots
        self.dot_capture = StatusDot("캡처")
        self.dot_arduino = StatusDot("Arduino")
        self.dot_telegram = StatusDot("Telegram")
        for d in (self.dot_capture, self.dot_arduino, self.dot_telegram):
            h.addWidget(d)

        h.addStretch(1)

        # Centre — HP/MP minibars + PK / Potion / FPS chips
        self.hp_meter = _MiniMeter("HP", fill_token="hp_fill")
        self.mp_meter = _MiniMeter("MP", fill_token="mp_fill")
        h.addWidget(self.hp_meter); h.addWidget(self.mp_meter)

        self.pk_lbl = QLabel("전투 보통")
        self.po_lbl = QLabel("물약 있음")
        self.fps_lbl = QLabel("FPS 60.0")
        for lbl in (self.pk_lbl, self.po_lbl, self.fps_lbl):
            lbl.setStyleSheet(
                f"color:{T.palette.text_secondary}; font-family:{T.type.mono};"
                f" font-size:11px; padding:0 6px;"
            )
            h.addWidget(lbl)

        h.addStretch(1)

        # Right — Master indicator. Ctrl+K hint removed (use ? button or shortcut).
        self.master_lbl = QLabel("Master OFF")
        self.master_lbl.setStyleSheet(f"color:{T.palette.text_tertiary}; font-size:11px;")
        h.addWidget(self.master_lbl)

        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        # Master colour reflects state via inline style
        pass  # set in update_master

    # ───────── public API ─────────
    def update_capture(self, ok: bool, text: str = "") -> None:
        self.dot_capture.set(ok, text or ("연결됨" if ok else "미연결"))

    def update_arduino(self, ok: bool, text: str = "") -> None:
        self.dot_arduino.set(ok, text or ("연결됨" if ok else "미연결"))

    def update_telegram(self, ok: bool, text: str = "") -> None:
        self.dot_telegram.set(ok, text or ("연결됨" if ok else "미연결"))

    def update_hp(self, v: int) -> None:
        self.hp_meter.set_value(v)

    def update_mp(self, v: int) -> None:
        self.mp_meter.set_value(v)

    def update_master(self, on: bool) -> None:
        col = T.palette.state_success if on else T.palette.text_tertiary
        self.master_lbl.setText(f"Master {'ON' if on else 'OFF'}")
        self.master_lbl.setStyleSheet(f"color:{col}; font-weight:600; font-size:11px;")

    def update_pk(self, detected: bool) -> None:
        col = T.palette.state_danger if detected else T.palette.text_secondary
        self.pk_lbl.setText("전투 PK" if detected else "전투 보통")
        self.pk_lbl.setStyleSheet(
            f"color:{col}; font-family:{T.type.mono}; font-size:11px;"
            f" font-weight:{700 if detected else 400}; padding:0 6px;"
        )

    def update_potion(self, empty: bool) -> None:
        col = T.palette.state_warning if empty else T.palette.text_secondary
        self.po_lbl.setText("물약 없음" if empty else "물약 있음")
        self.po_lbl.setStyleSheet(
            f"color:{col}; font-family:{T.type.mono}; font-size:11px;"
            f" font-weight:{700 if empty else 400}; padding:0 6px;"
        )

    def update_fps(self, fps: float) -> None:
        self.fps_lbl.setText(f"FPS {fps:.1f}")


__all__ = ["StatusBar"]
