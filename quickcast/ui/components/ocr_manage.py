"""OCR 학습 관리 — review per-glyph instance counts and prune.

Lets the user see how many saved instances each glyph has (0..9, '/')
and either:

- zap one glyph's entire training set (e.g. "0" got polluted by a bad
  calibration pass and is now confused with "8"), or
- nuke the whole digit store and start fresh.

Changes go straight to disk via core.digit_store, and we emit
``bus.digit_templates_changed`` on close so the live recognizer reloads
its in-memory templates without an app restart.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from quickcast.core import digit_store
from quickcast.core.ocr import GLYPHS
from quickcast.ui.design.signals import bus


class OcrManageDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCR 학습 관리")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._dirty = False

        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(10)

        hdr = QLabel(
            "학습된 글자별 누적 샘플 수입니다. 인식이 안 되거나 잘못 잡히는 글자는\n"
            "[지우기]로 초기화하고 다시 학습하면 깔끔해집니다."
        )
        hdr.setWordWrap(True)
        v.addWidget(hdr)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(4)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)

        # Footer buttons
        ft = QHBoxLayout(); ft.setSpacing(8)
        self._clear_all = QPushButton("전체 초기화")
        self._clear_all.clicked.connect(self._on_clear_all)
        ft.addWidget(self._clear_all); ft.addStretch(1)
        close = QPushButton("닫기"); close.clicked.connect(self.accept)
        close.setDefault(True)
        ft.addWidget(close)
        v.addLayout(ft)

        self._refresh_rows()

    # ───────── rendering ─────────
    def _clear_layout(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _refresh_rows(self) -> None:
        self._clear_layout()
        counts = digit_store.instance_counts()
        total = sum(counts.values())
        # Header summary
        sf = QFont(); sf.setBold(True)
        summary = QLabel(f"총 {total}개 샘플")
        summary.setFont(sf)
        self._body_layout.addWidget(summary)

        # One row per known glyph (always show all 11 — 0 count for un-trained)
        for label in GLYPHS:
            row = QWidget()
            h = QHBoxLayout(row); h.setContentsMargins(0, 2, 0, 2); h.setSpacing(8)
            display = "/" if label == "/" else label
            tag = QLabel(f"  {display}")
            tag.setMinimumWidth(28)
            f = QFont(); f.setBold(True); f.setPointSize(11); tag.setFont(f)
            h.addWidget(tag)

            count = counts.get(label, 0)
            count_lbl = QLabel(f"{count}개 학습됨" if count > 0 else "학습되지 않음")
            if count == 0:
                count_lbl.setStyleSheet("color: #888;")
            h.addWidget(count_lbl, 1)

            btn = QPushButton("지우기")
            btn.setFixedHeight(24)
            btn.setEnabled(count > 0)
            btn.clicked.connect(lambda _=False, lab=label: self._on_clear_one(lab))
            h.addWidget(btn)

            self._body_layout.addWidget(row)
        self._body_layout.addStretch(1)

    # ───────── actions ─────────
    def _on_clear_one(self, label: str) -> None:
        display = "/" if label == "/" else label
        if QMessageBox.question(
            self, "글자 학습 삭제",
            f"'{display}' 글자의 학습 데이터를 모두 지울까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        digit_store.clear_label(label)
        self._dirty = True
        self._refresh_rows()

    def _on_clear_all(self) -> None:
        if QMessageBox.question(
            self, "전체 학습 초기화",
            "모든 학습 데이터를 지울까요? 되돌릴 수 없습니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        digit_store.clear_templates()
        self._dirty = True
        self._refresh_rows()

    # ───────── lifecycle ─────────
    def accept(self) -> None:
        if self._dirty:
            try:
                bus.digit_templates_changed.emit()
            except Exception:
                pass
        super().accept()


__all__ = ["OcrManageDialog"]
