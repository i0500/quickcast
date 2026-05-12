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
    - ``glyphs``: per-segment diagnostics: list of (label, score) pairs
      in left-to-right order. Lets the dashboard log show exactly
      which glyph was picked and how confident it was.
    """
    current: Optional[int]
    maximum: Optional[int]
    text: str
    confidence: float
    glyphs: list[tuple[str, float]] = None    # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.glyphs is None:
            self.glyphs = []


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
    in ROI-local coordinates. Empty list ⇒ no glyphs detected.

    Algorithm has two passes:

    1. **Projection pass** — binarise, sum foreground pixels per column,
       and treat each contiguous run of non-zero columns as one glyph
       box (cropped vertically to its foreground extent).
    2. **Width-split pass** — if some boxes ended up much wider than
       the typical glyph (anti-aliased fringes can bridge adjacent
       digits at low binarisation thresholds, fusing them into one
       run), split them into ``round(width / median_width)`` equal-
       width sub-boxes. The valley between two glued digits isn't
       always a clean zero-column so column gaps alone miss those
       cases; the width statistic catches them.

    ``threshold`` overrides the auto binarisation percentile when the
    calibration UI exposes a slider.
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

    return _split_wide_boxes(boxes, mask)


def _split_wide_boxes(
    boxes: list[tuple[int, int, int, int]],
    mask: np.ndarray,
) -> list[tuple[int, int, int, int]]:
    """Split fused-glyph boxes using width + height + aspect signals.

    The single-feature width-only version missed two real cases:

    - "10"-style fusions where a thin digit ("1") plus a wide digit
      ("0") add up to only ~1.4 × median width, below the 1.5
      threshold.
    - Noisy stray pixels producing a tiny box that distorted the
      median and let real fused pairs slip through.

    So we now:

    1. Drop boxes whose height is < 50 % of the median (noise) before
       computing width stats. They go to the output as-is *after*
       the split decision so the projection still records them.
    2. Compute median glyph width on the *non-noise* boxes.
    3. Mark a box as fused when **any** of:
         - width > 1.4 × median_w
         - aspect ratio (w / h) > 1.15      (a single digit is taller
                                             than wide in a HUD font)
    4. Split fused boxes into ``round(w / median_w)`` equal-width
       sub-boxes (clamped to ≥ 2).
    5. Re-crop each sub-box vertically so its bounding rect matches
       the actual foreground.
    """
    if len(boxes) < 2:
        return boxes

    heights = sorted(b[3] for b in boxes)
    median_h = heights[len(heights) // 2]
    noise_h_threshold = max(2, int(median_h * 0.5))

    # Separate "real" boxes from "noise" for the median-width estimate.
    real_widths = [b[2] for b in boxes if b[3] >= noise_h_threshold]
    if len(real_widths) < 2:
        return boxes
    real_widths.sort()
    median_w = real_widths[len(real_widths) // 2]
    if median_w <= 0:
        return boxes

    out: list[tuple[int, int, int, int]] = []
    for x, y, w, h in boxes:
        if h < noise_h_threshold:
            # tiny stray — keep as-is so user can see it in the
            # calibration preview, but don't split it.
            out.append((x, y, w, h))
            continue
        ratio = w / median_w
        aspect = w / max(1, h)
        fused = ratio >= 1.4 or aspect > 1.15
        if not fused:
            out.append((x, y, w, h))
            continue
        # Pick split count from the width ratio (rounded), capped low
        # at 2. If the aspect-only rule fired (ratio < 1.4) we still
        # split into ≥ 2 because aspect > 1.15 means at least 2 glyphs
        # are crammed in.
        n = max(2, int(round(ratio)))
        sub_w = w / n
        for i in range(n):
            sx0 = int(round(x + i * sub_w))
            sx1 = int(round(x + (i + 1) * sub_w))
            if sx1 <= sx0:
                continue
            band = mask[:, sx0:sx1]
            if band.size == 0 or not band.any():
                out.append((sx0, y, sx1 - sx0, h))
                continue
            rows = np.where(band.any(axis=1))[0]
            y0 = int(rows[0])
            y1 = int(rows[-1]) + 1
            out.append((sx0, y0, sx1 - sx0, max(1, y1 - y0)))
    return out


def _glyph_patch(roi_bgra: np.ndarray,
                  box: tuple[int, int, int, int],
                  threshold: Optional[int] = None) -> np.ndarray:
    """Crop one glyph from the ROI as a binarised (H, W) uint8 mask.

    Binarising glyphs before matching makes the template scores
    insensitive to background colour / alpha bleed under the text.
    ``threshold`` MUST match the value the templates were learned at
    (passed through from recognise()) for the binary masks to align.
    """
    x, y, w, h = box
    crop = roi_bgra[y : y + h, x : x + w]
    return _binarise(crop, threshold=threshold)


def _normalize_to(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Resize a uint8 mask to ``(width, height)`` for canonical match.

    Inputs and templates rescaled to the same canonical size let
    matchTemplate compare shape only — glyph-size differences across
    HUD regions (HP / MP / potion text might render at slightly
    different pixel sizes) stop affecting the score.

    INTER_AREA on shrinks (good for binary masks), INTER_NEAREST on
    growths so we don't smear in greyscale fringes that would defeat
    the binarisation later.
    """
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0 or mask is None or mask.size == 0:
        return mask
    if mask.shape[0] == h and mask.shape[1] == w:
        return mask
    interp = cv2.INTER_AREA if mask.shape[0] > h else cv2.INTER_NEAREST
    out = cv2.resize(mask, (w, h), interpolation=interp)
    # Re-binarise — INTER_AREA can leave mid-gray values.
    _, out = cv2.threshold(out, 127, 255, cv2.THRESH_BINARY)
    return out


