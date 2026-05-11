"""Read-only log panel — receives messages from utils.logger."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QTextEdit


_LEVEL_COLOR = {
    "DEBUG":   "#888888",
    "INFO":    "#cfd8dc",
    "SUCCESS": "#66ff66",
    "WARNING": "#ffb74d",
    "ERROR":   "#ef5350",
    "CRITICAL": "#d50000",
}


class LogView(QTextEdit):
    line_received = Signal(str, str)  # (level, message)

    MAX_LINES = 500

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        # Background/font come from global theme QSS
        self._lines: deque[str] = deque(maxlen=self.MAX_LINES)
        # Cross-thread: logger sinks call append_line from arbitrary threads;
        # the signal forwards into the GUI thread.
        self.line_received.connect(self._append_html)

    @Slot(str, str)
    def append_line(self, level: str, message: str) -> None:
        self.line_received.emit(level, message)

    @Slot(str, str)
    def _append_html(self, level: str, message: str) -> None:
        color = _LEVEL_COLOR.get(level.upper(), "#cfd8dc")
        safe = message.replace("<", "&lt;").replace(">", "&gt;")
        line = f'<span style="color:{color}">[{level}]</span> {safe}'
        self._lines.append(line)
        self.setHtml("<br>".join(self._lines))
        # Auto-scroll
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


__all__ = ["LogView"]
