"""CommandPalette — Ctrl+K searchable action launcher (VS Code style).

Borderless modal centered on the parent window. The host registers
`Action(id, title, hint, callback)` entries; user types to filter,
arrow keys to navigate, Enter to fire, Escape to dismiss.

Ranking is fuzzy-ish: substring match wins over single-character
matches; recent picks float to the top within a session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QVBoxLayout, QWidget,
)

from quickcast.ui.components.kbd import Kbd
from quickcast.ui.design.icons import Icon
from quickcast.ui.design.signals import bus
from quickcast.ui.design.themed import reactive
from quickcast.ui.design.tokens import T


@dataclass
class Action:
    id: str
    title: str
    hint: str = ""             # secondary line — shortcut, group, status
    icon: str = "command"      # Lucide name
    section: str = ""          # optional grouping label
    callback: Callable[[], None] = field(default=lambda: None)


_RECENT_LIMIT = 5
_PALETTE_W = 520
_PALETTE_H = 420
_ROW_H = 44


class _ActionRow(QWidget):
    """Single hit in the result list — icon + title + hint."""

    def __init__(self, action: Action) -> None:
        super().__init__()
        self.action = action
        self.setMinimumHeight(_ROW_H)
        h = QHBoxLayout(self); h.setContentsMargins(12, 6, 12, 6); h.setSpacing(10)

        self.icon_lbl = QLabel()
        h.addWidget(self.icon_lbl)

        text_box = QVBoxLayout(); text_box.setContentsMargins(0, 0, 0, 0); text_box.setSpacing(0)
        self.title_lbl = QLabel(action.title)
        f = QFont(); f.setPointSize(13); self.title_lbl.setFont(f)
        text_box.addWidget(self.title_lbl)
        if action.hint:
            self.hint_lbl = QLabel(action.hint)
            self.hint_lbl.setProperty("role", "caption")
            text_box.addWidget(self.hint_lbl)
        else:
            self.hint_lbl = None
        h.addLayout(text_box, stretch=1)

        if action.section:
            self.sec_lbl = QLabel(action.section)
            h.addWidget(self.sec_lbl)
        else:
            self.sec_lbl = None

        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        self.title_lbl.setStyleSheet(f"color:{p.text_primary};")
        if self.hint_lbl is not None:
            self.hint_lbl.setStyleSheet(
                f"color:{p.text_tertiary}; font-size:11px;"
            )
        if self.sec_lbl is not None:
            self.sec_lbl.setStyleSheet(
                f"color:{p.text_tertiary};"
                f" font-family:{T.type.mono}; font-size:11px;"
            )
        self.icon_lbl.setPixmap(
            Icon.get(self.action.icon, 16, p.text_secondary).pixmap(16, 16)
        )


class CommandPalette(QDialog):
    """Modal floating action launcher."""

    fired = Signal(str)  # emits action.id after callback runs

    def __init__(self, actions: Iterable[Action], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedSize(_PALETTE_W, _PALETTE_H)

        self._actions: list[Action] = list(actions)
        self._recent: list[str] = []
        self._build()
        self._refresh_results("")

    # ───────── building ─────────
    def _build(self) -> None:
        wrap = QFrame(self)
        wrap.setObjectName("cmdPaletteFrame")
        wrap.setGeometry(0, 0, _PALETTE_W, _PALETTE_H)
        v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)

        # Search header
        head = QFrame(); head.setFixedHeight(48)
        hh = QHBoxLayout(head); hh.setContentsMargins(14, 0, 8, 0); hh.setSpacing(10)
        self.search_icon = QLabel()
        hh.addWidget(self.search_icon)
        self.search = QLineEdit()
        self.search.setPlaceholderText("명령 검색…")
        self.search.setStyleSheet("border:none; background:transparent; font-size:14px;")
        self.search.textChanged.connect(self._refresh_results)
        hh.addWidget(self.search, stretch=1)
        # Top-right close (X) button — alternative to Esc for users who
        # don't know the shortcut.
        from quickcast.ui.components.icon_button import IconOnlyButton
        self.close_btn = IconOnlyButton("x", size="sm", tooltip="닫기 (Esc)")
        self.close_btn.clicked.connect(self.reject)
        hh.addWidget(self.close_btn)
        v.addWidget(head)

        # Separator
        sep = QFrame(); sep.setFrameShape(QFrame.HLine); sep.setFixedHeight(1)
        v.addWidget(sep)
        self._sep = sep

        # Results list
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.itemActivated.connect(lambda _it: self._fire_current())
        self.list.itemClicked.connect(lambda _it: self._fire_current())
        v.addWidget(self.list, stretch=1)

        # Footer hint
        foot = QFrame(); foot.setFixedHeight(28)
        fh = QHBoxLayout(foot); fh.setContentsMargins(14, 0, 14, 0); fh.setSpacing(8)
        self.foot_lbl = QLabel("↑↓ 이동   ↵ 실행")
        fh.addWidget(self.foot_lbl); fh.addStretch(1)
        self.count_lbl = QLabel("")
        fh.addWidget(self.count_lbl)
        v.addWidget(foot)

        bus.theme_changed.connect(self._restyle)
        self._restyle()

    def _restyle(self) -> None:
        p = T.palette
        self.setStyleSheet(
            f"QFrame#cmdPaletteFrame {{ background:{p.bg_elevated};"
            f" border:1px solid {p.border_default}; border-radius:10px; }}"
            f"QListWidget {{ background:transparent; border:none; outline:0; }}"
            f"QListWidget::item {{ border:none; }}"
            f"QListWidget::item:selected {{ background:{p.accent_subtle};"
            f" color:{p.text_primary}; }}"
        )
        self._sep.setStyleSheet(f"background:{p.border_subtle};")
        self.search_icon.setPixmap(
            Icon.get("search", 16, p.text_tertiary).pixmap(16, 16)
        )
        self.search.setStyleSheet(
            "border:none; background:transparent; font-size:14px;"
            f" color:{p.text_primary};"
        )
        self.foot_lbl.setStyleSheet(
            f"color:{p.text_tertiary}; font-size:11px;"
        )
        self.count_lbl.setStyleSheet(
            f"color:{p.text_tertiary}; font-size:11px;"
            f" font-family:{T.type.mono};"
        )

    # ───────── ranking ─────────
    def _score(self, query: str, action: Action) -> int:
        """Higher is better. 0 means filtered out."""
        if not query:
            base = 100
        else:
            q = query.lower()
            t = action.title.lower()
            h = action.hint.lower()
            if q == t:
                base = 1000
            elif t.startswith(q):
                base = 500
            elif q in t:
                base = 250
            elif q in h:
                base = 100
            else:
                # all chars present in order
                idx = 0
                for ch in q:
                    f = t.find(ch, idx)
                    if f == -1:
                        return 0
                    idx = f + 1
                base = 50
        # Recent boost
        if action.id in self._recent:
            base += 10 * (_RECENT_LIMIT - self._recent.index(action.id))
        return base

    def _refresh_results(self, query: str = "") -> None:
        scored = [
            (self._score(query, a), a) for a in self._actions
        ]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda p: -p[0])
        self.list.clear()
        for _, action in scored:
            row = _ActionRow(action)
            it = QListWidgetItem(self.list)
            it.setSizeHint(QSize(0, _ROW_H))
            it.setData(Qt.UserRole, action.id)
            self.list.addItem(it)
            self.list.setItemWidget(it, row)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)
        self.count_lbl.setText(f"{self.list.count()}건")

    # ───────── interaction ─────────
    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        if k in (Qt.Key_Escape,):
            self.reject(); return
        if k in (Qt.Key_Down,):
            row = self.list.currentRow()
            self.list.setCurrentRow(min(row + 1, self.list.count() - 1))
            return
        if k in (Qt.Key_Up,):
            row = self.list.currentRow()
            self.list.setCurrentRow(max(row - 1, 0))
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._fire_current(); return
        super().keyPressEvent(e)

    def showEvent(self, e: QEvent) -> None:
        super().showEvent(e)
        # Centre on parent.
        if self.parent() is not None:
            pgeo = self.parent().geometry()
            self.move(
                pgeo.x() + (pgeo.width() - self.width()) // 2,
                pgeo.y() + (pgeo.height() - self.height()) // 3,
            )
        self.search.setFocus()
        self.search.selectAll()

    def _fire_current(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        aid = it.data(Qt.UserRole)
        action = next((a for a in self._actions if a.id == aid), None)
        if action is None:
            return
        # Update recent
        if aid in self._recent:
            self._recent.remove(aid)
        self._recent.insert(0, aid)
        self._recent = self._recent[:_RECENT_LIMIT]
        self.accept()
        try:
            action.callback()
        finally:
            self.fired.emit(aid)


__all__ = ["Action", "CommandPalette"]