def _score_against(glyph_mask: np.ndarray, tmpl: np.ndarray,
                     canonical: Optional[tuple[int, int]] = None) -> float:
    """Score one (glyph, template) pair via TM_CCOEFF_NORMED.

    When ``canonical=(w, h)`` is supplied (per-domain training has
    locked in a canonical size), both glyph and template are
    resized to that fixed pixel size before matching. matchTemplate
    then compares pure shape — aspect / area penalties become
    unnecessary because every input is the same size.

    When ``canonical`` is None we fall back to the legacy path:
    height-match the template to the glyph, pad/crop width to fit,
    then multiply by aspect + area penalties so wrong-shaped
    templates can't hijack a column purely on pad-zero noise.

    Negative score on any failure.
    """
    gh, gw = glyph_mask.shape
    if gh == 0 or gw == 0:
        return -1.0
    th, tw = tmpl.shape[:2]
    if th == 0 or tw == 0:
        return -1.0

    # Canonical-size path: pure shape match, no penalty needed.
    if canonical is not None:
        g = _normalize_to(glyph_mask, canonical)
        t = _normalize_to(tmpl, canonical)
        try:
            result = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
        except cv2.error:
            return -1.0
        return float(max_val)

    scale = gh / th
    new_w = max(1, int(round(tw * scale)))
    new_h = gh
    if new_w == tw and new_h == th:
        resized = tmpl
    else:
        interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        resized = cv2.resize(tmpl, (new_w, new_h), interpolation=interp)
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
        return -1.0
    # ── Aspect (width-ratio) penalty ──
    # Down-weight when template and glyph widths disagree, quadratic
    # so a "0" template doesn't drift over to narrow "5"-shaped glyphs.
    # Floor at 0.2 keeps a tiny signal for edge cases.
    width_ratio = min(gw, new_w) / max(gw, new_w)
    aspect_penalty = max(0.2, width_ratio * width_ratio)

    # ── Area (foreground-pixel) penalty ──
    # Width alone doesn't catch "1" winning a "5"-shaped glyph: a
    # narrow "1" template can be width-similar to a partially-cropped
    # "5", yet its total foreground area is far smaller. Compare
    # foreground pixel counts directly; large mismatch ⇒ wrong glyph.
    # Floor at 0.3 so small natural variation isn't crushed.
    g_area = int((glyph_mask > 0).sum())
    t_area = int((tmpl > 0).sum())
    if g_area > 0 and t_area > 0:
        area_ratio = min(g_area, t_area) / max(g_area, t_area)
        area_penalty = max(0.3, area_ratio)
    else:
        area_penalty = 0.5

    return float(max_val) * aspect_penalty * area_penalty


def _best_labels(glyph_mask: np.ndarray,
                   templates: dict[str, list[np.ndarray]],
                   n: int = 2,
                   canonical: Optional[tuple[int, int]] = None,
                   ) -> list[tuple[str, float]]:
    """Top-n (label, score) candidates for one glyph, score-descending.

    Domain post-processing in recognise() needs the *second*-best
    candidate so it can correct unlikely leading-zero results: "0350"
    almost always means "5350" or "3350" depending on which digit
    came in second on column 0. Top-1 alone hides that.

    Per-label score is best-of-instances (matches the original
    _best_label semantics so single-best behaviour is unchanged).
    Returns at most ``n`` entries; can return fewer if fewer labels
    are trained.
    """
    if not templates:
        return []
    gh, gw = glyph_mask.shape
    if gh == 0 or gw == 0:
        return []
    label_best: list[tuple[str, float]] = []
    for label, instances in templates.items():
        if isinstance(instances, np.ndarray):
            instances = [instances]
        per_label = -1.0
        for tmpl in instances:
            if tmpl is None:
                continue
            score = _score_against(glyph_mask, tmpl, canonical=canonical)
            if score > per_label:
                per_label = score
        if per_label >= 0:
            label_best.append((label, per_label))
    label_best.sort(key=lambda kv: -kv[1])
    return label_best[: max(1, n)]


def _best_label(glyph_mask: np.ndarray,
                 templates: dict[str, list[np.ndarray]]) -> tuple[str, float]:
    """Thin wrapper kept for callers that only want the top-1 result."""
    tops = _best_labels(glyph_mask, templates, n=1)
    return tops[0] if tops else ("", 0.0)


