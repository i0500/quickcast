"""Image recognition — HP/MP percentage + PK/potion template matching.

Algorithms ported 1:1 from the original JavaScript so behaviour is
indistinguishable to the user. Implementation is vectorised with NumPy
which is roughly an order of magnitude faster than the OpenCV.js + JS
loop pipeline it replaces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import time

import cv2
import numpy as np

from quickcast.config import ClientProfile, PkSlot, PotionSlot, Settings
from quickcast.core.capture import Frame

TARGETS_DIR = Path(__file__).resolve().parent.parent / "data" / "targets"


@dataclass
class OverlayMatch:
    """One overlay's match result for a frame.

    Pet-whistle paw, item-acquired chest, and any other future centred
    popup template all produce one of these per frame they're enabled.
    ``detected`` is True when ``score`` >= the overlay's threshold.
    ``match_xy`` is (-1, -1) when no scan ran (disabled, template missing,
    ROI too small).
    """
    detected: bool = False
    score: float = 0.0
    match_xy: tuple[int, int] = (-1, -1)
    match_scale: float = 1.0


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
    # Per-overlay detection results (pet_whistle, item_acquired, …).
    # Empty when no overlay is enabled or templates aren't loaded.
    overlay_matches: dict = field(default_factory=dict)
    # Buff-count badge state — populated via digit OCR when
    # Settings.buff.enabled AND a non-zero buff_text_cap are configured.
    # ``buff_count`` is the OCR-read integer (None when OCR couldn't
    # confidently read a number). ``buff_scanned`` distinguishes "feature
    # off / template untrained" (False) from "scanned this frame" (True);
    # the town-idle trigger only acts on a successful scan.
    buff_count: Optional[int] = None
    buff_confidence: float = 0.0
    buff_text: str = ""
    buff_scanned: bool = False


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


# Multi-scale candidates — settings.scale_steps(1..10)로 단계 수를 조절.
# 단계가 늘어날수록 인식률은 올라가고 CPU 비용은 거의 선형으로 증가
# (13×13 템플릿 기준 1단계 ≈ 0.3ms / 10단계 ≈ 2.5ms @ 한 프레임).
# 기본 3단계는 1.0× 중심 ±8% 만 보므로 같은 해상도에서 안정적인 사용자에게
# 가장 가벼움. 게임 창 크기를 자주 바꾸는 사용자는 7~9단계로 올리면 됨.
# 각 프리셋은 1.0× 부근은 촘촘 / 양 끝은 듬성한 비대칭 분포.
_SCALE_PRESETS: dict[int, tuple[float, ...]] = {
    1:  (1.00,),
    2:  (0.93, 1.08),
    3:  (0.93, 1.00, 1.08),
    4:  (0.85, 0.93, 1.08, 1.18),
    5:  (0.85, 0.93, 1.00, 1.08, 1.18),
    6:  (0.72, 0.85, 0.93, 1.08, 1.18, 1.35),
    7:  (0.72, 0.85, 0.93, 1.00, 1.08, 1.18, 1.35),
    8:  (0.60, 0.72, 0.85, 0.93, 1.08, 1.18, 1.35, 1.60),
    9:  (0.60, 0.72, 0.85, 0.93, 1.00, 1.08, 1.18, 1.35, 1.60),
    10: (0.60, 0.72, 0.80, 0.85, 0.93, 1.00, 1.08, 1.18, 1.35, 1.60),
}
_DEFAULT_SCALES: tuple[float, ...] = _SCALE_PRESETS[3]


def _resolve_scales(steps: int) -> tuple[float, ...]:
    """Map settings.scale_steps → preset tuple, clamped to [1, 10]."""
    n = max(1, min(10, int(steps)))
    return _SCALE_PRESETS[n]


def _template_search(roi_bgra: np.ndarray, target_bgra: np.ndarray,
                       *, scale_legacy: float = 1_000_000.0,
                       scales: tuple[float, ...] = _DEFAULT_SCALES,
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

    Matching strategy (per-scale):
      1. BGR (color) 채널만 비교 — alpha=255 일정인 템플릿(pk/potion/buff
         계열)에서 alpha 채널이 매칭 점수에 0만 기여하면서도 4채널 입력은
         OpenCV 내부에서 4번째 분산 0 채널을 처리하느라 정밀도가 떨어질
         수 있음. BGR 3채널로 명시 변환해 깨끗한 신호만 사용.
      2. 알파가 실제로 변동(투명도 그라데이션)하는 템플릿(예: pet_whistle)
         은 alpha 를 mask 로 전달해 투명 영역을 매칭에서 제외.
      3. Grayscale 매칭을 병행하고 색상/회색 중 큰 점수를 채택. 디스플레이
         감마/모니터 캘리브레이션 차이로 BGR NORMED 점수가 떨어지는
         사례를 잡기 위함. 동일 색계열의 다른 모양에 대한 오탐 위험은
         형태 자체가 충분히 변별적인 PK 경보/물약 ! 아이콘에서 무시 가능.

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
    smallest = max(1, int(round(tw_native * scales[0])))
    smallest_h = max(1, int(round(th_native * scales[0])))
    if rw < smallest or rh < smallest_h:
        logger.debug(
            f"_template_search[{_kind}]: ROI {rw}x{rh} < smallest "
            f"scaled template {smallest}x{smallest_h}"
        )
        return 0.0, (-1, -1), 1.0

    # ── Channel preparation (once per call, not per scale) ──
    # Templates are loaded as BGRA via _load_bgra. Split into BGR and
    # alpha; use alpha as mask only if it actually varies. ROI from
    # frame.crop is BGRA too (mss/PrintWindow output).
    if target_bgra.ndim == 3 and target_bgra.shape[2] == 4:
        target_bgr = target_bgra[:, :, :3]
        target_alpha = target_bgra[:, :, 3]
        # >1 unique value ⇒ real transparency, use as mask. 단일 값(255)
        # 이면 mask 효과 0 → 비용만 늘어나므로 skip.
        _use_mask = int(np.unique(target_alpha).size) > 1
    else:
        target_bgr = target_bgra if target_bgra.ndim == 3 else cv2.cvtColor(
            target_bgra, cv2.COLOR_GRAY2BGR
        )
        target_alpha = None
        _use_mask = False

    if roi_bgra.ndim == 3 and roi_bgra.shape[2] == 4:
        roi_bgr = roi_bgra[:, :, :3]
    elif roi_bgra.ndim == 3:
        roi_bgr = roi_bgra
    else:
        roi_bgr = cv2.cvtColor(roi_bgra, cv2.COLOR_GRAY2BGR)

    # Ensure contiguous (matchTemplate is picky about strided views).
    roi_bgr = np.ascontiguousarray(roi_bgr)
    target_bgr = np.ascontiguousarray(target_bgr)

    roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    target_gray_native = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2GRAY)

    best_score = -1.0
    best_xy = (-1, -1)
    best_scale = 1.0
    for s in scales:
        sw = max(1, int(round(tw_native * s)))
        sh = max(1, int(round(th_native * s)))
        if sw > rw or sh > rh:
            continue
        # INTER_AREA for shrink, INTER_LINEAR for upscale — cleanest
        # result for small UI icons.
        interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
        if sw == tw_native and sh == th_native:
            tmpl_bgr = target_bgr
            tmpl_gray = target_gray_native
            tmpl_mask = target_alpha
        else:
            tmpl_bgr = cv2.resize(target_bgr, (sw, sh), interpolation=interp)
            tmpl_gray = cv2.resize(target_gray_native, (sw, sh), interpolation=interp)
            tmpl_mask = (
                cv2.resize(target_alpha, (sw, sh), interpolation=interp)
                if target_alpha is not None else None
            )
        try:
            if _use_mask and tmpl_mask is not None:
                result_bgr = cv2.matchTemplate(
                    roi_bgr, tmpl_bgr, cv2.TM_CCOEFF_NORMED, mask=tmpl_mask
                )
                result_gray = cv2.matchTemplate(
                    roi_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED, mask=tmpl_mask
                )
            else:
                result_bgr = cv2.matchTemplate(roi_bgr, tmpl_bgr, cv2.TM_CCOEFF_NORMED)
                result_gray = cv2.matchTemplate(roi_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
            # mask 경로에서 NaN/-inf 가 나올 수 있음 → 안전하게 클립.
            np.nan_to_num(result_bgr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.nan_to_num(result_gray, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            _, max_bgr, _, loc_bgr = cv2.minMaxLoc(result_bgr)
            _, max_gray, _, loc_gray = cv2.minMaxLoc(result_gray)
            # Take whichever (color/gray) scored higher. Gray catches
            # the cases where display gamma / color profile knocked the
            # color CCOEFF down; color keeps the discrimination edge for
            # well-saturated icons (PK red / 물약 ! orange).
            if max_bgr >= max_gray:
                max_val, max_loc = max_bgr, loc_bgr
            else:
                max_val, max_loc = max_gray, loc_gray
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
        buff_target_path: Path = TARGETS_DIR / "buff_full.png",
    ) -> None:
        self._pk_target = self._load_bgra(pk_target_path)
        self._potion_target = self._load_bgra(potion_target_path)
        # Top-left buff-count badge ("75" circle) template. None when
        # the file isn't shipped — profile.buff.enabled gracefully
        # no-ops in that case.
        self._buff_target = self._load_bgra(buff_target_path)
        # Overlay templates — keyed by overlay id (matches the keys in
        # profile.overlay_closes). Any file under data/targets/ whose
        # stem is not pk/potion/buff_full is treated as an overlay
        # template; the user can drop a new png and a matching settings
        # entry to extend without code changes.
        self._overlay_targets: dict[str, np.ndarray] = {}
        for p in sorted(TARGETS_DIR.glob("*.png")):
            stem = p.stem.lower()
            if stem in ("pk", "potion", "buff_full"):
                continue
            img = self._load_bgra(p)
            if img is not None:
                self._overlay_targets[stem] = img
        from quickcast.utils.logger import logger
        # Single concise init line — only warn when a template is missing.
        pk_ok = self._pk_target is not None
        po_ok = self._potion_target is not None
        bu_ok = self._buff_target is not None
        if not pk_ok or not po_ok:
            logger.warning(
                f"⚠️ 템플릿 로드 실패 — PK: {'OK' if pk_ok else 'FAIL'},"
                f" 물약: {'OK' if po_ok else 'FAIL'}"
            )
        if not bu_ok:
            logger.info("ℹ️ 버프 카운트 템플릿(buff_full.png) 없음 — 마을 대기 트리거 비활성")
        if self._overlay_targets:
            # Demoted from INFO → DEBUG: load message fired twice on
            # startup (recognizer + dashboard re-import) and added zero
            # debug value compared to its log-spam cost. Kept at DEBUG
            # for when the dev needs to confirm templates loaded.
            logger.debug(
                f"overlay templates loaded: {', '.join(self._overlay_targets)}"
            )
        self._last_score_log_at = 0.0
        # Throttle the per-frame potion-OCR debug log to once per ~2s
        # so a 10 fps capture loop doesn't drown the log card.
        self._last_potion_dbg_at = 0.0
        # Rolling history of (value, confidence) pairs for buff-count
        # OCR. Smooths per-frame jitter while still letting a stronger
        # signal override an earlier weak one — see the analyze() block
        # for the confidence-weighted voting rule.
        self._buff_history: list[tuple[int, float]] = []
        self._buff_smoothed: Optional[int] = None
        # OCR scan throttle — OCR (digit template matching, optionally
        # multi-threshold) is the heaviest per-frame cost, and the values
        # it reads (buff count for town-idle, HP/MP/potion numbers) don't
        # need 10 fps freshness. We run OCR at most once per
        # ``settings.ocr_scan_interval`` seconds and reuse the cached
        # result on intervening frames. ``_ocr_last_at`` is the monotonic
        # timestamp of the last real scan; ``_ocr_cache`` holds the last
        # computed (ocr_hp, ocr_mp, ocr_potion_empty, ocr_potion_score,
        # buff_* ) tuple so analyze() can short-circuit cheaply.
        self._ocr_last_at: float = 0.0
        self._ocr_cache: Optional[dict] = None
        # Overlay-match throttle — same idea as the OCR throttle. The
        # pet-whistle / item-acquired / blood-pledge popups stay on
        # screen for seconds, so matching them every frame is wasteful.
        # Run at most once per ``settings.overlay_scan_interval`` and
        # reuse the cached OverlayMatch dict in between.
        self._overlay_last_at: float = 0.0
        self._overlay_cache: dict = {}
        # Window of frames considered for the vote. At ~10 fps this
        # is ~1 second of jitter rejection without lagging real changes.
        self._buff_window: int = 10
        # Minimum *summed confidence* for the leading candidate to be
        # accepted. ≈ 2 high-confidence reads (conf 1.0 each) OR 4
        # medium reads (conf 0.5 each). Prevents one stray glyph match
        # from latching the smoother on its first read.
        self._buff_min_score: float = 1.5
        # Per-domain digit templates + canonical sizes.
        # Structure: { "hp": {"0": [mask, …], …}, "mp": {...}, "potion": {...}, "buff": {...} }
        # A "legacy" entry holds the pre-domain global pool (digits/*.png) so
        # users who trained before the split keep working until they
        # re-train per domain.
        self._digit_templates: dict[str, dict[str, list[np.ndarray]]] = {
            "hp": {}, "mp": {}, "potion": {}, "buff": {}, "legacy": {},
        }
        self._digit_canonical: dict[str, Optional[tuple[int, int]]] = {
            "hp": None, "mp": None, "potion": None, "buff": None, "legacy": None,
        }
        # Per-domain binarisation threshold from digit_store.read_threshold.
        # None means "auto-percentile" — the threshold the legacy global
        # Settings.ocr_threshold falls back to when no per-domain value
        # has been written yet.
        self._digit_threshold: dict[str, Optional[int]] = {
            "hp": None, "mp": None, "potion": None, "buff": None, "legacy": None,
        }
        self.reload_digits()

    def reload_digits(self) -> None:
        """Re-read digit templates from disk. Safe to call repeatedly.

        Loads each domain's pool plus the legacy global pool, and pulls
        each domain's canonical (W, H) for the per-domain normalised
        matchTemplate path in ocr._score_against. Also caches each
        domain's learned binarisation threshold so inference uses the
        exact value the templates were trained at (per-domain, not the
        global Settings.ocr_threshold which got overwritten every time
        the user retrained a different domain).
        """
        try:
            from quickcast.core.digit_store import (
                load_templates, read_canonical, read_threshold,
            )
            for d in ("hp", "mp", "potion", "buff"):
                self._digit_templates[d] = load_templates(domain=d)
                self._digit_canonical[d] = read_canonical(domain=d)
                self._digit_threshold[d] = read_threshold(domain=d)
            self._digit_templates["legacy"] = load_templates(domain=None)
            self._digit_canonical["legacy"] = None
            self._digit_threshold["legacy"] = None
        except Exception:
            from quickcast.utils.logger import logger
            logger.exception("digit templates reload failed")
            for d in self._digit_templates:
                self._digit_templates[d] = {}
                self._digit_canonical[d] = None
                self._digit_threshold[d] = None

    def _templates_for(self, domain: str) -> tuple[
        dict[str, list[np.ndarray]], Optional[tuple[int, int]]
    ]:
        """Pick the templates + canonical to use for a domain.

        Prefers the domain-specific pool when it's been trained;
        falls back to the legacy global pool (with no canonical, so
        the legacy path runs) when the domain is empty. Returning
        empty dict signals "no templates trained anywhere yet" —
        recognition.analyze() then skips OCR for that field.
        """
        tpl = self._digit_templates.get(domain) or {}
        if tpl:
            return tpl, self._digit_canonical.get(domain)
        return self._digit_templates.get("legacy", {}), None

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

    def analyze(
        self,
        frame: Frame,
        settings: Settings,
        profile: ClientProfile | None = None,
    ) -> FrameAnalysis:
        """Run all detectors for one frame.

        ``profile`` lets a per-client controller pass its own ClientProfile
        so each tab's recognizer reads only its own ROIs/templates. When
        omitted (legacy callers), the active client's profile is used —
        same data the top-level settings fields mirror.
        """
        if profile is None:
            profile = settings.get_profile()
        sura_offset_hp = 5 if profile.sura_mode else 0
        sura_offset_mp = 6 if profile.sura_mode else 0
        scales = _resolve_scales(settings.scale_steps)

        # PK / Potion: ROI is a *search region* — the user draws a box
        # at least as large as the template, and cv2.matchTemplate scans
        # for the icon's exact position inside it. This tolerates small
        # HUD shifts (전체화면 ↔ 최대화 등) without re-calibration.
        # Auto-correct only when the saved box is *smaller* than the
        # template, never shrink a deliberately enlarged search box.
        from quickcast.utils.logger import logger
        if self._pk_target is not None:
            th, tw = self._pk_target.shape[:2]
            if profile.pk.cap_w < tw or profile.pk.cap_h < th:
                if not _ROI_FIX_REPORTED.get("pk"):
                    logger.info(
                        f"🔧 PK 박스가 템플릿보다 작아 최소 크기로 확장: "
                        f"{profile.pk.cap_w}×{profile.pk.cap_h} → "
                        f"{max(profile.pk.cap_w, tw)}×{max(profile.pk.cap_h, th)}"
                    )
                    _ROI_FIX_REPORTED["pk"] = True
                profile.pk.cap_w = max(int(profile.pk.cap_w), int(tw))
                profile.pk.cap_h = max(int(profile.pk.cap_h), int(th))
        if self._potion_target is not None:
            th, tw = self._potion_target.shape[:2]
            if profile.potion.cap_w < tw or profile.potion.cap_h < th:
                if not _ROI_FIX_REPORTED.get("potion"):
                    logger.info(
                        f"🔧 물약 박스가 템플릿보다 작아 최소 크기로 확장: "
                        f"{profile.potion.cap_w}×{profile.potion.cap_h} → "
                        f"{max(profile.potion.cap_w, tw)}×{max(profile.potion.cap_h, th)}"
                    )
                    _ROI_FIX_REPORTED["potion"] = True
                profile.potion.cap_w = max(int(profile.potion.cap_w), int(tw))
                profile.potion.cap_h = max(int(profile.potion.cap_h), int(th))

        hp_roi = frame.crop(profile.hp_cap, profile.hp_cap_w, profile.hp_cap_h, sura_offset_hp)
        mp_roi = frame.crop(profile.mp_cap, profile.mp_cap_w, profile.mp_cap_h, sura_offset_mp)

        # OCR path — only when explicitly enabled AND templates exist.
        # Falls back to the legacy colour/template detectors when any
        # piece is missing (no templates, no ROI, low confidence).
        ocr_hp: Optional[int] = None
        ocr_mp: Optional[int] = None
        ocr_potion_empty: Optional[bool] = None
        ocr_potion_score: float = 0.0
        # OCR active when the mode is on AND at least one domain (or
        # the legacy pool) has any trained templates.
        ocr_active = bool(getattr(settings, "ocr_mode", False)) and any(
            self._digit_templates.get(d) for d in ("hp", "mp", "potion", "buff", "legacy")
        )
        if ocr_active:
            from quickcast.core.ocr import recognise, hp_percentage
            # Multi-threshold sweep count — shared setting (인식 안정도).
            ocr_steps = int(getattr(settings, "ocr_threshold_steps", 3) or 3)
            # Per-domain learned threshold (from digit_store .threshold
            # file) wins over the legacy global Settings.ocr_threshold
            # fallback. Both feed the same recognise() threshold arg —
            # None at the end of the chain means "auto-percentile".
            global_thr_raw = int(getattr(settings, "ocr_threshold", 0) or 0)
            global_thr: Optional[int] = (
                global_thr_raw if global_thr_raw > 0 else None
            )
            def _thr_for(dom: str) -> Optional[int]:
                v = self._digit_threshold.get(dom)
                return v if v is not None else global_thr
            # HP
            if profile.hp_text_cap_w > 0 and profile.hp_text_cap_h > 0:
                tpl, canon = self._templates_for("hp")
                if tpl:
                    hp_text_roi = frame.crop(
                        profile.hp_text_cap,
                        profile.hp_text_cap_w, profile.hp_text_cap_h,
                    )
                    r = recognise(hp_text_roi, tpl, threshold=_thr_for("hp"),
                                    canonical=canon, threshold_steps=ocr_steps)
                    ocr_hp = hp_percentage(r)
            # MP
            if profile.mp_text_cap_w > 0 and profile.mp_text_cap_h > 0:
                tpl, canon = self._templates_for("mp")
                if tpl:
                    mp_text_roi = frame.crop(
                        profile.mp_text_cap,
                        profile.mp_text_cap_w, profile.mp_text_cap_h,
                    )
                    r = recognise(mp_text_roi, tpl, threshold=_thr_for("mp"),
                                    canonical=canon, threshold_steps=ocr_steps)
                    ocr_mp = hp_percentage(r)
            # Potion — single-number field; 0 == empty.
            # Confidence threshold lowered to 0.45 (was 0.55) because a
            # single-glyph counter is harder to match cleanly than a
            # full "cur/max" string, and 0.55 was rejecting valid hits.
            if profile.potion_text_cap_w > 0 and profile.potion_text_cap_h > 0:
                tpl_po, canon_po = self._templates_for("potion")
                if not tpl_po:
                    r = None
                else:
                    po_text_roi = frame.crop(
                        profile.potion_text_cap,
                        profile.potion_text_cap_w, profile.potion_text_cap_h,
                    )
                    r = recognise(po_text_roi, tpl_po, threshold=_thr_for("potion"),
                                    canonical=canon_po, threshold_steps=ocr_steps)
            else:
                r = None
            if r is not None:
                # Throttled diagnostic at INFO level so it lands in the
                # dashboard log card — the user needs to actually see
                # what OCR is reading to debug "0으로만 인식" reports.
                import time as _t
                now = _t.monotonic()
                if now - self._last_potion_dbg_at > 2.0:
                    self._last_potion_dbg_at = now
                    glyphs_str = ""
                    if getattr(r, "glyphs", None):
                        glyphs_str = " [" + " ".join(
                            f"{lab or '?'}={sc:.2f}" for lab, sc in r.glyphs
                        ) + "]"
                    logger.info(
                        f"🔢 물약 OCR → text={r.text!r} cur={r.current} "
                        f"conf={r.confidence:.2f}{glyphs_str}"
                    )
                if r.confidence >= 0.45 and r.current is not None:
                    # "0 detected ⇒ potion empty" is the trigger for
                    # the recovery sequence — it's the high-stakes
                    # decision in this whole pipeline. An under-trained
                    # template set ("0" learned but not "5"/"8"/…)
                    # picks "0" weakly for every digit that crosses
                    # the ROI, conf hovering around 0.4–0.6 and
                    # firing recovery on every frame. Solution: keep
                    # the generic 0.45 floor for "have potion" (False
                    # is the safe non-firing answer), but require a
                    # stricter 0.65 to flip to "empty" (True).
                    if r.current <= 0:
                        ocr_potion_empty = (r.confidence >= 0.65)
                    else:
                        ocr_potion_empty = False
                    # Map confidence to legacy 0..250_000 magnitude so
                    # combat-panel sliders / dashboards keep working.
                    ocr_potion_score = float(r.confidence) * 250_000.0
                else:
                    # OCR couldn't read it confidently — DO NOT fall
                    # through to the legacy template match (the user
                    # has stopped maintaining that ROI in OCR mode and
                    # it tends to false-positive "potion empty"). Mark
                    # as a safe non-empty so the macro doesn't fire on
                    # bad data.
                    ocr_potion_empty = False
                    ocr_potion_score = 0.0

        # Per-kind scale matches the legacy threshold magnitudes so the
        # existing PK 1M-5M / Potion 50K-250K sliders still work. Skip
        # the matchTemplate entirely when the slot is OFF — the user's
        # intuition (off switch ⇒ no work) wins over keeping a live
        # calibration score, which the combat panel labels handle by
        # showing "감지 OFF" instead.
        pk_match_xy: tuple[int, int] = (-1, -1)
        pk_match_scale = 1.0
        if profile.pk.use and self._pk_target is not None:
            pk_roi = frame.crop(profile.pk.cap, profile.pk.cap_w, profile.pk.cap_h)
            pk_score, local_xy, pk_match_scale = _template_search(
                pk_roi, self._pk_target,
                scale_legacy=5_000_000.0, scales=scales, _kind="pk",
            )
            if local_xy != (-1, -1):
                pk_match_xy = (profile.pk.cap.x + local_xy[0],
                               profile.pk.cap.y + local_xy[1])
        else:
            pk_score = 0.0

        potion_match_xy: tuple[int, int] = (-1, -1)
        potion_match_scale = 1.0
        if profile.potion.use and self._potion_target is not None:
            potion_roi = frame.crop(profile.potion.cap, profile.potion.cap_w, profile.potion.cap_h)
            potion_score, local_xy, potion_match_scale = _template_search(
                potion_roi, self._potion_target,
                scale_legacy=250_000.0, scales=scales, _kind="potion",
            )
            if local_xy != (-1, -1):
                potion_match_xy = (profile.potion.cap.x + local_xy[0],
                                    profile.potion.cap.y + local_xy[1])
        else:
            potion_score = 0.0

        # ── Buff-count badge (top-left "75") via digit OCR ───────────
        # Replaces the earlier template-match path. Reads the actual
        # integer count using the per-domain digit templates (trained
        # by the user via the OCR calibration dialog). Per-frame raw
        # reads pass through a majority-vote smoother below so the
        # exposed ``buff_count`` doesn't flicker between adjacent
        # values when the user's game UI hasn't actually changed.
        buff_cfg = getattr(settings, "buff", None)
        buff_count: Optional[int] = None
        buff_confidence: float = 0.0
        buff_text: str = ""
        buff_scanned = False
        # Reset smoother when feature is off so re-enabling starts fresh.
        if buff_cfg is None or not bool(getattr(buff_cfg, "enabled", False)):
            if self._buff_history:
                self._buff_history.clear()
                self._buff_smoothed = None
        if buff_cfg is not None and bool(getattr(buff_cfg, "enabled", False)) \
                and profile.buff_text_cap_w > 0 and profile.buff_text_cap_h > 0:
            tpl, canon = self._templates_for("buff")
            # ── OCR scan throttle ──
            # Buff OCR (multi-threshold digit matching on a 2× upsampled
            # ROI) is the single heaviest per-frame operation. The town-
            # idle trigger it feeds is a minutes-scale decision, so there's
            # no value in re-reading it every capture frame. Run it at most
            # once per ``settings.ocr_scan_interval`` seconds; on the
            # in-between frames reuse the cached smoothed value so the
            # town-idle timer in the controller still sees a continuous
            # ``buff_scanned=True`` stream. interval=0 → every frame.
            _ocr_interval = float(getattr(settings, "ocr_scan_interval", 1.0) or 0.0)
            _now_ocr = time.monotonic()
            _do_buff_scan = (
                _ocr_interval <= 0.0
                or (_now_ocr - self._ocr_last_at) >= _ocr_interval
            )
            if tpl and not _do_buff_scan and self._ocr_cache is not None:
                # Reuse last scan — keeps CPU near zero on skipped frames.
                buff_count = self._ocr_cache.get("buff_count")
                buff_confidence = float(self._ocr_cache.get("buff_confidence", 0.0))
                buff_text = self._ocr_cache.get("buff_text", "")
                buff_scanned = True
                tpl = None    # fall through past the real-scan block below
            if tpl:
                self._ocr_last_at = _now_ocr
                from quickcast.core.ocr import recognise
                # Per-domain learned threshold wins over the legacy global
                # Settings.ocr_threshold so retraining HP/MP doesn't break
                # buff (and vice-versa). None at the chain end ⇒ auto.
                buff_thr = self._digit_threshold.get("buff")
                if buff_thr is None:
                    g = int(getattr(settings, "ocr_threshold", 0) or 0)
                    buff_thr = g if g > 0 else None
                buff_roi = frame.crop(
                    profile.buff_text_cap,
                    profile.buff_text_cap_w, profile.buff_text_cap_h,
                )
                # ── 2× upsample before OCR ──
                # The buff badge is small (≈28×24 in the normalised frame)
                # and the digits sit on a noisy circular background. At
                # native size anti-aliasing collapses glyph edges into
                # the border which breaks connected-components
                # segmentation; INTER_CUBIC upscale to 2× smooths the
                # interpolated edges so segment_glyphs gets cleaner
                # boundaries to work with. The canonical-size normaliser
                # downstream renders this scale-invariant for the actual
                # template match.
                if buff_roi.size > 0:
                    bh, bw = buff_roi.shape[:2]
                    buff_roi = cv2.resize(
                        buff_roi, (bw * 2, bh * 2),
                        interpolation=cv2.INTER_CUBIC,
                    )
                _buff_steps = int(getattr(settings, "ocr_threshold_steps", 3) or 3)
                r = recognise(buff_roi, tpl, threshold=buff_thr, canonical=canon,
                              threshold_steps=_buff_steps)
                buff_scanned = True
                buff_text = r.text
                buff_confidence = float(r.confidence)
                raw: Optional[int] = None
                # Buff count is always ≥ 2 digits in Lineage W (the
                # badge tops out around 75 and even an empty hunting
                # buff stack shows "10"-ish during transient frames).
                # A 1-character read is almost always a misread where
                # binarisation fused two glyphs into one or dropped the
                # leading digit — treat as no-read so the smoother
                # ignores it rather than letting "5" or "7" pollute
                # the vote tally and force false town-idle triggers.
                if (r.current is not None
                        and r.confidence >= 0.55
                        and len(r.text) >= 2):
                    raw = int(r.current)
                # ── Temporal stabilisation (confidence-weighted vote) ──
                # Append latest raw + confidence to ring buffer.
                if raw is not None:
                    self._buff_history.append((raw, float(r.confidence)))
                else:
                    # Keep the slot but signal "no read" via conf 0.
                    self._buff_history.append((-1, 0.0))
                if len(self._buff_history) > self._buff_window:
                    del self._buff_history[: len(self._buff_history) - self._buff_window]
                # Sum confidence per value over the window. Higher
                # confidence reads weigh more — a single conf-0.95 read
                # beats two conf-0.4 misreads. Skip the -1 placeholder
                # rows so dropped frames don't count toward anything.
                scores: dict[int, float] = {}
                for val, conf in self._buff_history:
                    if val < 0:
                        continue
                    scores[val] = scores.get(val, 0.0) + conf
                if scores:
                    best_val, best_score = max(scores.items(), key=lambda kv: kv[1])
                    # Switch to a new leader whenever its summed
                    # confidence clears the minimum AND exceeds the
                    # current sticky value's score (if any). Prevents
                    # the "첫 인식된 값이 latch" failure mode.
                    if best_score >= self._buff_min_score:
                        cur_score = scores.get(self._buff_smoothed, -1.0)
                        if (self._buff_smoothed is None
                                or best_val == self._buff_smoothed
                                or best_score > cur_score):
                            self._buff_smoothed = best_val
                # buff_count exposes the smoothed value; trigger + UI
                # labels read from this, not the raw per-frame number.
                buff_count = self._buff_smoothed
                # Cache for the throttled (skipped) frames in between scans.
                self._ocr_cache = {
                    "buff_count": buff_count,
                    "buff_confidence": buff_confidence,
                    "buff_text": buff_text,
                }

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
            final_potion_empty = round(potion_score) >= profile.potion.threshold
            final_potion_score = potion_score

        # ── Overlay close detection ────────────────────────────────
        # For each enabled overlay (pet_whistle / item_acquired / …)
        # scan its search region for the template. Match magnitude
        # mirrors PK (scale_legacy=5_000_000) so thresholds are
        # interchangeable mentally.
        #
        # Throttled: the popups stay on screen for seconds, so matching
        # 3 templates every frame is wasteful. Run at most once per
        # ``settings.overlay_scan_interval`` and reuse the cached match
        # dict on the frames in between. The controller's sustain /
        # cooldown logic is monotonic-time based, so the ESC still fires
        # on time even though detection is sampled at a coarser rate.
        overlay_cfg = getattr(settings, "overlay_closes", None) or {}
        _ov_interval = float(getattr(settings, "overlay_scan_interval", 0.4) or 0.0)
        _now_ov = time.monotonic()
        _do_ov_scan = (
            _ov_interval <= 0.0
            or (_now_ov - self._overlay_last_at) >= _ov_interval
        )
        if not _do_ov_scan and self._overlay_cache:
            overlay_matches: dict[str, OverlayMatch] = self._overlay_cache
        else:
            self._overlay_last_at = _now_ov
            overlay_matches = {}
            for ov_id, ov in overlay_cfg.items():
                if not getattr(ov, "enabled", False):
                    continue
                tmpl = self._overlay_targets.get(ov_id)
                if tmpl is None:
                    continue
                # Auto-expand search ROI to at least template size, same as
                # PK / Potion above. Centred popups rarely move, but a user
                # drawing a tiny box shouldn't silently fail to match.
                th, tw = tmpl.shape[:2]
                if ov.cap_w < tw:
                    ov.cap_w = int(tw)
                if ov.cap_h < th:
                    ov.cap_h = int(th)
                roi = frame.crop(ov.cap, ov.cap_w, ov.cap_h)
                score, local_xy, scale = _template_search(
                    roi, tmpl,
                    scale_legacy=5_000_000.0, scales=scales, _kind=f"overlay:{ov_id}",
                )
                match_xy: tuple[int, int] = (-1, -1)
                if local_xy != (-1, -1):
                    match_xy = (ov.cap.x + local_xy[0], ov.cap.y + local_xy[1])
                overlay_matches[ov_id] = OverlayMatch(
                    detected=round(score) >= int(ov.threshold),
                    score=score,
                    match_xy=match_xy,
                    match_scale=scale,
                )
            self._overlay_cache = overlay_matches

        return FrameAnalysis(
            hp=final_hp,
            mp=final_mp,
            pk_detected=round(pk_score) >= profile.pk.threshold,
            pk_score=pk_score,
            potion_empty=final_potion_empty,
            potion_score=final_potion_score,
            pk_match_xy=pk_match_xy,
            potion_match_xy=potion_match_xy,
            pk_match_scale=pk_match_scale,
            potion_match_scale=potion_match_scale,
            overlay_matches=overlay_matches,
            buff_count=buff_count,
            buff_confidence=buff_confidence,
            buff_text=buff_text,
            buff_scanned=buff_scanned,
        )


__all__ = ["Recognizer", "FrameAnalysis", "OverlayMatch", "TARGETS_DIR"]
