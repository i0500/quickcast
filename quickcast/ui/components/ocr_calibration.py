"""One-shot digit-template learning dialog.

Shows the user the live capture of a HUD text region, splits it into
candidate glyph boxes via the OCR engine's segmenter, and asks for the
ground-truth string. Each segmented box becomes a learned template,
stored to disk via core.digit_store.

UX flow
-------
1. Caller passes a BGRA numpy array (the cropped text ROI) plus the
   suggested ground-truth (e.g. read from settings, if the user has
   typed it once before).
2. Dialog renders the ROI 4× upscaled, with green outlines on every
   detected glyph and an index label above each.
3. The user types the ground-truth ("1234/5678") into the input.
4. When the typed string's character count matches the detected box
   count, the [저장] button activates. Mismatch → red hint label.
5. On Save: zip the boxes with the characters in order, extract each
   binarised crop as a template, write through digit_store. Caller
   gets the templates dict back.

This is intentionally one dialog per text region — running the dialog
three times (HP / MP / potion) gives a more reliable training set than
trying to learn from any one field alone.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QImage, QPainter, QPen, QPixmap, QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from quickcast.core.ocr import segment_glyphs, _binarise
from quickcast.core.digit_store import load_templates, save_templates


_DISPLAY_SCALE = 4    # pixel zoom — small HUD glyphs are 8-12 px tall


class _RoiCanvas(QWidget):
    """Renders the upscaled ROI + glyph box overlays."""

    def __init__(self, roi_bgra: np.ndarray, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.roi = roi_bgra
        h, w = roi_bgra.shape[:2]
        self.setFixedSize(QSize(w * _DISPLAY_SCALE, h * _DISPLAY_SCALE))
        self.boxes: list[tuple[int, int, int, int]] = segment_glyphs(roi_bgra)

    def refresh_boxes(self) -> None:
        self.boxes = segment_glyphs(self.roi)
        self.update()

    def paintEvent(self, _e) -> None:
        if self.roi is None or self.roi.size == 0:
            return
        p = QPainter(self)
        # Convert BGRA → QImage (Qt expects ARGB32 byte order on little-
        # endian; mss/PrintWindow already produce BGRA so this maps 1:1).
        h, w = self.roi.shape[:2]
        if self.roi.flags["C_CONTIGUOUS"]:
            buf = self.roi
        else:
            buf = np.ascontiguousarray(self.roi)
        qimg = QImage(buf.data, w, h, w * 4, QImage.Format_ARGB32)
        pm = QPixmap.fromImage(qimg).scaled(
            w * _DISPLAY_SCALE, h * _DISPLAY_SCALE,
            Qt.IgnoreAspectRatio, Qt.FastTransformation,
        )
        p.drawPixmap(0, 0, pm)

        # Glyph boxes
        pen = QPen(QColor(0, 220, 90), 2)
        p.setPen(pen)
        f = p.font(); f.setBold(True); f.setPointSize(9); p.setFont(f)
        for i, (x, y, bw, bh) in enumerate(self.boxes, 1):
            rx = x * _DISPLAY_SCALE
            ry = y * _DISPLAY_SCALE
            rw = bw * _DISPLAY_SCALE
            rh = bh * _DISPLAY_SCALE
            p.drawRect(rx, ry, rw, rh)
            # index label above the box
            p.drawText(rx, max(12, ry - 2), str(i))


class OcrCalibrationDialog(QDialog):
    """Dialog wrapper around _RoiCanvas + truth input + save."""

    def __init__(self, roi_bgra: np.ndarray,
                  suggested_truth: str = "",
                  parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("OCR 글자 학습")
        self.setModal(True)
        self.setMinimumWidth(560)

        self._roi = roi_bgra
        self._templates: dict[str, np.ndarray] = {}

        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        info = QLabel(
            "아래 이미지는 캡쳐된 텍스트 영역입니다.\n"
            "초록 박스는 자동 검출된 글자입니다 — 박스 위 번호 순서대로\n"
            "정답을 입력해주세요. 예: 1234/5678 또는 0"
        )
        info.setWordWrap(True)
        v.addWidget(info)

        # Centred canvas
        canvas_row = QHBoxLayout(); canvas_row.addStretch(1)
        self._canvas = _RoiCanvas(roi_bgra, self)
        canvas_row.addWidget(self._canvas); canvas_row.addStretch(1)
        v.addLayout(canvas_row)

        # Detected count line
        self._count_lbl = QLabel("")
        self._count_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self._count_lbl)

        # Truth input
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(QLabel("정답:"))
        self._input = QLineEdit(suggested_truth)
        f = QFont(); f.setPointSize(13); self._input.setFont(f)
        self._input.textChanged.connect(self._refresh_state)
        row.addWidget(self._input, 1)
        v.addLayout(row)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#d35454;")
        v.addWidget(self._status)

        # Buttons
        btns = QHBoxLayout(); btns.setSpacing(8); btns.addStretch(1)
        cancel = QPushButton("취소"); cancel.clicked.connect(self.reject)
        self._save = QPushButton("저장"); self._save.clicked.connect(self._on_save)
        self._save.setDefault(True)
        btns.addWidget(cancel); btns.addWidget(self._save)
        v.addLayout(btns)

        self._refresh_state()

    def _refresh_state(self) -> None:
        n = len(self._canvas.boxes)
        text = self._input.text()
        # Drop whitespace — users sometimes type "1234 / 5678".
        text = "".join(ch for ch in text if not ch.isspace())
        self._count_lbl.setText(f"검출된 글자: {n}개")
        if not text:
            self._status.setText("정답을 입력해주세요.")
            self._save.setEnabled(False)
            return
        if n == 0:
            self._status.setText("자동 검출 실패 — 영역을 다시 잡아주세요.")
            self._save.setEnabled(False)
            return
        if len(text) != n:
            self._status.setText(
                f"입력된 글자 수({len(text)})가 검출된 글자 수({n})와 다릅니다."
            )
            self._save.setEnabled(False)
            return
        # Validate characters
        valid = set("0123456789/")
        bad = [ch for ch in text if ch not in valid]
        if bad:
            self._status.setText(
                f"지원되지 않는 문자: {' '.join(sorted(set(bad)))}"
            )
            self._save.setEnabled(False)
            return
        self._status.setText("")
        self._save.setEnabled(True)

    def _on_save(self) -> None:
        text = "".join(ch for ch in self._input.text() if not ch.isspace())
        boxes = self._canvas.boxes
        if len(text) != len(boxes) or not boxes:
            return

        # Merge with whatever's already on disk so multiple training
        # passes (HP then MP) accumulate templates instead of replacing.
        existing = load_templates()
        new_templates: dict[str, np.ndarray] = dict(existing)
        for ch, (x, y, w, h) in zip(text, boxes):
            crop = self._roi[y : y + h, x : x + w]
            mask = _binarise(crop)
            # Trim to actual foreground in case binarisation produced
            # padded margins (slight overhang of the segment box).
            ys = np.where(mask.any(axis=1))[0]
            xs = np.where(mask.any(axis=0))[0]
            if ys.size == 0 or xs.size == 0:
                continue
            mask = mask[ys[0] : ys[-1] + 1, xs[0] : xs[-1] + 1]
            new_templates[ch] = mask

        save_templates(new_templates)
        self._templates = new_templates
        self.accept()

    def templates(self) -> dict[str, np.ndarray]:
        """Return the templates dict the user just saved (empty on cancel)."""
        return self._templates


__all__ = ["OcrCalibrationDialog"]
