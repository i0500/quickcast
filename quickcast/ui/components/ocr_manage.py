"""OCR 학습 관리 — review per-glyph instance counts and prune.

Now per-domain (HP / MP / potion). Each domain has its own glyph
pool under digits/<domain>/<label>/, so the dialog walks every
trained domain and groups the rows by domain. Per-row delete +
per-domain "전체 초기화" buttons.

Closes via accept() and emits ``bus.digit_templates_changed`` if
anything actually changed so the live recognizer reloads.
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


_DOMAIN_TITLES = {
    "hp": "HP",
    "mp": "MP",
    "potion": "물약",
    "legacy": "구버전 통합",
}


class OcrManageDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCR 학습 관리")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setMinimumHeight(540)

        self._dirty = False

        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(10)

        hdr = QLabel(
            "영역별로 학습된 글자 instance를 관리합니다. 인식이 안 되거나\n"
            "잘못 잡히는 글자는 그 영역에서 [지우기]로 초기화하고 다시\n"
            "학습하면 깨끗해집니다. 영역끼리는 서로 영향을 주지 않습니다."
        )
        hdr.setWordWrap(True)
        v.addWidget(hdr)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)
        scroll.setWidget(body)
        v.addWidget(scroll, 1)

        ft = QHBoxLayout(); ft.setSpacing(8); ft.addStretch(1)
        close = QPushButton("닫기"); close.clicked.connect(self.accept)
        close.setDefault(True)
        ft.addWidget(close)
        v.addLayout(ft)

        self._refresh_all()

    # ───────── rendering ─────────
    def _clear_layout(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _refresh_all(self) -> None:
        self._clear_layout()
        for domain in ("hp", "mp", "potion", "legacy"):
            counts = digit_store.instance_counts(domain=None if domain == "legacy" else domain)
            canon = digit_store.read_canonical(domain=None if domain == "legacy" else domain)
            total = sum(counts.values())
            # Skip the legacy bucket entirely when it's empty — most
            # fresh installs will have nothing there and the row would
            # just be visual noise.
            if domain == "legacy" and total == 0:
                continue
            self._body_layout.addWidget(self._build_domain_block(
                domain, counts, total, canon,
            ))
        self._body_layout.addStretch(1)

    def _build_domain_block(self, domain: str,
                              counts: dict[str, int],
                              total: int,
                              canon: Optional[tuple[int, int]]) -> QWidget:
        wrap = QWidget()
        col = QVBoxLayout(wrap); col.setContentsMargins(8, 6, 8, 6); col.setSpacing(4)

        # Domain header row
        head = QHBoxLayout(); head.setSpacing(8)
        title = QLabel(_DOMAIN_TITLES.get(domain, domain))
        f = QFont(); f.setBold(True); f.setPointSize(12); title.setFont(f)
        head.addWidget(title)
        sub_bits: list[str] = [f"총 {total}개"]
        if canon:
            sub_bits.append(f"기준 크기 {canon[0]}×{canon[1]}")
        sub = QLabel(" · ".join(sub_bits))
        sub.setStyleSheet("color:#888;")
        head.addWidget(sub); head.addStretch(1)
        wipe = QPushButton("전체 초기화")
        wipe.setFixedHeight(24)
        wipe.setEnabled(total > 0)
        wipe.clicked.connect(lambda _=False, d=domain: self._on_clear_domain(d))
        head.addWidget(wipe)
        col.addLayout(head)

        # Glyph rows
        for label in GLYPHS:
            row = QHBoxLayout(); row.setContentsMargins(0, 2, 0, 2); row.setSpacing(8)
            display = "/" if label == "/" else label
            tag = QLabel(f"  {display}"); tag.setMinimumWidth(28)
            ff = QFont(); ff.setBold(True); ff.setPointSize(11); tag.setFont(ff)
            row.addWidget(tag)
            count = counts.get(label, 0)
            count_lbl = QLabel(f"{count}개 학습됨" if count > 0 else "학습되지 않음")
            if count == 0:
                count_lbl.setStyleSheet("color:#888;")
            row.addWidget(count_lbl, 1)
            btn = QPushButton("지우기")
            btn.setFixedHeight(22)
            btn.setEnabled(count > 0)
            btn.clicked.connect(
                lambda _=False, lab=label, d=domain: self._on_clear_one(lab, d)
            )
            row.addWidget(btn)
            col.addLayout(row)

        return wrap

    # ───────── actions ─────────
    def _on_clear_one(self, label: str, domain: str) -> None:
        display = "/" if label == "/" else label
        ttl = _DOMAIN_TITLES.get(domain, domain)
        if QMessageBox.question(
            self, "글자 학습 삭제",
            f"[{ttl}] 영역의 '{display}' 학습 데이터를 모두 지울까요?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        digit_store.clear_label(
            label, domain=None if domain == "legacy" else domain,
        )
        self._dirty = True
        self._refresh_all()

    def _on_clear_domain(self, domain: str) -> None:
        ttl = _DOMAIN_TITLES.get(domain, domain)
        if QMessageBox.question(
            self, "영역 학습 초기화",
            f"[{ttl}] 영역의 모든 학습 데이터를 지울까요?\n"
            "되돌릴 수 없습니다.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        digit_store.clear_templates(
            domain=None if domain == "legacy" else domain,
        )
        self._dirty = True
        self._refresh_all()

    # ───────── lifecycle ─────────
    def accept(self) -> None:
        if self._dirty:
            try:
                bus.digit_templates_changed.emit()
            except Exception:
                pass
        super().accept()


__all__ = ["OcrManageDialog"]
