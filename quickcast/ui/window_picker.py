"""Modal dialog for picking a capture target window — with thumbnails.

Each visible top-level window gets a live thumbnail (via mss) so the
user can recognise their target visually, similar to Alt-Tab.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from quickcast.utils.logger import logger
from quickcast.utils.window_finder import (
    WindowEntry, get_window_rect, list_visible_windows,
)


THUMB_W = 200
THUMB_H = 120


def _grab_thumb(entry: WindowEntry) -> Optional[QPixmap]:
    """Capture the window's rendered content as a QPixmap thumbnail.

    Prefers PrintWindow (works even if the window is occluded or on a
    secondary monitor with negative coords) and falls back to mss only
    when PrintWindow refuses.
    """
    try:
        from quickcast.core.window_print_capture import WindowPrintCapture
        cap = WindowPrintCapture(hwnd=entry.hwnd, label=entry.title)
        frame = cap.grab()
        raw = frame.image
    except Exception:
        try:
            import mss
            rect = get_window_rect(entry.hwnd)
            if rect is None or rect.width <= 0 or rect.height <= 0:
                return None
            with mss.mss() as sct:
                raw = np.asarray(sct.grab({"left": rect.left, "top": rect.top,
                                           "width": rect.width, "height": rect.height}))
        except Exception as e:
            logger.debug(f"thumb grab failed for '{entry.title}': {e}")
            return None

    if not raw.flags["C_CONTIGUOUS"]:
        raw = np.ascontiguousarray(raw)
    h, w = raw.shape[:2]
    # Hold a reference so QImage doesn't read freed memory mid-render
    buf = raw.tobytes()
    qimg = QImage(buf, w, h, w * 4, QImage.Format_ARGB32).copy()
    return QPixmap.fromImage(qimg).scaled(
        THUMB_W, THUMB_H, Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )


class WindowPicker(QDialog):
    """Grid of windows with thumbnails; user picks one for capture."""

    def __init__(self, current_title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("캡처할 창 선택")
        self.resize(780, 560)

        self._chosen: Optional[WindowEntry] = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            "캡처할 게임/응용프로그램 창을 선택하세요. 썸네일을 클릭 후 [확인] 또는 더블클릭."
        ))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("필터:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("창 제목 일부 입력")
        self.filter_edit.textChanged.connect(self._refilter)
        refresh_btn = QPushButton("🔄 새로고침")
        refresh_btn.clicked.connect(self._reload)
        filter_row.addWidget(self.filter_edit, stretch=1)
        filter_row.addWidget(refresh_btn)
        root.addLayout(filter_row)

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(THUMB_W, THUMB_H))
        self.list.setGridSize(QSize(THUMB_W + 24, THUMB_H + 60))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setSpacing(8)
        self.list.setMovement(QListView.Static)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        root.addWidget(self.list, stretch=1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept); btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._all_windows: list[WindowEntry] = []
        self._reload()
        if current_title:
            self.filter_edit.setText(current_title)
            self._select_first_match(current_title)

    # ───────── data ─────────
    def _reload(self) -> None:
        self._all_windows = list_visible_windows()
        self._refilter()

    def _refilter(self) -> None:
        needle = self.filter_edit.text().lower().strip()
        self.list.clear()
        # Async-ish thumbnail load: insert placeholders then fill via timer
        for w in self._all_windows:
            if needle and needle not in w.title.lower():
                continue
            display = w.title if len(w.title) <= 40 else w.title[:37] + "…"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, w)
            item.setToolTip(w.title)
            self.list.addItem(item)
        # Fill thumbnails after the dialog is shown to avoid blocking
        QTimer.singleShot(0, self._fill_thumbs)

    def _fill_thumbs(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.icon().isNull():
                entry: WindowEntry = item.data(Qt.UserRole)
                pix = _grab_thumb(entry)
                if pix:
                    item.setIcon(QIcon(pix))

    def _select_first_match(self, title: str) -> None:
        title = title.lower()
        for i in range(self.list.count()):
            entry: WindowEntry = self.list.item(i).data(Qt.UserRole)
            if title in entry.title.lower():
                self.list.setCurrentRow(i); break

    # ───────── actions ─────────
    def _on_double_click(self, _item) -> None:
        self._accept()

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self._chosen = item.data(Qt.UserRole)
        self.accept()

    def chosen(self) -> Optional[WindowEntry]:
        return self._chosen


__all__ = ["WindowPicker"]