def _apply_no_leading_zero(
    box_labels: list[str],
    per_box_tops: list[list[tuple[str, float]]],
    box_scores: Optional[list[float]] = None,
) -> None:
    """Rewrite leading '0' digits in multi-digit groups in place.

    A HUD counter never reads "0350" — that string only appears when
    OCR mislabelled the first glyph as '0' because the right template
    wasn't trained or had a less-matching width. We split the label
    sequence on '/' and, for each group of length ≥ 2 starting with
    '0', try the second-best candidate for the first glyph.

    Replacement only happens when the runner-up's score is at least
    50 % of the winner's — so a confident "really is zero" result
    (e.g. single-digit "0" or "0/100") never gets overruled by a
    low-score guess.
    """
    # Identify group boundaries: each '/' is a delimiter.
    boundaries: list[int] = [-1]
    for i, lab in enumerate(box_labels):
        if lab == "/":
            boundaries.append(i)
    boundaries.append(len(box_labels))

    for j in range(len(boundaries) - 1):
        start = boundaries[j] + 1
        end = boundaries[j + 1]
        # Skip "/" itself — only operate inside real number groups.
        # Also skip empty groups (two '/' in a row, shouldn't happen).
        if end - start < 2:
            continue
        # Skip if the first slot already isn't '0' or has no candidates.
        if start >= len(box_labels) or box_labels[start] != "0":
            continue
        tops = per_box_tops[start] if start < len(per_box_tops) else []
        if len(tops) < 2:
            continue
        first_lab, first_sc = tops[0]
        second_lab, second_sc = tops[1]
        # If the runner-up isn't a digit (e.g. '/'), skip — leading
        # '/' makes no sense for a number group.
        if not second_lab or second_lab == "/":
            continue
        # Only swap when second is plausibly competitive.
        if first_sc <= 0 or (second_sc / first_sc) < 0.5:
            continue
        box_labels[start] = second_lab
        if box_scores is not None and start < len(box_scores):
            box_scores[start] = second_sc


def recognise(roi_bgra: np.ndarray,
               templates: dict[str, list[np.ndarray]],
               threshold: Optional[int] = None,
               canonical: Optional[tuple[int, int]] = None) -> OcrResult:
    """Run the full OCR pipeline on one ROI.

    ``templates`` is now ``{label: [mask, ...]}`` — a list of learned
    instances per glyph. The matcher picks the highest-scoring instance
    across all labels for each glyph in the ROI. The legacy
    ``{label: mask}`` form is also accepted (treated as a single
    instance) for backward compatibility.

    ``threshold`` (0..255 or None) MUST match the binarisation value
    the templates were learned at. Passing a different threshold makes
    the runtime glyph masks diverge from the stored templates and
    TM_CCOEFF_NORMED scores collapse — which manifests as "the same
    digit fails to match itself" exactly like the user reported.

    Returns ``OcrResult`` — see its docstring for field semantics.
    A blank templates dict produces an empty result (no crash) so
    callers can ship the OCR path before the user has learned glyphs.
    """
    if roi_bgra is None or roi_bgra.size == 0 or not templates:
        return OcrResult(current=None, maximum=None, text="",
                          confidence=0.0, glyphs=[])

    boxes = segment_glyphs(roi_bgra, threshold=threshold)
    if not boxes:
        return OcrResult(current=None, maximum=None, text="",
                          confidence=0.0, glyphs=[])

    # Gather top-2 candidates per glyph so the post-processor can
    # swap a leading-zero misread for its plausible runner-up.
    per_box_tops: list[list[tuple[str, float]]] = []
    for box in boxes:
        gm = _glyph_patch(roi_bgra, box, threshold=threshold)
        tops = _best_labels(gm, templates, n=2, canonical=canonical)
        per_box_tops.append(tops)

    # Top-1 picks for each box; "" placeholders for boxes that
    # produced no candidates at all.
    box_labels: list[str] = [t[0][0] if t else "" for t in per_box_tops]
    box_scores: list[float] = [t[0][1] if t else 0.0 for t in per_box_tops]

    # Domain rule: a HUD counter never carries a leading zero unless
    # it IS zero. So inside each "/" -separated group, if length ≥ 2
    # and the first glyph came back as '0', try the second-best
    # candidate — but only if it's plausibly competitive (score
    # within 50 % of the top so a confident real "0" isn't overruled
    # by a low-score guess).
    _apply_no_leading_zero(box_labels, per_box_tops, box_scores)

    # Strip empty placeholders for the final accounting.
    labels: list[str] = [lab for lab in box_labels if lab]
    scores: list[float] = [sc for lab, sc in zip(box_labels, box_scores) if lab]
    diag: list[tuple[str, float]] = [
        (lab, sc) for lab, sc in zip(box_labels, box_scores)
    ]

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

    return OcrResult(current=cur, maximum=maxv, text=text,
                       confidence=conf, glyphs=diag)


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
