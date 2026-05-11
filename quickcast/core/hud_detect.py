"""Heuristic HUD region detection.

Given a captured frame (BGRA, 1280×720) we try to locate the rectangles
that hold HP / MP / potion-count text without any user input. The
algorithm is intentionally simple — get the user 80% of the way there
and let them drag the ROI a few pixels if it's slightly off.

Heuristics
----------
* **HP bar**: the longest horizontal run of red-dominant pixels in the
  upper-left quadrant of the frame. The accompanying "cur/max" text
  sits right next to it, either centred on the bar or just above/below.
  We expand a small text-candidate window adjacent to the bar.
* **MP bar**: same as HP but blue-dominant, usually one bar height
  below the HP bar.
* **Potion count**: a small bright-text cluster in the lower band of
  the screen, near the hotkey row. We scan the lower half for compact
  bright-text clusters and pick the topmost short candidate.

This isn't pixel-perfect — it can't be without per-game template
calibration — but it gets the user out of the "drag a 5px box around
a 20px-wide UI element" hell. The dashboard wraps the result in a
"detected, confirm or drag" prompt before saving.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass
class RegionGuess:
    """One auto-detected ROI. All coords are in the 1280×720 frame."""
    x: int
    y: int
    w: int
    h: int
    confidence: float    # 0..1, very rough — only meaningful for ranking


def _red_mask(bgr: np.ndarray) -> np.ndarray:
    """Pixels where R dominates over B and G — typical HP bar fill."""
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    mask = ((r > 110) & (r > b + 35) & (r > g + 25)).astype(np.uint8) * 255
    return mask


def _blue_mask(bgr: np.ndarray) -> np.ndarray:
    """Pixels where B dominates — typical MP bar fill."""
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    mask = ((b > 110) & (b > r + 35) & (b > g + 10)).astype(np.uint8) * 255
    return mask


def _longest_horizontal_run(mask: np.ndarray) -> tuple[int, int, int, int]:
    """Find the (x0, y, x1, h) of the longest contiguous horizontal bar.

    "Bar" here means a connected component that is much wider than it
    is tall (aspect ≥ 4) — the HP / MP fills fit. Returns zeros if
    nothing matches.
    """
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best = (0, 0, 0, 0)
    best_w = 0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h <= 0:
            continue
        if w >= 60 and w / max(1, h) >= 4 and area >= w * 0.5:
            if w > best_w:
                best_w = w
                best = (int(x), int(y), int(x + w), int(h))
    return best


def detect_hp_bar(frame_bgra: np.ndarray) -> Optional[RegionGuess]:
    """Find the HP bar's bounding box. Limited to the top-left quadrant."""
    h, w = frame_bgra.shape[:2]
    upper_left = frame_bgra[: h // 3, : w // 2, :3]
    mask = _red_mask(upper_left)
    x0, yb, x1, hb = _longest_horizontal_run(mask)
    if x1 <= x0:
        return None
    return RegionGuess(x=x0, y=yb, w=x1 - x0, h=hb, confidence=0.7)


def detect_mp_bar(frame_bgra: np.ndarray) -> Optional[RegionGuess]:
    """Find the MP bar's bounding box. Limited to the top-left quadrant."""
    h, w = frame_bgra.shape[:2]
    upper_left = frame_bgra[: h // 3, : w // 2, :3]
    mask = _blue_mask(upper_left)
    x0, yb, x1, hb = _longest_horizontal_run(mask)
    if x1 <= x0:
        return None
    return RegionGuess(x=x0, y=yb, w=x1 - x0, h=hb, confidence=0.7)


def text_window_for_bar(bar: RegionGuess,
                          frame_w: int, frame_h: int) -> RegionGuess:
    """Expand a bar bounding box into a likely text region.

    HUD numbers usually sit centred over the bar (covering it) or just
    above. We produce a rect that spans roughly the bar's width and
    twice its height, centred vertically on the bar, so the user only
    needs to nudge it a few pixels if the actual text is offset.
    """
    cy = bar.y + bar.h // 2
    th = max(12, bar.h * 3)
    ty = max(0, cy - th // 2)
    if ty + th > frame_h:
        th = frame_h - ty
    # Expand horizontally by 10% on each side to catch numbers that
    # overshoot the bar's left/right edges (common when bars are full).
    pad = max(4, bar.w // 10)
    tx = max(0, bar.x - pad)
    tw = min(frame_w - tx, bar.w + pad * 2)
    return RegionGuess(x=tx, y=ty, w=tw, h=th, confidence=bar.confidence * 0.9)


def detect_potion_count(frame_bgra: np.ndarray) -> Optional[RegionGuess]:
    """Best-effort scan for a small bright-text cluster in the hotkey strip.

    We project the bright-pixel mask along the horizontal axis in the
    bottom third of the frame, find narrow tall clusters, and return
    the topmost one. Hit rate isn't perfect — this is meant as a seed
    the user can drag a few pixels, not a guarantee.
    """
    h, w = frame_bgra.shape[:2]
    band = frame_bgra[2 * h // 3 :, :, :3]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    # Suppress thin specular noise — open by a 2×2 kernel.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best: Optional[RegionGuess] = None
    for i in range(1, n):
        x, y, cw, ch, area = stats[i]
        # Reject big, bar-like, or near-square things and tiny noise.
        if cw < 8 or ch < 8 or cw > 60 or ch > 40:
            continue
        if cw / max(1, ch) > 4.0:
            continue
        if area < cw * ch * 0.2:
            continue
        candidate = RegionGuess(
            x=int(x) - 4, y=int(y) + 2 * h // 3 - 2,
            w=int(cw) + 8, h=int(ch) + 4,
            confidence=0.4,
        )
        if best is None or candidate.y < best.y:
            best = candidate
    return best


def auto_detect_all(frame_bgra: np.ndarray) -> dict[str, RegionGuess]:
    """Run all three detectors. Keys absent ⇒ detector returned None.

    The returned regions are *text candidates* (already expanded around
    the bars). Caller writes them straight into Settings.*_text_cap.
    """
    h, w = frame_bgra.shape[:2]
    out: dict[str, RegionGuess] = {}
    hp_bar = detect_hp_bar(frame_bgra)
    if hp_bar is not None:
        out["hp"] = text_window_for_bar(hp_bar, w, h)
    mp_bar = detect_mp_bar(frame_bgra)
    if mp_bar is not None:
        out["mp"] = text_window_for_bar(mp_bar, w, h)
    potion = detect_potion_count(frame_bgra)
    if potion is not None:
        out["potion"] = potion
    return out


__all__ = ["RegionGuess", "detect_hp_bar", "detect_mp_bar",
            "detect_potion_count", "text_window_for_bar", "auto_detect_all"]
