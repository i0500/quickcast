"""Splash — boot-time loading screen.

Shown by `main.run()` between QApplication construction and the AppShell
becoming visible. Lets the user see "we're alive" during the ~2-5 s
PyInstaller self-extraction + Python interpreter + Qt + section
builder phases.

The splash also hooks into PyInstaller's `pyi_splash` if present (set
up via `--splash` build flag) so messages can flow seamlessly from the
extraction phase into Qt.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPaintEvent, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget


WIDTH = 420
HEIGHT = 220


class Splash(QSplashScreen):
    """Branded splash with deterministic 0-100% progress gauge."""

    def __init__(self) -> None:
        pix = QPixmap(WIDTH, HEIGHT)
        pix.fill(Qt.transparent)
        super().__init__(pix, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._message = "시작 중…"
        # Real progress in [0, 1]. `_displayed` lags slightly so the
        # bar visually animates instead of snapping between updates.
        self._progress = 0.0
        self._displayed = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)        # ~60 fps for smooth tween
        self._anim.timeout.connect(self._tick)
        self._anim.start()

    def update_message(self, msg: str, pct: float | None = None) -> None:
        """Update the bottom text. If `pct` provided, also bumps the
        progress bar (clamped to never go backwards)."""
        self._message = msg
        if pct is not None:
            self.set_progress(pct)
        self.repaint()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def set_progress(self, pct: float) -> None:
        """Set absolute progress 0..1 (or 0..100 — both accepted).
        Monotonic — never moves backwards."""
        if pct > 1.0:
            pct = pct / 100.0
        self._progress = max(self._progress, max(0.0, min(1.0, pct)))
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _tick(self) -> None:
        # Ease the displayed bar toward the real target so the user
        # sees a continuous fill instead of stepwise jumps.
        delta = self._progress - self._displayed
        if abs(delta) > 0.0005:
            self._displayed += delta * 0.18
            self.repaint()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # Card background — dark surface w/ subtle accent border.
        body = QRectF(0, 0, self.width(), self.height())
        bg = QColor(20, 24, 31)
        border = QColor(91, 141, 239, 180)
        p.setBrush(QBrush(bg))
        pen = QPen(border, 1.5)
        p.setPen(pen)
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)

        # Title + tagline
        p.setPen(QColor(230, 234, 240))
        title_f = QFont("Pretendard Variable", 22, QFont.Bold)
        p.setFont(title_f)
        p.drawText(QRectF(24, 22, self.width() - 48, 40),
                   Qt.AlignLeft | Qt.AlignVCenter, "QuickCast")

        p.setPen(QColor(154, 164, 176))
        sub_f = QFont("Pretendard Variable", 10)
        p.setFont(sub_f)
        p.drawText(QRectF(24, 64, self.width() - 48, 22),
                   Qt.AlignLeft | Qt.AlignVCenter, "Skill Macro — Native Python")

        # Progress bar — real fill driven by `_displayed` (eased towards
        # the current `_progress` target).
        bar_y = self.height() - 60
        bar_h = 4
        bar_w = self.width() - 48
        track = QRectF(24, bar_y, bar_w, bar_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(43, 49, 59))
        p.drawRoundedRect(track, 2, 2)

        fill_w = max(0.0, min(1.0, self._displayed)) * bar_w
        if fill_w > 1:
            fill = QRectF(24, bar_y, fill_w, bar_h)
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0, QColor(91, 141, 239, 200))
            grad.setColorAt(1, QColor(110, 156, 242, 255))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(fill, 2, 2)

        # Progress message + percent on the right
        p.setPen(QColor(154, 164, 176))
        msg_f = QFont("Pretendard Variable", 10)
        p.setFont(msg_f)
        msg_rect = QRectF(24, bar_y - 26, bar_w - 50, 22)
        p.drawText(msg_rect, Qt.AlignLeft | Qt.AlignVCenter, self._message)
        pct_text = f"{int(round(self._displayed * 100))}%"
        pct_rect = QRectF(24 + bar_w - 50, bar_y - 26, 50, 22)
        p.setPen(QColor(91, 141, 239))
        p.drawText(pct_rect, Qt.AlignRight | Qt.AlignVCenter, pct_text)

    def finish_for(self, window: QWidget) -> None:
        """Stop animation + dismiss when `window` becomes visible."""
        self._anim.stop()
        super().finish(window)


def show_splash() -> Splash:
    """Create + show the splash. Caller updates messages, then finishes."""
    splash = Splash()
    # Centre on primary screen
    app = QApplication.instance()
    if app is not None and app.primaryScreen():
        scr = app.primaryScreen().availableGeometry()
        splash.move(QPoint(
            scr.x() + (scr.width()  - splash.width())  // 2,
            scr.y() + (scr.height() - splash.height()) // 2,
        ))
    splash.show()
    if app is not None:
        app.processEvents()
    return splash


# ───────── PyInstaller --splash bridge ─────────
# When the EXE was built with `--splash splash.png` PyInstaller injects
# `pyi_splash` into the bundled Python so we can update text and close
# its bootloader splash before we show the Qt one.
def pyi_update(text: str) -> None:
    try:
        import pyi_splash  # type: ignore
        pyi_splash.update_text(text)
    except Exception:
        pass


def pyi_close() -> None:
    try:
        import pyi_splash  # type: ignore
        pyi_splash.close()
    except Exception:
        pass


__all__ = ["Splash", "show_splash", "pyi_update", "pyi_close"]
