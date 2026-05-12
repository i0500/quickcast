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
    # Best-match top-left within the ROI search region, in frame coords
    # (1280×720 normalised space). (-1, -1) when no scan was performed
    # (slot OFF or template missing). Used by the preview overlay to show
    # the user where the template actually locked on.
    pk_match_xy: tuple[int, int] = (-1, -1)
    potion_match_xy: tuple[int, int] = (-1, -1)
    # Effective scale that produced the best score (1.0 = native template
    # size). Useful for diagnostics — if the best fit is consistently
    # ≠ 1.0 the user's calibration is at the wrong zoom level.
    pk_match_scale: float = 1.0
    potion_match_scale: float = 1.0


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


# Multi-scale candidates around 1.0× — covers the small zoom variation
# that comes from non-uniform stretching (16:9 → 16:10 normalisation,
# user-resized window, etc.). 5 scales × ~30µs each = under 1ms total
# for the 13×13 potion template, ~4ms for the 25×25 pk template.
_SCALE_CANDIDATES: tuple[float, ...] = (0.85, 0.93, 1.00, 1.08, 1.18)


def _template_search(roi_bgra: np.ndarray, target_bgra: np.ndarray,
                       *, scale_legacy: float = 1_000_000.0,
                       _kind: str = "") -> tuple[float, tuple[int, int], float]:
    """Find the best template match inside `roi_bgra`.

    Returns ``(legacy_score, (best_x, best_y), best_scale)``:
      - ``legacy_score`` is the normalised correlation (0..1) rescaled to
        the legacy threshold magnitude so existing sliders still calibrate
        the same way.
      - ``(best_x, best_y)`` is the top-left of the best match **relative
        to the ROI** (caller adds ROI origin to map to frame coords).
        Set to ``(-1, -1)`` on failure / unmatchable input.
      - ``best_scale`` is the template scale factor that gave the best
        score (1.0 = native template size). Useful for diagnostics.

    Tries multiple template scales around 1.0× so a HUD that shrunk /
    grew slightly under a different client size still locks on. The ROI
    must be at least as large as the *largest* scaled template; smaller
    scales are skipped silently if the ROI can't fit them.
    """
    from quickcast.utils.logger import logger
    if roi_bgra is None or target_bgra is None:
        return 0.0, (-1, -1), 1.0
    if roi_bgra.size == 0 or target_bgra.size == 0:
        return 0.0, (-1, -1), 1.0

    th_native, tw_native = target_bgra.shape[:2]
    rh, rw = roi_bgra.shape[:2]

    # ROI too small even for the smallest scale → cannot match. Reported
    # at DEBUG only (the recognizer enforces a minimum size before
    # calling us in the steady-state path).
    smallest = max(1, int(round(tw_native * _SCALE_CANDIDATES[0])))
    smallest_h = max(1, int(round(th_native * _SCALE_CANDIDATES[0])))
    if rw < smallest or rh < smallest_h:
        logger.debug(
            f"_template_search[{_kind}]: ROI {rw}x{rh} < smallest "
            f"scaled template {smallest}x{smallest_h}"
        )
        return 0.0, (-1, -1), 1.0

    best_score = -1.0
    best_xy = (-1, -1)
    best_scale = 1.0
    for s in _SCALE_CANDIDATES:
        sw = max(1, int(round(tw_native * s)))
        sh = max(1, int(round(th_native * s)))
        if sw > rw or sh > rh:
            continue
        if sw == tw_native and sh == th_native:
            tmpl = target_bgra
        else:
            # INTER_AREA for shrink, INTER_LINEAR for upscale — gives the
            # cleanest result for the small UI icons we're matching.
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
            tmpl = cv2.resize(target_bgra, (sw, sh), interpolation=interp)
        try:
            result = cv2.matchTemplate(roi_bgra, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
        except cv2.error as exc:
            logger.error(f"❌ 인식 실패 [{_kind} @ {s:.2f}×]: {exc}")
            continue
        if max_val > best_score:
            best_score = float(max_val)
            best_xy = (int(max_loc[0]), int(max_loc[1]))
            best_scale = s

    if best_score < 0:
        return 0.0, (-1, -1), 1.0
    return max(0.0, best_score * scale_legacy), best_xy, best_scale


def _template_score(roi_bgra: np.ndarray, target_bgra: np.ndarray,
                     scale: float = 1_000_000.0,
                     _kind: str = "") -> float:
    """Backwards-compat shim: returns just the legacy score."""
    s, _, _ = _template_search(roi_bgra, target_bgra,
                                  scale_legacy=scale, _kind=_kind)
    return s


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
        # Digit templates for the OCR path. Loaded lazily so a fresh
        # install that hasn't trained yet doesn't pay the disk hit on
        # every analyse() call. reload_digits() refreshes from disk
        # whenever the learner saves a new set.
        self._digit_templates: dict[str, np.ndarray] = {}
        self.reload_digits()

    def reload_digits(self) -> None:
        """Re-read digit templates from disk. Safe to call repeatedly."""
        try:
            from quickcast.core.digit_store import load_templates
            self._digit_templates = load_templates()
        except Exception:
            from quickcast.utils.logger import logger
            logger.exception("digit templates reload failed")
            self._digit_templates = {}

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

        # PK / Potion: ROI must match the template exactly. We tried a
        # "ROI as search-region" mode that lets the matcher hunt inside
        # a larger box, but it didn't behave reliably in real games and
        # the user rolled it back. So we force the ROI to the template
        # size — the user has to position the small box precisely.
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

        # OCR path — only when explicitly enabled AND templates exist.
        # Falls back to the legacy colour/template detectors when any
        # piece is missing (no templates, no ROI, low confidence).
        ocr_hp: Optional[int] = None
        ocr_mp: Optional[int] = None
        ocr_potion_empty: Optional[bool] = None
        ocr_potion_score: float = 0.0
        if getattr(settings, "ocr_mode", False) and self._digit_templates:
            from quickcast.core.ocr import recognise, hp_percentage
            # HP
            if settings.hp_text_cap_w > 0 and settings.hp_text_cap_h > 0:
                hp_text_roi = frame.crop(
                    settings.hp_text_cap,
                    settings.hp_text_cap_w, settings.hp_text_cap_h,
                )
                r = recognise(hp_text_roi, self._digit_templates)
                ocr_hp = hp_percentage(r)
            # MP
            if settings.mp_text_cap_w > 0 and settings.mp_text_cap_h > 0:
                mp_text_roi = frame.crop(
                    settings.mp_text_cap,
                    settings.mp_text_cap_w, settings.mp_text_cap_h,
                )
                r = recognise(mp_text_roi, self._digit_templates)
                ocr_mp = hp_percentage(r)
            # Potion — single-number field; 0 == empty.
            if settings.potion_text_cap_w > 0 and settings.potion_text_cap_h > 0:
                po_text_roi = frame.crop(
                    settings.potion_text_cap,
                    settings.potion_text_cap_w, settings.potion_text_cap_h,
                )
                r = recognise(po_text_roi, self._digit_templates)
                if r.confidence >= 0.55 and r.current is not None:
                    ocr_potion_empty = (r.current <= 0)
                    # Map confidence to legacy 0..250_000 magnitude so
                    # combat-panel sliders / dashboards keep working.
                    ocr_potion_score = float(r.confidence) * 250_000.0

        # Per-kind scale matches the legacy threshold magnitudes so the
        # existing PK 1M-5M / Potion 50K-250K sliders still work. Skip
        # the matchTemplate entirely when the slot is OFF — the user's
        # intuition (off switch ⇒ no work) wins over keeping a live
        # calibration score, which the combat panel labels handle by
        # showing "감지 OFF" instead.
        pk_match_xy: tuple[int, int] = (-1, -1)
        pk_match_scale = 1.0
        if settings.pk.use and self._pk_target is not None:
            pk_roi = frame.crop(settings.pk.cap, settings.pk.cap_w, settings.pk.cap_h)
            pk_score, local_xy, pk_match_scale = _template_search(
                pk_roi, self._pk_target,
                scale_legacy=5_000_000.0, _kind="pk",
            )
            if local_xy != (-1, -1):
                pk_match_xy = (settings.pk.cap.x + local_xy[0],
                               settings.pk.cap.y + local_xy[1])
        else:
            pk_score = 0.0

        potion_match_xy: tuple[int, int] = (-1, -1)
        potion_match_scale = 1.0
        if settings.potion.use and self._potion_target is not None:
            potion_roi = frame.crop(settings.potion.cap, settings.potion.cap_w, settings.potion.cap_h)
            potion_score, local_xy, potion_match_scale = _template_search(
                potion_roi, self._potion_target,
                scale_legacy=250_000.0, _kind="potion",
            )
            if local_xy != (-1, -1):
                potion_match_xy = (settings.potion.cap.x + local_xy[0],
                                    settings.potion.cap.y + local_xy[1])
        else:
            potion_score = 0.0

        # Prefer OCR readings when valid (non-None, learnt templates
        # produced a confident parse). Falls back to legacy detectors
        # otherwise so a half-trained / partly-configured state still
        # works exactly like before.
        final_hp = ocr_hp if ocr_hp is not None else _hp_ratio(hp_roi)
        final_mp = ocr_mp if ocr_mp is not None else _mp_ratio(mp_roi)
        if ocr_potion_empty is not None:
            final_potion_empty = bool(ocr_potion_empty)
            final_potion_score = ocr_potion_score
        else:
            final_potion_empty = round(potion_score) >= settings.potion.threshold
            final_potion_score = potion_score

        return FrameAnalysis(
            hp=final_hp,
            mp=final_mp,
            pk_detected=round(pk_score) >= settings.pk.threshold,
            pk_score=pk_score,
            potion_empty=final_potion_empty,
            potion_score=final_potion_score,
            pk_match_xy=pk_match_xy,
            potion_match_xy=potion_match_xy,
            pk_match_scale=pk_match_scale,
            potion_match_scale=potion_match_scale,
        )


__all__ = ["Recognizer", "FrameAnalysis", "TARGETS_DIR"]
