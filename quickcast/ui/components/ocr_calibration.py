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
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from quickcast.core.ocr import segment_glyphs, _binarise
from quickcast.core.digit_store import load_templates, save_templates


_DISPLAY_SCALE = 4    # pixel zoom — small HUD glyphs are 8-12 px tall


class _RoiCanvas(QWidget):
    """Renders the upscaled ROI + glyph box overlays.

    ``threshold`` (0..255 or None) controls the binarisation cutoff used
    for segmentation; ``show_binarised`` swaps the background between
    the original image and the white/black mask so the user can see
    exactly what the segmenter sees.
    """

    def __init__(self, roi_bgra: np.ndarray, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.roi = roi_bgra
        h, w = roi_bgra.shape[:2]
        self.setFixedSize(QSize(w * _DISPLAY_SCALE, h * _DISPLAY_SCALE))
        self.threshold: Optional[int] = None    # None ⇒ auto percentile
        self.show_binarised: bool = False
        self.boxes: list[tuple[int, int, int, int]] = []
        self.refresh_boxes()

    def set_threshold(self, value: Optional[int]) -> None:
        self.threshold = value
        self.refresh_boxes()

    def set_show_binarised(self, on: bool) -> None:
        self.show_binarised = bool(on)
        self.update()

    def refresh_boxes(self) -> None:
        self.boxes = segment_glyphs(self.roi, threshold=self.threshold)
        self.update()

    def paintEvent(self, _e) -> None:
        from quickcast.core.ocr import _binarise
        if self.roi is None or self.roi.size == 0:
            return
        p = QPainter(self)
        h, w = self.roi.shape[:2]

        # Pick the background image: raw frame or the binarised mask
        # that the segmenter actually sees. Either way upscale to the
        # display size with nearest-neighbour for crisp pixel edges.
        if self.show_binarised:
            mask = _binarise(self.roi, threshold=self.threshold)
            # Convert single-channel mask to BGRA for QImage.
            bgra = np.zeros((h, w, 4), dtype=np.uint8)
            bgra[..., 0] = mask
            bgra[..., 1] = mask
            bgra[..., 2] = mask
            bgra[..., 3] = 255
            buf = np.ascontiguousarray(bgra)
        else:
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
        self._templates: dict[str, list[np.ndarray]] = {}
        # Per-label count of glyphs added by THIS dialog session.
        # Capture-section reads it on success to show "added X samples"
        # toast that nudges the user toward more training passes.
        self._added_per_label: dict[str, int] = {}

        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        info = QLabel(
            "아래 이미지는 캡쳐된 텍스트 영역입니다.\n"
            "초록 박스가 글자별로 깔끔히 나뉠 때까지 임계값을 조절해주세요.\n"
            "박스 개수가 정답 글자 수와 같아지면 [저장]이 활성화됩니다."
        )
        info.setWordWrap(True)
        v.addWidget(info)

        # Centred canvas
        canvas_row = QHBoxLayout(); canvas_row.addStretch(1)
        self._canvas = _RoiCanvas(roi_bgra, self)
        canvas_row.addWidget(self._canvas); canvas_row.addStretch(1)
        v.addLayout(canvas_row)

        # ── Threshold slider + binarised-preview toggle ──
        thr_row = QHBoxLayout(); thr_row.setSpacing(8)
        thr_label = QLabel("임계값 (자동)")
        thr_label.setMinimumWidth(110)
        self._thr_slider = QSlider(Qt.Horizontal)
        self._thr_slider.setRange(0, 255)
        # 0 == "auto" sentinel. Start there so first paint uses auto.
        self._thr_slider.setValue(0)

        def _on_thr(v: int) -> None:
            if v <= 0:
                thr_label.setText("임계값 (자동)")
                self._canvas.set_threshold(None)
            else:
                thr_label.setText(f"임계값 {v}")
                self._canvas.set_threshold(int(v))
            self._refresh_state()
        self._thr_slider.valueChanged.connect(_on_thr)
        thr_row.addWidget(thr_label); thr_row.addWidget(self._thr_slider, 1)

        self._show_bin = QCheckBox("이진화 보기")
        self._show_bin.toggled.connect(self._canvas.set_show_binarised)
        thr_row.addWidget(self._show_bin)
        v.addLayout(thr_row)

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

        # APPEND to the on-disk instance list — don't overwrite. Every
        # training pass adds fresh per-glyph samples so the matcher can
        # pick the best-fitting one at inference. After many passes the
        # store ends up with e.g. 7 distinct "9" masks (from "999/2999",
        # "1099/4099", …), which makes OCR robust to subtle rendering
        # differences from one capture to the next.
        existing = load_templates()
        # Copy so we don't mutate the loaded dict's lists in place.
        new_templates: dict[str, list[np.ndarray]] = {
            k: list(v) for k, v in existing.items()
        }
        thr_val = self._canvas.threshold
        added_per_label: dict[str, int] = {}
        for ch, (x, y, w, h) in zip(text, boxes):
            crop = self._roi[y : y + h, x : x + w]
            mask = _binarise(crop, threshold=thr_val)
            # Trim to actual foreground in case binarisation produced
            # padded margins (slight overhang of the segment box).
            ys = np.where(mask.any(axis=1))[0]
            xs = np.where(mask.any(axis=0))[0]
            if ys.size == 0 or xs.size == 0:
                continue
            mask = mask[ys[0] : ys[-1] + 1, xs[0] : xs[-1] + 1]
            new_templates.setdefault(ch, []).append(mask)
            added_per_label[ch] = added_per_label.get(ch, 0) + 1

        save_templates(new_templates)
        self._templates = new_templates
        self._added_per_label = added_per_label
        self.accept()

    def templates(self) -> dict[str, list[np.ndarray]]:
        """Return the templates dict the user just saved (empty on cancel)."""
        return self._templates

    def added_summary(self) -> dict[str, int]:
        """{label: instances_added_this_session}. Empty on cancel."""
        return dict(self._added_per_label)

    def total_instances(self) -> int:
        """Total instances stored across all labels (post-save)."""
        return sum(len(v) for v in self._templates.values())

    def chosen_threshold(self) -> Optional[int]:
        """The binarisation threshold value the user landed on.

        Returns the slider value (1..255) or ``None`` for auto. The
        caller persists this to Settings so inference binarises the
        same way training did — without this, the saved glyph masks
        and the live capture masks diverge and TM_CCOEFF_NORMED never
        reaches 1.0 even on a pixel-identical capture.
        """
        return self._canvas.threshold


__all__ = ["OcrCalibrationDialog"]
