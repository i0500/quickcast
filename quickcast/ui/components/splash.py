"""Splash — boot-time loading screen.

Shown by `main.run()` between QApplication construction and the AppShell
becoming visible. Lets the user see "we're alive" during the ~2-5 s
PyInstaller self-extraction + Python interpreter + Qt + section
builder phases.

The splash also hooks into PyInstaller's `pyi_splash` if present (set
up via `--splash` build flag) so messages can flow seamlessly from the
extraction phase into Qt. **Show order matters**: `main.run()` shows
this Qt splash FIRST (it's frameless + stays-on-top so it covers the
native bootloader splash), THEN closes the native one — otherwise the
user sees the native splash blink off before the Qt splash blinks on
(the "꺼졌다 다시 뜨는" double-loading flicker).

Design: a floating dark card with a soft drop shadow, subtle blue
ambient glow, glass sheen, and a glowing blue progress bar — matched to
QuickCast's accent (#5B8DEF).
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPaintEvent, QPen, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QSplashScreen, QWidget


# 카드 + 소프트 섀도우 여백. 위젯은 (카드 + 2*MARGIN) 크기로, 카드는 그
# 안쪽에 그려 둘레에 떠 있는 듯한 그림자를 남긴다.
CARD_W = 440
CARD_H = 248
MARGIN = 24
WIDTH = CARD_W + MARGIN * 2
HEIGHT = CARD_H + MARGIN * 2


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

        card = QRectF(MARGIN, MARGIN, CARD_W, CARD_H)
        radius = 20.0

        # ── 떠 있는 카드 느낌의 소프트 섀도우 (살짝 아래로) ──
        p.setPen(Qt.NoPen)
        for i in range(MARGIN, 0, -1):
            t = i / MARGIN
            a = int(70 * (1.0 - t) ** 2.2)      # 바깥은 0, 카드 가까울수록 진하게
            if a <= 0:
                continue
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(card.adjusted(-i, -i + 3, i, i + 6),
                              radius + i * 0.6, radius + i * 0.6)

        # ── 카드 본체: 둥근 사각형으로 클립 후 그라데이션 + 앰비언트 글로우 ──
        clip = QPainterPath()
        clip.addRoundedRect(card, radius, radius)
        p.save()
        p.setClipPath(clip)

        # 대각 그라데이션 배경 — 깊은 차콜/거의 검정 (아주 옅은 블루끼).
        bg = QLinearGradient(card.topLeft(), card.bottomRight())
        bg.setColorAt(0.0, QColor(14, 17, 23))      # very dark, slight blue
        bg.setColorAt(0.55, QColor(10, 12, 16))
        bg.setColorAt(1.0, QColor(7, 8, 11))        # near black
        p.fillRect(card, QBrush(bg))

        # 앰비언트 글로우 — 우상단에만 아주 은은한 블루 한 점.
        g1 = QRadialGradient(card.left() + CARD_W * 0.88,
                             card.top() + CARD_H * 0.08, CARD_W * 0.5)
        g1.setColorAt(0.0, QColor(91, 141, 239, 38))    # #5B8DEF, faint
        g1.setColorAt(1.0, QColor(91, 141, 239, 0))
        p.fillRect(card, QBrush(g1))

        # 상단 하이라이트 라인 (유리 느낌) — 매우 약하게.
        sheen = QLinearGradient(card.topLeft(), QRectF(card).bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 10))
        sheen.setColorAt(0.16, QColor(255, 255, 255, 0))
        p.fillRect(card, QBrush(sheen))
        p.restore()

        # 카드 테두리 — 미묘한 블루 그라데이션 (은은하게).
        bpen = QLinearGradient(card.topLeft(), card.bottomRight())
        bpen.setColorAt(0.0, QColor(91, 141, 239, 95))
        bpen.setColorAt(0.5, QColor(110, 130, 170, 40))
        bpen.setColorAt(1.0, QColor(110, 156, 242, 75))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(bpen), 1.2))
        p.drawRoundedRect(card.adjusted(0.7, 0.7, -0.7, -0.7), radius, radius)

        cx, cy = card.left(), card.top()
        tx = cx + 28        # 타이틀을 좌측에서 시작.

        # ── 타이틀 (옅은 블루-화이트 그라데이션 텍스트) ──
        title_f = QFont("Pretendard Variable", 22, QFont.Bold)
        title_f.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        p.setFont(title_f)
        tgrad = QLinearGradient(0, cy + 30, 0, cy + 60)
        tgrad.setColorAt(0.0, QColor(238, 243, 252))
        tgrad.setColorAt(1.0, QColor(176, 198, 240))
        p.setPen(QPen(QBrush(tgrad), 1))
        p.drawText(QRectF(tx, cy + 28, CARD_W - (tx - cx) - 24, 34),
                   Qt.AlignLeft | Qt.AlignVCenter, "QuickCast")

        # 서브타이틀.
        p.setPen(QColor(150, 160, 180))
        p.setFont(QFont("Pretendard Variable", 9))
        p.drawText(QRectF(tx, cy + 62, CARD_W - (tx - cx) - 24, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, "Skill Macro · Native Python")

        # ── 진행 영역 ──
        bar_w = CARD_W - 52
        bar_x = cx + 26
        bar_y = cy + CARD_H - 42
        bar_h = 6.0

        # 메시지 + 퍼센트
        p.setPen(QColor(168, 178, 198))
        p.setFont(QFont("Pretendard Variable", 9))
        p.drawText(QRectF(bar_x, bar_y - 26, bar_w - 54, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, self._message)
        pct_text = f"{int(round(self._displayed * 100))}%"
        p.setPen(QColor(140, 176, 255))     # 밝은 블루
        p.setFont(QFont("Pretendard Variable", 10, QFont.Bold))
        p.drawText(QRectF(bar_x + bar_w - 54, bar_y - 26, 54, 20),
                   Qt.AlignRight | Qt.AlignVCenter, pct_text)

        # 트랙
        track = QRectF(bar_x, bar_y, bar_w, bar_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(track, bar_h / 2, bar_h / 2)

        # 채움 (블루 그라데이션 + 은은한 글로우)
        fill_w = max(0.0, min(1.0, self._displayed)) * bar_w
        if fill_w > 2:
            fill = QRectF(bar_x, bar_y, fill_w, bar_h)
            # glow
            glow = fill.adjusted(-2, -3, 2, 3)
            p.setBrush(QColor(91, 141, 239, 70))
            p.drawRoundedRect(glow, (bar_h + 6) / 2, (bar_h + 6) / 2)
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0.0, QColor(74, 123, 216))      # deep blue
            grad.setColorAt(0.6, QColor(91, 141, 239))      # accent
            grad.setColorAt(1.0, QColor(173, 200, 250))     # light sky
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(fill, bar_h / 2, bar_h / 2)

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
