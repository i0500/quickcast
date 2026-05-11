"""TutorialOverlay — first-run guided setup.

Renders a translucent dimmer over the whole window with a "spotlight"
hole around the highlighted widget, plus a Markdown-styled bubble
that explains the step. Walked sequentially via prev/next/skip.

The step list is kept dumb data so it's trivial to re-order, edit
copy, or render preview screenshots without touching layout code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QPainter, QPainterPath, QPaintEvent, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


@dataclass
class TutorialStep:
    """One bubble in the sequence."""
    title: str
    body_html: str
    section_id: Optional[str] = None      # which app section to switch to
    target_finder: Optional[Callable[[QWidget], Optional[QWidget]]] = None  # find target widget on app
    arrow: str = "below"                  # "above" / "below" / "left" / "right" / "center"
    extras: dict = field(default_factory=dict)


class _Bubble(QFrame):
    """The text+buttons card placed near the highlighted widget."""

    next_clicked = Signal()
    prev_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tutorialBubble")
        self.setFixedWidth(420)
        self.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 14); v.setSpacing(8)

        self.step_lbl = QLabel("")
        self.step_lbl.setStyleSheet("color:#5B8DEF; font-size:11px; font-weight:600;")
        v.addWidget(self.step_lbl)

        self.title_lbl = QLabel("")
        f = QFont(); f.setBold(True); f.setPointSize(14)
        self.title_lbl.setFont(f)
        self.title_lbl.setStyleSheet("color:#E6EAF0;")
        v.addWidget(self.title_lbl)

        self.body_lbl = QLabel("")
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setTextFormat(Qt.RichText)
        self.body_lbl.setStyleSheet("color:#9AA4B0; font-size:12px; line-height:1.45;")
        v.addWidget(self.body_lbl)

        btns = QHBoxLayout(); btns.setSpacing(6)
        self.skip = QPushButton("건너뛰기")
        self.skip.setStyleSheet(
            "QPushButton { background:transparent; color:#6B7280;"
            " border:none; padding:6px 8px; font-size:11px; }"
            "QPushButton:hover { color:#9AA4B0; }"
        )
        self.skip.clicked.connect(self.skip_clicked.emit)
        btns.addWidget(self.skip); btns.addStretch(1)

        self.prev = QPushButton("← 이전")
        self.prev.setStyleSheet(
            "QPushButton { background:transparent; color:#9AA4B0;"
            " border:1px solid #2B313B; border-radius:6px;"
            " padding:6px 12px; font-size:12px; }"
            "QPushButton:hover { background:rgba(255,255,255,0.04); }"
            "QPushButton:disabled { color:#4B5563; border-color:#1B2129; }"
        )
        self.prev.clicked.connect(self.prev_clicked.emit)
        btns.addWidget(self.prev)

        self.nxt = QPushButton("다음 →")
        self.nxt.setStyleSheet(
            "QPushButton { background:#5B8DEF; color:white;"
            " border:none; border-radius:6px;"
            " padding:6px 14px; font-size:12px; font-weight:600; }"
            "QPushButton:hover { background:#6E9CF2; }"
        )
        self.nxt.clicked.connect(self.next_clicked.emit)
        btns.addWidget(self.nxt)

        v.addLayout(btns)

        self.setStyleSheet(
            "QFrame#tutorialBubble {"
            " background:#14181F; border:1px solid #2B313B;"
            " border-radius:12px;"
            "}"
        )

    def set_step(self, idx: int, total: int, step: TutorialStep,
                  is_first: bool, is_last: bool) -> None:
        self.step_lbl.setText(f"단계 {idx + 1} / {total}")
        self.title_lbl.setText(step.title)
        self.body_lbl.setText(step.body_html)
        self.prev.setEnabled(not is_first)
        self.nxt.setText("완료" if is_last else "다음 →")


class TutorialOverlay(QWidget):
    """Full-window overlay with spotlight + bubble. Lives as a child of
    the AppShell so it covers everything when shown."""

    finished = Signal(bool)   # True if completed, False if skipped

    def __init__(self, parent: QWidget, steps: list[TutorialStep]) -> None:
        # Top-level frameless overlay positioned to cover the anchor
        # window's screen rect. Sibling-as-child approach lost paint
        # races with the AppShell's layout-managed widgets, so we
        # commit to a real OS-level window with AlwaysOnTop hint.
        super().__init__(None)
        self._anchor = parent
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.SplashScreen        # show without taskbar entry, no focus steal
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.steps = steps
        self.idx = 0
        self._spot_rect: QRect = QRect()
        self._target: Optional[QWidget] = None

        self.bubble = _Bubble(self)
        self.bubble.next_clicked.connect(self._on_next)
        self.bubble.prev_clicked.connect(self._on_prev)
        self.bubble.skip_clicked.connect(self._on_skip)
        # Don't refresh until show_at() — we need the anchor sized first
        # so target.mapFromGlobal yields meaningful overlay-local coords.

    def _sync_to_anchor(self) -> None:
        """Position the top-level overlay over the anchor's screen rect."""
        a = self._anchor
        if a is None:
            return
        tl = a.mapToGlobal(QPoint(0, 0))
        self.setGeometry(tl.x(), tl.y(), a.width(), a.height())

    def show_at(self) -> None:
        self._sync_to_anchor()
        self.show()
        self.raise_()
        self.activateWindow()
        self._refresh()

    def _on_next(self) -> None:
        if self.idx >= len(self.steps) - 1:
            self.finished.emit(True)
            self.hide()
            return
        self.idx += 1
        self._refresh()

    def _on_prev(self) -> None:
        if self.idx > 0:
            self.idx -= 1
            self._refresh()

    def _on_skip(self) -> None:
        self.finished.emit(False)
        self.hide()

    def _refresh(self) -> None:
        step = self.steps[self.idx]
        self.bubble.set_step(
            self.idx, len(self.steps), step,
            is_first=(self.idx == 0),
            is_last=(self.idx == len(self.steps) - 1),
        )
        # Switch app section if requested.
        if step.section_id:
            try:
                from quickcast.ui.design.signals import bus
                bus.activate_section.emit(step.section_id)
            except Exception:
                pass
        # Resolve spotlight target.
        target = None
        if step.target_finder is not None and self.parentWidget() is not None:
            try:
                target = step.target_finder(self.parentWidget())
            except Exception:
                target = None
        self._target = target
        if target is not None:
            # Overlay is top-level — map target's global pos into our
            # window-local coords (relative to overlay's own rect).
            tl_global = target.mapToGlobal(QPoint(0, 0))
            tl = self.mapFromGlobal(tl_global)
            self._spot_rect = QRect(tl, target.size()).adjusted(-6, -6, 6, 6)
        else:
            self._spot_rect = QRect()
        self._place_bubble(step)
        self.update()

    def _place_bubble(self, step: TutorialStep) -> None:
        # Position bubble near spotlight (or centre if no target).
        rect = self.rect()
        bw = self.bubble.sizeHint().width()
        bh = self.bubble.sizeHint().height()
        self.bubble.resize(bw, bh)
        if not self._spot_rect.isValid() or step.arrow == "center":
            x = rect.center().x() - bw // 2
            y = rect.center().y() - bh // 2
        elif step.arrow == "below":
            x = self._spot_rect.center().x() - bw // 2
            y = self._spot_rect.bottom() + 16
        elif step.arrow == "above":
            x = self._spot_rect.center().x() - bw // 2
            y = self._spot_rect.top() - bh - 16
        elif step.arrow == "right":
            x = self._spot_rect.right() + 16
            y = self._spot_rect.center().y() - bh // 2
        elif step.arrow == "left":
            x = self._spot_rect.left() - bw - 16
            y = self._spot_rect.center().y() - bh // 2
        else:
            x = rect.center().x() - bw // 2
            y = rect.center().y() - bh // 2
        # Clamp inside parent bounds.
        x = max(16, min(x, rect.width() - bw - 16))
        y = max(16, min(y, rect.height() - bh - 16))
        self.bubble.move(x, y)

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # Dim everything …
        veil = QPainterPath()
        veil.addRect(self.rect())
        if self._spot_rect.isValid():
            spot = QPainterPath()
            spot.addRoundedRect(self._spot_rect, 8, 8)
            veil = veil.subtracted(spot)
        p.fillPath(veil, QColor(0, 0, 0, 170))
        # Spotlight border for emphasis.
        if self._spot_rect.isValid():
            pen = QPen(QColor(91, 141, 239, 220), 2)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(self._spot_rect, 8, 8)


__all__ = ["TutorialOverlay", "TutorialStep"]
