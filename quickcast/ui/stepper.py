"""Stepper widget — [-] [number] [+] horizontal layout.

Replaces QSpinBox/QDoubleSpinBox where the built-in up/down arrows
overlap the value text. Buttons are on the sides so the number is
always fully readable.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout, QLineEdit, QPushButton, QSizePolicy, QWidget,
)


class Stepper(QWidget):
    """Numeric stepper. Click ± to nudge, type to edit, Enter to commit."""

    valueChanged = Signal(float)

    def __init__(
        self,
        value: float = 0,
        minimum: float = 0,
        maximum: float = 100,
        step: float = 1,
        decimals: int = 0,
        suffix: str = "",
        width: int = 110,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._step = step
        self._decimals = decimals
        self._suffix = suffix
        self._value = self._clamp(value)

        H = 32           # unified row height — matches buttons / inputs
        BTN_W = 28       # square stepper button
        self.setFixedHeight(H)
        self.setFixedWidth(max(width, BTN_W * 2 + 56))
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Use Lucide icons (lazy import to avoid circulars)
        from PySide6.QtCore import QSize
        from quickcast.ui.design.icons import Icon
        from quickcast.ui.design.tokens import T

        self.btn_minus = QPushButton()
        self.btn_minus.setIcon(Icon.get("minus", 14, T.palette.text_secondary))
        self.btn_minus.setIconSize(QSize(14, 14))
        self.btn_minus.setFixedSize(BTN_W, H)
        self.btn_minus.setFocusPolicy(Qt.NoFocus)
        self.btn_minus.setObjectName("stepperMinus")
        self.btn_minus.clicked.connect(lambda: self._nudge(-self._step))
        self._install_repeat(self.btn_minus, lambda: self._nudge(-self._step))

        self.entry = QLineEdit(self._format(self._value))
        self.entry.setAlignment(Qt.AlignCenter)
        self.entry.setObjectName("stepperEdit")
        self.entry.setFixedHeight(H)
        f = QFont(T.type.mono); f.setPointSize(11); f.setBold(True)
        self.entry.setFont(f)
        self.entry.editingFinished.connect(self._commit_text)

        self.btn_plus = QPushButton()
        self.btn_plus.setIcon(Icon.get("plus", 14, T.palette.text_secondary))
        self.btn_plus.setIconSize(QSize(14, 14))
        self.btn_plus.setFixedSize(BTN_W, H)
        self.btn_plus.setFocusPolicy(Qt.NoFocus)
        self.btn_plus.setObjectName("stepperPlus")
        self.btn_plus.clicked.connect(lambda: self._nudge(self._step))
        self._install_repeat(self.btn_plus, lambda: self._nudge(self._step))

        lay.addWidget(self.btn_minus)
        lay.addWidget(self.entry, stretch=1)
        lay.addWidget(self.btn_plus)

        p = T.palette
        # Buttons are ghost-style attached to a rounded input — no double borders.
        self.setStyleSheet(
            f"QLineEdit#stepperEdit {{"
            f"  background:{p.bg_input}; color:{p.text_primary};"
            f"  border:1px solid {p.border_default};"
            f"  border-radius:6px;"
            f"  padding:0 6px;"
            f"}}"
            f"QLineEdit#stepperEdit:focus {{"
            f"  border-color:{p.border_focus};"
            f"  background:{p.bg_surface};"
            f"}}"
            f"QPushButton#stepperMinus, QPushButton#stepperPlus {{"
            f"  background: transparent; border: none; border-radius:6px;"
            f"  margin: 2px;"
            f"}}"
            f"QPushButton#stepperMinus:hover, QPushButton#stepperPlus:hover {{"
            f"  background:{p.bg_hover};"
            f"}}"
            f"QPushButton#stepperMinus:pressed, QPushButton#stepperPlus:pressed {{"
            f"  background:{p.bg_pressed};"
            f"}}"
        )

    # ───────── public API ─────────
    def value(self) -> float:
        return self._value

    def setValue(self, v: float) -> None:
        v = self._clamp(v)
        if v == self._value:
            return
        self._value = v
        self.entry.setText(self._format(v))
        self.valueChanged.emit(v)

    def setRange(self, lo: float, hi: float) -> None:
        self._min, self._max = lo, hi
        self.setValue(self._clamp(self._value))

    def setSuffix(self, s: str) -> None:
        self._suffix = s
        self.entry.setText(self._format(self._value))

    # ───────── internals ─────────
    def _clamp(self, v: float) -> float:
        return max(self._min, min(self._max, v))

    def _format(self, v: float) -> str:
        if self._decimals == 0:
            text = f"{int(round(v))}"
        else:
            text = f"{v:.{self._decimals}f}"
        return f"{text}{self._suffix}"

    def _nudge(self, delta: float) -> None:
        self.setValue(self._value + delta)

    def _commit_text(self) -> None:
        raw = self.entry.text().strip()
        if self._suffix and raw.endswith(self._suffix):
            raw = raw[: -len(self._suffix)].strip()
        try:
            v = float(raw)
        except ValueError:
            self.entry.setText(self._format(self._value))
            return
        self.setValue(v)

    @staticmethod
    def _install_repeat(button: QPushButton, action) -> None:
        """Hold-down auto-repeat (faster than relying on QPushButton's native)."""
        timer = QTimer(button)
        timer.setInterval(70)
        timer.timeout.connect(action)
        button.pressed.connect(lambda: QTimer.singleShot(350, lambda:
            timer.start() if button.isDown() else None))
        button.released.connect(timer.stop)


__all__ = ["Stepper"]
