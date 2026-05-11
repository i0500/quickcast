"""KeyCaptureDialog — press a key, get its name (handles F1~F12, Enter, etc.).

QInputDialog only captures *typed text*, so function keys, Enter, arrows
and friends never show up. This dialog listens to keyPressEvent directly
and converts the Qt.Key enum to the same string format the macro core
sends to Arduino (single chars for letters/digits, lowercase names for
specials like "f5", "enter", "space").
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


# Qt.Key → wire-protocol token (matches input_io/win32_input.py _VK_MAP)
_NAMED = {
    Qt.Key_F1: "f1", Qt.Key_F2: "f2", Qt.Key_F3: "f3", Qt.Key_F4: "f4",
    Qt.Key_F5: "f5", Qt.Key_F6: "f6", Qt.Key_F7: "f7", Qt.Key_F8: "f8",
    Qt.Key_F9: "f9", Qt.Key_F10: "f10", Qt.Key_F11: "f11", Qt.Key_F12: "f12",
    Qt.Key_Return: "enter", Qt.Key_Enter: "enter",
    Qt.Key_Space: "space", Qt.Key_Tab: "tab",
    Qt.Key_Escape: "esc", Qt.Key_Backspace: "backspace",
    Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
    Qt.Key_Insert: "insert", Qt.Key_Delete: "delete",
    Qt.Key_Home: "home", Qt.Key_End: "end",
    Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown",
    Qt.Key_Shift: "shift", Qt.Key_Control: "ctrl", Qt.Key_Alt: "alt",
    # Numpad — only matched when KeypadModifier is set on the event
    # (see _key_to_name); otherwise digit text "2" wins for main row.
    Qt.Key_Asterisk: "nummul",
    Qt.Key_Plus: "numadd",
    Qt.Key_Minus: "numsub",
    Qt.Key_Period: "numdec",
    Qt.Key_Slash: "numdiv",
}


def _key_to_name(e: QKeyEvent) -> Optional[str]:
    k = e.key()
    is_numpad = bool(e.modifiers() & Qt.KeypadModifier)
    if is_numpad:
        # Numpad digits → "num0".."num9"
        if Qt.Key_0 <= k <= Qt.Key_9:
            return f"num{k - Qt.Key_0}"
        # Numpad Enter
        if k in (Qt.Key_Return, Qt.Key_Enter):
            return "numenter"
        # Numpad operators
        if k == Qt.Key_Asterisk: return "nummul"
        if k == Qt.Key_Plus:     return "numadd"
        if k == Qt.Key_Minus:    return "numsub"
        if k == Qt.Key_Period:   return "numdec"
        if k == Qt.Key_Slash:    return "numdiv"
    if k in _NAMED:
        return _NAMED[k]
    text = e.text()
    if text and text.isprintable() and not text.isspace():
        return text
    return None


class KeyCaptureDialog(QDialog):
    def __init__(self, current: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("키 입력")
        self.setModal(True)
        self.resize(360, 180)
        self.captured: str = current
        self._build()
        self.setFocusPolicy(Qt.StrongFocus)

    def _build(self) -> None:
        v = QVBoxLayout(self); v.setContentsMargins(20, 18, 20, 14); v.setSpacing(12)

        prompt = QLabel("아무 키나 눌러주세요")
        f = QFont(); f.setPointSize(11); prompt.setFont(f)
        prompt.setAlignment(Qt.AlignCenter)
        v.addWidget(prompt)

        from quickcast.ui.design.themed import reactive
        from quickcast.ui.design.tokens import T

        self.kbd_label = QLabel(self.captured or "—")
        kf = QFont(); kf.setBold(True); kf.setPointSize(20); kf.setFamily("JetBrains Mono")
        self.kbd_label.setFont(kf)
        self.kbd_label.setAlignment(Qt.AlignCenter)
        reactive(self.kbd_label, lambda: (
            "padding:14px; border-radius:8px;"
            f" background:{T.palette.bg_input};"
            f" color:{T.palette.text_primary};"
            f" border:1px solid {T.palette.border_default};"
        ))
        v.addWidget(self.kbd_label)

        hint = QLabel("F1~F12 · Enter · Space · Tab · Esc · ↑↓←→ · 넘패드 0~9 모두 가능")
        hint.setAlignment(Qt.AlignCenter)
        reactive(hint, lambda: f"color:{T.palette.text_tertiary}; font-size:11px;")
        v.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        # Don't swallow Tab — let the dialog buttons keep keyboard navigation
        # but we still record it as a captured key.
        name = _key_to_name(e)
        if name:
            self.captured = name
            self.kbd_label.setText(name)
            e.accept()
            return
        super().keyPressEvent(e)

    @staticmethod
    def get_key(parent, current: str = "") -> Optional[str]:
        dlg = KeyCaptureDialog(current, parent)
        if dlg.exec() == QDialog.Accepted and dlg.captured:
            return dlg.captured
        return None


__all__ = ["KeyCaptureDialog"]
