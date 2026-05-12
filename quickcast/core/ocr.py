"""Small fixed-font OCR for HUD text like "1234/5678".

Designed for the case where the font is identical across the screen
(common in MMORPG HUDs) so a tiny per-glyph template-matching pipeline
beats heavyweight OCR libraries in every axis: latency (< 5ms), binary
size (zero deps), and accuracy (≥ 99% on the trained font).

Pipeline
--------
1. **Binarise** the ROI to white-text / dark-background using a
   percentile-driven brightness threshold. Robust against the HUD's
   slight transparency-on-game-scene effect because the text is always
   far brighter than the underlying scene.
2. **Segment glyphs** via the vertical projection profile — runs of
   columns with zero foreground pixels separate adjacent characters.
3. **Match each glyph crop** against every known digit template
   ('0'..'9' plus '/'). Templates are resized to the glyph's height so
   minor zoom drift between learning capture and inference capture
   doesn't tank the score.
4. **Pick the best-scoring label** per glyph; concatenate to the
   recognised string. The string is then parsed for the "cur/max"
   shape and returned as (current, maximum, raw_text).

Templates
---------
The 11 templates ('0'..'9' and '/') are learned interactively from the
running game by the dashboard. See ``calibration.py`` (next step) for
the segment-then-label flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


# Glyphs we recognise. '/' is the cur/max separator in HUDs like
# "1234/5678" — we include it so we can split the field cleanly without
# heuristics on glyph spacing.
GLYPHS: tuple[str, ...] = ("0", "1", "2", "3", "4", "5", "6", "7",
                              "8", "9", "/")


@dataclass
class OcrResult:
    """One OCR pass against a HUD text region.

    - ``current`` / ``maximum``: integers parsed from the recognised
      "cur/max" pattern. Either may be None if parsing failed or the
      template set is empty.
    - ``text``: the raw concatenated label string ("1234/5678", "5/5",
      "0", …). Useful for diagnostics and for single-value targets
      like the potion counter.
    - ``confidence``: mean match score (0..1) across the recognised
      glyphs. Below ~0.6 the result should not be trusted.
    """
    current: Optional[int]
    maximum: Optional[int]
    text: str
    confidence: float


def _binarise(roi_bgra: np.ndarray,
                threshold: Optional[int] = None) -> np.ndarray:
    """Return a uint8 (H, W) mask where bright text pixels are 255.

    ``threshold`` (0..255) lets callers override the auto-picked value
    from the calibration UI — when the user slides it they get to see
    segmentation update in real-time. ``None`` means "auto": use the
    75th-percentile brightness, clamped to ≥140 so empty/dim frames
    don't produce a noise-driven mask.
    """
    if roi_bgra.ndim == 3:
        gray = cv2.cvtColor(roi_bgra[:, :, :3], cv2.COLOR_BGR2GRAY)
    else:
        gray = roi_bgra
    if threshold is None:
        thr = max(int(np.percentile(gray, 75)), 140)
    else:
        thr = int(max(0, min(255, threshold)))
    _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
    return mask


def segment_glyphs(roi_bgra: np.ndarray,
                     min_glyph_w: int = 2,
                     min_glyph_h: int = 4,
                     threshold: Optional[int] = None,
                     ) -> list[tuple[int, int, int, int]]:
    """Find bounding boxes for each glyph in a HUD text ROI.

    Returns a list of ``(x, y, w, h)`` boxes, left-to-right. Boxes are
    in ROI-local coordinates. Empty list ⇒ no glyphs detected (likely
    a bad ROI or no text present). ``threshold`` overrides the auto
    binarisation percentile when the calibration UI exposes a slider.

    Algorithm: binarise → vertical-projection profile → runs of >0
    foreground pixels delimit glyph columns. Each column run is then
    cropped vertically to the foreground extent for that glyph.
    """
    mask = _binarise(roi_bgra, threshold=threshold)
    h, w = mask.shape
    if h == 0 or w == 0:
        return []

    col_sums = (mask > 0).sum(axis=0)
    active = col_sums > 0

    boxes: list[tuple[int, int, int, int]] = []
    in_run = False
    run_x0 = 0
    for x in range(w):
        if active[x] and not in_run:
            in_run = True
            run_x0 = x
        elif not active[x] and in_run:
            in_run = False
            x1 = x
            gw = x1 - run_x0
            if gw < min_glyph_w:
                continue
            band = mask[:, run_x0:x1]
            rows = np.where(band.any(axis=1))[0]
            if rows.size == 0:
                continue
            y0, y1 = int(rows[0]), int(rows[-1]) + 1
            gh = y1 - y0
            if gh < min_glyph_h:
                continue
            boxes.append((run_x0, y0, gw, gh))
    # Flush a trailing run that hit the right edge without a 0-column.
    if in_run:
        x1 = w
        gw = x1 - run_x0
        if gw >= min_glyph_w:
            band = mask[:, run_x0:x1]
            rows = np.where(band.any(axis=1))[0]
            if rows.size > 0:
                y0, y1 = int(rows[0]), int(rows[-1]) + 1
                if (y1 - y0) >= min_glyph_h:
                    boxes.append((run_x0, y0, gw, y1 - y0))
    return boxes


def _glyph_patch(roi_bgra: np.ndarray,
                  box: tuple[int, int, int, int]) -> np.ndarray:
    """Crop one glyph from the ROI as a binarised (H, W) uint8 mask.

    Binarising glyphs before matching makes the template scores
    insensitive to background colour / alpha bleed under the text.
    """
    x, y, w, h = box
    crop = roi_bgra[y : y + h, x : x + w]
    return _binarise(crop)


def _best_label(glyph_mask: np.ndarray,
                 templates: dict[str, np.ndarray]) -> tuple[str, float]:
    """Match a glyph against every template; return (label, score).

    Templates are resized to the glyph's height so the matcher only sees
    same-size inputs (cv2.matchTemplate requires it). Width is scaled
    proportionally. Score is the TM_CCOEFF_NORMED value at (0, 0) since
    both inputs are the same size and we just want a similarity score.
    """
    if not templates:
        return ("", 0.0)
    gh, gw = glyph_mask.shape
    if gh == 0 or gw == 0:
        return ("", 0.0)
    best = ("", -1.0)
    for label, tmpl in templates.items():
        th, tw = tmpl.shape[:2]
        if th == 0 or tw == 0:
            continue
        # Scale template to match glyph height; keep aspect ratio.
        scale = gh / th
        new_w = max(1, int(round(tw * scale)))
        new_h = gh
        if new_w == tw and new_h == th:
            resized = tmpl
        else:
            interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            resized = cv2.resize(tmpl, (new_w, new_h), interpolation=interp)
        # Need glyph as wide as the template to match. If glyph is
        # narrower, pad it with zeros (matches "skinny digit hit by
        # binarisation noise" failure mode). If wider, crop centre.
        if gw < new_w:
            pad_total = new_w - gw
            pad_l = pad_total // 2
            pad_r = pad_total - pad_l
            search = cv2.copyMakeBorder(glyph_mask, 0, 0, pad_l, pad_r,
                                          cv2.BORDER_CONSTANT, value=0)
        elif gw > new_w:
            x0 = (gw - new_w) // 2
            search = glyph_mask[:, x0 : x0 + new_w]
        else:
            search = glyph_mask
        try:
            result = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
        except cv2.error:
            continue
        if max_val > best[1]:
            best = (label, float(max_val))
    return best


def recognise(roi_bgra: np.ndarray,
               templates: dict[str, np.ndarray]) -> OcrResult:
    """Run the full OCR pipeline on one ROI.

    Returns ``OcrResult`` — see its docstring for field semantics.
    A blank templates dict produces an empty result (no crash) so
    callers can ship the OCR path before the user has learned glyphs.
    """
    if roi_bgra is None or roi_bgra.size == 0 or not templates:
        return OcrResult(current=None, maximum=None, text="", confidence=0.0)

    boxes = segment_glyphs(roi_bgra)
    if not boxes:
        return OcrResult(current=None, maximum=None, text="", confidence=0.0)

    labels: list[str] = []
    scores: list[float] = []
    for box in boxes:
        gm = _glyph_patch(roi_bgra, box)
        lab, sc = _best_label(gm, templates)
        if lab:
            labels.append(lab)
            scores.append(sc)

    text = "".join(labels)
    conf = float(sum(scores) / len(scores)) if scores else 0.0

    cur: Optional[int] = None
    maxv: Optional[int] = None
    if "/" in text:
        left, _, right = text.partition("/")
        try:
            cur = int(left) if left else None
        except ValueError:
            cur = None
        try:
            maxv = int(right) if right else None
        except ValueError:
            maxv = None
    else:
        # Single number — single-counter HUD field like potion count.
        try:
            cur = int(text) if text else None
        except ValueError:
            cur = None

    return OcrResult(current=cur, maximum=maxv, text=text, confidence=conf)


def hp_percentage(result: OcrResult) -> Optional[int]:
    """Convert an OcrResult into a 0..100 percentage.

    Returns None when the values aren't usable (no '/', divide-by-zero,
    confidence below trust threshold). Callers fall back to colour-based
    detection in that case.
    """
    if result.current is None or result.maximum is None or result.maximum <= 0:
        return None
    if result.confidence < 0.55:
        return None
    pct = int(round(result.current * 100.0 / result.maximum))
    return max(0, min(100, pct))


__all__ = [
    "GLYPHS", "OcrResult",
    "segment_glyphs", "recognise", "hp_percentage",
]
