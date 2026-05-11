"""Image recognition — HP/MP percentage + PK/potion template matching.

Algorithms ported 1:1 from the original JavaScript so behaviour is
indistinguishable to the user. Implementation is vectorised with NumPy
which is roughly an order of magnitude faster than the OpenCV.js + JS
loop pipeline it replaces.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from quickcast.config import PkSlot, PotionSlot, Settings
from quickcast.core.capture import Frame

TARGETS_DIR = Path(__file__).resolve().parent.parent / "data" / "targets"


@dataclass
class FrameAnalysis:
    """Result of one capture-loop iteration."""
    hp: int                   # 0..100
    mp: int                   # 0..100
    pk_detected: bool
    pk_score: float
    potion_empty: bool
    potion_score: float


def _hp_ratio(roi_bgra: np.ndarray) -> int:
    """Read HP bar fill percentage.

    Original algorithm:
      - Take Red channel of the ROI
      - 5x5 box blur
      - Threshold at 210 → binary mask
      - For each row, find the rightmost '255' pixel
      - The narrowest bar (min from-right index) defines current ratio
      - Special case: a result of 0 is almost always a misread → 100
    """
    if roi_bgra.size == 0:
        return 100
    red = roi_bgra[:, :, 2]
    blurred = cv2.blur(red, (5, 5))
    _, mask = cv2.threshold(blurred, 210, 255, cv2.THRESH_BINARY)

    # Per-row distance from right edge to first 255 pixel
    width = mask.shape[1]
    flipped = mask[:, ::-1]
    # argmax returns 0 if no 255 found, so guard with `any`
    has_match = flipped.any(axis=1)
    first_idx = flipped.argmax(axis=1)
    # rows with no match -> treat as full-empty (= width)
    first_idx = np.where(has_match, first_idx, width)

    min_idx = int(first_idx.min())
    ratio = round((width - min_idx) / width * 100)
    return 100 if ratio == 0 else int(ratio)


# MP colour brackets — primary BGRA range (matches the original macro's
# sample) plus a more permissive secondary range. mss/PrintWindow output
# BGRA so R and B are swapped relative to the original JS RGBA values.
# We try the tight range first; if it returns 0% (no match), fall back
# to the wider range so a slightly different game build / display
# colour profile still reads MP correctly.
_MP_LOW = np.array([125, 110, 60, 0], dtype=np.uint8)
_MP_HIGH = np.array([200, 200, 120, 255], dtype=np.uint8)
_MP_LOW_WIDE = np.array([90, 80, 30, 0], dtype=np.uint8)
_MP_HIGH_WIDE = np.array([255, 230, 160, 255], dtype=np.uint8)


def _mp_ratio_via_mask(roi_bgra: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[int, int]:
    """Returns (percent, total_match_pixels). 0 pixels means no MP-coloured area."""
    mask = cv2.inRange(roi_bgra, lo, hi)
    width = mask.shape[1]
    flipped = mask[:, ::-1]
    has_match = flipped.any(axis=1)
    first_idx = flipped.argmax(axis=1)
    first_idx = np.where(has_match, first_idx, width)
    min_idx = int(first_idx.min())
    pct = int(round((width - min_idx) / width * 100))
    return pct, int((mask > 0).sum())


def _mp_ratio_via_hsv(roi_bgra: np.ndarray) -> tuple[int, int]:
    """HSV-based fallback for MP detection — robust to colour shifts.

    MP bar is consistently cyan/blue-ish (hue 90-130 in OpenCV's 0-180
    space). Saturation/value lower bounds filter out neutral pixels.
    """
    bgr = roi_bgra[:, :, :3]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lo = np.array([85, 70, 70], dtype=np.uint8)
    hi = np.array([135, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lo, hi)
    width = mask.shape[1]
    flipped = mask[:, ::-1]
    has_match = flipped.any(axis=1)
    first_idx = flipped.argmax(axis=1)
    first_idx = np.where(has_match, first_idx, width)
    min_idx = int(first_idx.min())
    pct = int(round((width - min_idx) / width * 100))
    return pct, int((mask > 0).sum())


_MP_DIAG_LIMIT = 8           # log diagnostic at most this many times per session
_mp_diag_count = 0


def _mp_ratio_via_blue_channel(roi_bgra: np.ndarray) -> tuple[int, int]:
    """Blue-channel threshold — mirrors HP's red-channel approach.

    Most useful when the MP bar's exact colour is hard to bracket in
    BGR/HSV (e.g. heavily anti-aliased or skinned UI). We just look for
    'blue-dominant' pixels: B > 120 AND B > R + 30.
    """
    b = roi_bgra[:, :, 0].astype(np.int16)
    r = roi_bgra[:, :, 2].astype(np.int16)
    mask = ((b > 120) & (b > r + 30)).astype(np.uint8) * 255
    width = mask.shape[1]
    flipped = mask[:, ::-1]
    has_match = flipped.any(axis=1)
    first_idx = flipped.argmax(axis=1)
    first_idx = np.where(has_match, first_idx, width)
    min_idx = int(first_idx.min())
    pct = int(round((width - min_idx) / width * 100))
    return pct, int((mask > 0).sum())


def _mp_ratio(roi_bgra: np.ndarray) -> int:
    """Read MP bar fill percentage with a 4-step fallback ladder."""
    global _mp_diag_count
    if roi_bgra.size == 0:
        return 100
    pct, hits = _mp_ratio_via_mask(roi_bgra, _MP_LOW, _MP_HIGH)
    used = "tight"
    if hits == 0:
        pct, hits = _mp_ratio_via_mask(roi_bgra, _MP_LOW_WIDE, _MP_HIGH_WIDE)
        used = "wide"
    if hits == 0 and roi_bgra.shape[2] >= 3:
        pct, hits = _mp_ratio_via_hsv(roi_bgra)
        used = "hsv"
    if hits == 0 and roi_bgra.shape[2] >= 3:
        pct, hits = _mp_ratio_via_blue_channel(roi_bgra)
        used = "blue"
    if hits == 0 and _mp_diag_count < _MP_DIAG_LIMIT:
        try:
            from quickcast.utils.logger import logger
            mean_bgr = roi_bgra[:, :, :3].reshape(-1, 3).mean(axis=0)
            logger.debug(
                f"MP detection returned 0 — ROI shape={roi_bgra.shape}, "
                f"mean BGR=({mean_bgr[0]:.0f},{mean_bgr[1]:.0f},{mean_bgr[2]:.0f}) "
                f"[ladder exhausted: {used}]"
            )
            _mp_diag_count += 1
        except Exception:
            pass
    return pct


_TS_DIAG = {"last": 0.0}    # throttled raw-score logger
_ROI_FIX_REPORTED: dict[str, bool] = {}    # log ROI auto-fix once per kind


def _template_score(roi_bgra: np.ndarray, target_bgra: np.ndarray,
                     scale: float = 1_000_000.0,
                     _kind: str = "") -> float:
    """Normalised correlation, rescaled to legacy threshold magnitudes."""
    from quickcast.utils.logger import logger
    import time as _t
    if roi_bgra is None or target_bgra is None:
        return 0.0
    if roi_bgra.size == 0 or target_bgra.size == 0:
        return 0.0
    if roi_bgra.shape[0] < target_bgra.shape[0] or roi_bgra.shape[1] < target_bgra.shape[1]:
        # Should never happen now (recogniser auto-fixes ROI dims to
        # match template). Logged at DEBUG only.
        logger.debug(
            f"_template_score[{_kind}]: ROI {roi_bgra.shape[1]}x{roi_bgra.shape[0]} "
            f"< template {target_bgra.shape[1]}x{target_bgra.shape[0]}"
        )
        return 0.0
    try:
        result = cv2.matchTemplate(roi_bgra, target_bgra, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
    except cv2.error as exc:
        logger.error(f"❌ 인식 실패 [{_kind}]: {exc}")
        return 0.0
    return max(0.0, float(max_val) * scale)


class Recognizer:
    """Holds preloaded template images, exposes one-shot frame analysis."""

    def __init__(
        self,
        pk_target_path: Path = TARGETS_DIR / "pk.png",
        potion_target_path: Path = TARGETS_DIR / "potion.png",
    ) -> None:
        self._pk_target = self._load_bgra(pk_target_path)
        self._potion_target = self._load_bgra(potion_target_path)
        from quickcast.utils.logger import logger
        # Single concise init line — only warn when a template is missing.
        pk_ok = self._pk_target is not None
        po_ok = self._potion_target is not None
        if not pk_ok or not po_ok:
            logger.warning(
                f"⚠️ 템플릿 로드 실패 — PK: {'OK' if pk_ok else 'FAIL'},"
                f" 물약: {'OK' if po_ok else 'FAIL'}"
            )
        self._last_score_log_at = 0.0

    @staticmethod
    def _load_bgra(path: Path) -> Optional[np.ndarray]:
        # Read via numpy + imdecode so non-ASCII paths (e.g. Korean) work.
        # cv2.imread on Windows fails on non-ASCII paths.
        if not path.exists():
            return None
        try:
            data = np.fromfile(str(path), dtype=np.uint8)
        except OSError:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        return img

    def analyze(self, frame: Frame, settings: Settings) -> FrameAnalysis:
        sura_offset_hp = 5 if settings.sura_mode else 0
        sura_offset_mp = 6 if settings.sura_mode else 0

        # Auto-correct ROI dims to template size (only logged once
        # per process via _ROI_FIX_REPORTED, to avoid log noise).
        from quickcast.utils.logger import logger
        if self._pk_target is not None:
            th, tw = self._pk_target.shape[:2]
            if settings.pk.cap_w != tw or settings.pk.cap_h != th:
                if not _ROI_FIX_REPORTED.get("pk"):
                    logger.info(f"🔧 PK 박스 크기 자동 보정: {settings.pk.cap_w}×{settings.pk.cap_h} → {tw}×{th}")
                    _ROI_FIX_REPORTED["pk"] = True
                settings.pk.cap_w = int(tw); settings.pk.cap_h = int(th)
        if self._potion_target is not None:
            th, tw = self._potion_target.shape[:2]
            if settings.potion.cap_w != tw or settings.potion.cap_h != th:
                if not _ROI_FIX_REPORTED.get("potion"):
                    logger.info(f"🔧 물약 박스 크기 자동 보정: {settings.potion.cap_w}×{settings.potion.cap_h} → {tw}×{th}")
                    _ROI_FIX_REPORTED["potion"] = True
                settings.potion.cap_w = int(tw); settings.potion.cap_h = int(th)

        hp_roi = frame.crop(settings.hp_cap, settings.hp_cap_w, settings.hp_cap_h, sura_offset_hp)
        mp_roi = frame.crop(settings.mp_cap, settings.mp_cap_w, settings.mp_cap_h, sura_offset_mp)

        # Per-kind scale matches the legacy threshold magnitudes so the
        # existing PK 1M-5M / Potion 50K-250K sliders still work. Skip
        # the matchTemplate entirely when the slot is OFF — the user's
        # intuition (off switch ⇒ no work) wins over keeping a live
        # calibration score, which the combat panel labels handle by
        # showing "감지 OFF" instead.
        if settings.pk.use and self._pk_target is not None:
            pk_roi = frame.crop(settings.pk.cap, settings.pk.cap_w, settings.pk.cap_h)
            pk_score = _template_score(pk_roi, self._pk_target,
                                         scale=5_000_000.0, _kind="pk")
        else:
            pk_score = 0.0

        if settings.potion.use and self._potion_target is not None:
            potion_roi = frame.crop(settings.potion.cap, settings.potion.cap_w, settings.potion.cap_h)
            potion_score = _template_score(potion_roi, self._potion_target,
                                             scale=250_000.0, _kind="potion")
        else:
            potion_score = 0.0

        return FrameAnalysis(
            hp=_hp_ratio(hp_roi),
            mp=_mp_ratio(mp_roi),
            pk_detected=round(pk_score) >= settings.pk.threshold,
            pk_score=pk_score,
            potion_empty=round(potion_score) >= settings.potion.threshold,
            potion_score=potion_score,
        )


__all__ = ["Recognizer", "FrameAnalysis", "TARGETS_DIR"]
