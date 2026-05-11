"""Custom alarm popup — auto-close countdown + repeat-interval display.

Mirrors the original HTML's `showCustomAlarmPopup`: a borderless modal
on top of the macro window with a live countdown until auto-close, and
an optional repeating beep at the configured interval.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from quickcast.notify.sound import play_alarm


class AlarmPopup(QDialog):
    """Auto-closing modal dialog that shows alarm details and beeps."""

    closed = Signal()

    def __init__(
        self,
        title: str,
        time_str: str,
        info_html: str = "",
        auto_close_minutes: int = 10,
        repeat_minutes: int = 1,
        parent=None,
    ) -> None:
        # IMPORTANT: parent=None for a frameless top-level dialog so
        # Windows treats it as its own top-level window. Passing a
        # parent (especially a frameless main window) makes Qt place
        # the dialog inside the parent's coordinate space — which on
        # some setups lands off-screen or under the main window where
        # the user never sees it.
        super().__init__(None)
        # Qt.Tool was causing the popup to silently fail to draw on
        # certain Windows setups (the OS treats Tool windows differently
        # for top-level visibility when no parent is focused). Plain
        # Qt.Window + StaysOnTop + Frameless behaves like a normal
        # always-on-top dialog and shows reliably.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
        )
        # No translucency — we want a solid filled QWidget so the OS
        # always allocates a visible surface even when DWM compositing
        # hiccups. The styled label inside still renders the rounded
        # gradient look.
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setModal(False)
        self.resize(420, 240)
        self._reposition_top_right()

        self._auto_close_ms = max(1000, auto_close_minutes * 60_000)
        self._repeat_ms = max(0, repeat_minutes * 60_000)
        self._remaining_ms = self._auto_close_ms

        self._build(title, time_str, info_html)

        # Sound — respect user-selected preset from settings.alarm_sound.
        sid = self._read_alarm_sound()
        self._sound_enabled = sid != "off"
        self._sound_id = sid
        if self._sound_enabled:
            play_alarm(times=5, interval_s=2.0, sound_id=sid)

        self._countdown = QTimer(self); self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._tick); self._countdown.start()

        if self._repeat_ms > 0:
            self._repeater = QTimer(self); self._repeater.setInterval(self._repeat_ms)
            if self._sound_enabled:
                self._repeater.timeout.connect(
                    lambda: play_alarm(times=5, interval_s=2.0, sound_id=self._sound_id)
                )
            self._repeater.start()
        else:
            self._repeater = None

    @staticmethod
    def _read_alarm_sound() -> str:
        try:
            from quickcast.ui.sections._mock_state import mock_settings
            return getattr(mock_settings, "alarm_sound", "default") or "default"
        except Exception:
            return "default"

    def _build(self, title: str, time_str: str, info_html: str) -> None:
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        frame = QLabel(self); frame.setObjectName("alarmFrame")
        frame.setStyleSheet(
            "QLabel#alarmFrame {"
            " background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "    stop:0 rgba(20,25,40,250), stop:1 rgba(40,30,80,250));"
            " border:2px solid #fbbf24; border-radius:14px;"
            "}"
        )
        outer.addWidget(frame)

        v = QVBoxLayout(frame); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(8)

        bell = QLabel("⏰"); bell.setAlignment(Qt.AlignCenter)
        bf = QFont(); bf.setPointSize(28); bell.setFont(bf)
        v.addWidget(bell)

        t_lbl = QLabel(title); t_lbl.setAlignment(Qt.AlignCenter)
        tf = QFont(); tf.setBold(True); tf.setPointSize(15)
        t_lbl.setFont(tf); t_lbl.setStyleSheet("color:white;")
        v.addWidget(t_lbl)

        time_lbl = QLabel(time_str); time_lbl.setAlignment(Qt.AlignCenter)
        time_lbl.setStyleSheet("color:#fbbf24; font-size:13px;")
        v.addWidget(time_lbl)

        if info_html:
            info_lbl = QLabel(info_html); info_lbl.setAlignment(Qt.AlignCenter)
            info_lbl.setStyleSheet("color:rgba(255,255,255,0.7); font-size:11px;")
            v.addWidget(info_lbl)

        self.timer_lbl = QLabel(""); self.timer_lbl.setAlignment(Qt.AlignCenter)
        self.timer_lbl.setStyleSheet("color:rgba(255,255,255,0.6); font-size:11px;")
        v.addWidget(self.timer_lbl)

        btns = QHBoxLayout()
        close_btn = QPushButton("닫기")
        close_btn.setStyleSheet(
            "QPushButton { background:#ef5350; color:white; border:none;"
            " padding:8px 18px; border-radius:8px; font-weight:700; }"
            "QPushButton:hover { background:#d32f2f; }"
        )
        close_btn.clicked.connect(self.close)
        btns.addStretch(1); btns.addWidget(close_btn); btns.addStretch(1)
        v.addLayout(btns)

    def _tick(self) -> None:
        self._remaining_ms -= 1000
        if self._remaining_ms <= 0:
            self.close(); return
        total = self._remaining_ms // 1000
        m, s = divmod(total, 60)
        next_total = self._repeat_ms // 1000
        nm, ns = divmod(next_total, 60)
        if self._repeat_ms > 0:
            self.timer_lbl.setText(
                f"⏱️ 자동닫기 {m:02d}:{s:02d}  |  📢 반복간격 {nm:02d}:{ns:02d}"
            )
        else:
            self.timer_lbl.setText(f"⏱️ 자동닫기 {m:02d}:{s:02d}")

    def _reposition_top_right(self) -> None:
        """Place the popup near the top-right of the primary screen so
        it's visible regardless of the main window's geometry. Falls
        back to centre when the screen API isn't available."""
        try:
            from PySide6.QtGui import QGuiApplication
            scr = QGuiApplication.primaryScreen()
            if scr is None:
                return
            geo = scr.availableGeometry()
            x = geo.right() - self.width() - 24
            y = geo.top() + 80
            self.move(int(x), int(y))
        except Exception:
            pass

    def showEvent(self, e) -> None:
        super().showEvent(e)
        # Force focus to the top so Windows / Qt don't bury it under
        # the (possibly fullscreen) game window.
        self.raise_()
        self.activateWindow()
        from quickcast.utils.logger import logger
        logger.info(
            f"AlarmPopup shown at ({self.x()},{self.y()}) "
            f"{self.width()}x{self.height()}, visible={self.isVisible()}"
        )

    def closeEvent(self, e) -> None:
        self._countdown.stop()
        if self._repeater:
            self._repeater.stop()
        self.closed.emit()
        super().closeEvent(e)


__all__ = ["AlarmPopup"]
