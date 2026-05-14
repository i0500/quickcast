"""Interactive capture preview — drag ROI rectangles directly.

Replaces the read-only QLabel preview. The user can:
  - Drag inside a ROI → moves it
  - Drag on an edge/corner → resizes it
  - Hover changes cursor to indicate the action
The widget owns no state of its own; ROI coordinates live on the
shared `Settings` instance and are updated in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from quickcast.config import Point, Settings
from quickcast.core.capture import TARGET_H, TARGET_W


# ROI metadata: id, label, color (BGRA for cv2 / QColor for Qt)
ROI_DEFS = [
    ("hp",     "HP",     QColor(255,  82,  82)),   # red
    ("mp",     "MP",     QColor( 66, 165, 245)),   # blue
    ("pk",     "PK",     QColor(255, 215,   0)),   # gold
    ("potion", "POTION", QColor( 76, 175,  80)),   # green
]

# OCR text-region ROIs. Only painted / hit-tested when settings.ocr_mode
# is True so they don't clutter the default view. Lighter shades so the
# user can visually distinguish them from the primary HUD boxes above.
OCR_ROI_DEFS = [
    ("hp_text",     "HP 텍스트",     QColor(255, 145, 145)),   # light red
    ("mp_text",     "MP 텍스트",     QColor(130, 195, 255)),   # light blue
    ("potion_text", "물약 텍스트",   QColor(170, 220, 170)),   # light green
]

# Overlay-close ROIs (pet whistle paw / item acquired chest / …). One
# colour per known overlay id — only drawn when that overlay's enabled
# flag is True so the preview stays uncluttered. Keys use the
# "overlay:{ov_id}" namespace to avoid colliding with HUD ROI ids.
OVERLAY_ROI_DEFS: list[tuple[str, str, QColor]] = [
    ("overlay:pet_whistle",   "펫 호루라기",   QColor(255, 145, 215)),   # pink
    ("overlay:item_acquired", "아이템 획득",   QColor(255, 200, 100)),   # amber
]


@dataclass
class _RoiRect:
    """In-frame coordinates (1280x720 space) of a single ROI."""
    x: int
    y: int
    w: int
    h: int

    def to_qrect(self) -> QRect:
        return QRect(self.x, self.y, self.w, self.h)


# Hit-test zones for resizing
_HIT_NONE = 0
_HIT_INSIDE = 1
_HIT_LEFT = 2
_HIT_RIGHT = 3
_HIT_TOP = 4
_HIT_BOTTOM = 5
_HIT_TL = 6
_HIT_TR = 7
_HIT_BL = 8
_HIT_BR = 9

_EDGE_PX = 6  # how many *frame* pixels count as the edge zone


class InteractivePreview(QWidget):
    """Draws the live frame and lets the user drag ROI handles."""

    roi_changed = Signal(str)   # roi id that changed

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._frame_pixmap: Optional[QPixmap] = None
        self._frame_size = QSize(TARGET_W, TARGET_H)

        # render area inside the widget — recomputed each paint
        self._render_rect = QRect()

        # Drag state
        self._drag_id: Optional[str] = None
        self._drag_hit: int = _HIT_NONE
        self._drag_start_pt: Optional[QPoint] = None     # frame coords
        self._drag_start_roi: Optional[_RoiRect] = None

        # View-only mode disables ROI drag/resize. Used by the fullscreen
        # mirror window so accidental clicks don't move saved coords.
        self._view_only = False

        # Recovery pick mode — when armed, the next mouse click captures
        # game-frame (x, y) into the targeted recovery step instead of
        # dragging an ROI. The dashboard wires this up; this widget just
        # holds the flag, draws the banner, and emits the result.
        self._pick_mode_idx: Optional[int] = None
        # Item-close pick mode — same idea but routes the click into
        # the ItemCloseSettings coord via bus.item_close_pick_done.
        self._item_close_pick: bool = False

        # Master grace-period countdown (seconds remaining). 0 = idle.
        self._grace_remaining: float = 0.0
        from quickcast.ui.design.signals import bus as _bus
        _bus.master_grace_changed.connect(self._on_grace_changed)
        # OCR-mode toggle / text ROI edits change which boxes are visible,
        # so repaint whenever settings flips. Cheap — paintEvent reads
        # live values on each call already.
        _bus.settings_dirty.connect(self.update)

        self.setMouseTracking(True)
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

        # Latest recognition values, drawn next to each ROI
        self._roi_value_text: dict[str, str] = {}
        # Cache of label hit-rects (widget coords) so clicking the label
        # also drags the corresponding ROI
        self._label_rects: dict[str, QRect] = {}

        # Native template sizes (W, H in frame coords). Read once from
        # the targets dir so the match overlay can draw a true-scale
        # inner box at the matched position.
        self._tmpl_size: dict[str, tuple[int, int]] = {
            "pk": self._read_template_size("pk.png") or (25, 25),
            "potion": self._read_template_size("potion.png") or (13, 13),
        }
        # Latest match locations + scales from the recognizer. (-1, -1)
        # means no scan / slot OFF — overlay skipped.
        self._match_xy: dict[str, tuple[int, int]] = {
            "pk": (-1, -1), "potion": (-1, -1),
        }
        self._match_scale: dict[str, float] = {"pk": 1.0, "potion": 1.0}

    @staticmethod
    def _read_template_size(name: str) -> Optional[tuple[int, int]]:
        from quickcast.core.recognition import TARGETS_DIR
        p = TARGETS_DIR / name
        if not p.exists():
            return None
        try:
            data = np.fromfile(str(p), dtype=np.uint8)
        except OSError:
            return None
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None
        return (int(img.shape[1]), int(img.shape[0]))

    # ───────── ROI accessors (read live from settings) ─────────
    def _get_roi(self, roi_id: str) -> _RoiRect:
        s = self.settings
        sura_hp = 5 if s.sura_mode else 0
        sura_mp = 6 if s.sura_mode else 0
        if roi_id == "hp":
            return _RoiRect(s.hp_cap.x, s.hp_cap.y + sura_hp, s.hp_cap_w, s.hp_cap_h)
        if roi_id == "mp":
            return _RoiRect(s.mp_cap.x, s.mp_cap.y + sura_mp, s.mp_cap_w, s.mp_cap_h)
        if roi_id == "pk":
            return _RoiRect(s.pk.cap.x, s.pk.cap.y, s.pk.cap_w, s.pk.cap_h)
        if roi_id == "potion":
            return _RoiRect(s.potion.cap.x, s.potion.cap.y,
                            s.potion.cap_w, s.potion.cap_h)
        if roi_id == "hp_text":
            return _RoiRect(s.hp_text_cap.x, s.hp_text_cap.y,
                            s.hp_text_cap_w, s.hp_text_cap_h)
        if roi_id == "mp_text":
            return _RoiRect(s.mp_text_cap.x, s.mp_text_cap.y,
                            s.mp_text_cap_w, s.mp_text_cap_h)
        if roi_id == "potion_text":
            return _RoiRect(s.potion_text_cap.x, s.potion_text_cap.y,
                            s.potion_text_cap_w, s.potion_text_cap_h)
        if roi_id.startswith("overlay:"):
            ov_id = roi_id.split(":", 1)[1]
            ov = (getattr(s, "overlay_closes", None) or {}).get(ov_id)
            if ov is None:
                return _RoiRect(0, 0, 0, 0)
            return _RoiRect(ov.cap.x, ov.cap.y, ov.cap_w, ov.cap_h)
        raise KeyError(roi_id)

    def _set_roi(self, roi_id: str, r: _RoiRect) -> None:
        # Clamp to frame bounds — without this, dragging the ROI
        # close to the right/bottom edge could produce a crop smaller
        # than the recognizer's saved template (potion.png / pk.png),
        # at which point _template_score returns 0 and the user sees
        # "score not recognised" even though the ROI moved correctly.
        s = self.settings
        sura_hp = 5 if s.sura_mode else 0
        sura_mp = 6 if s.sura_mode else 0
        fw = max(1, self._frame_size.width())
        fh = max(1, self._frame_size.height())
        w = max(1, min(r.w, fw))
        h = max(1, min(r.h, fh))
        cx = max(0, min(r.x, fw - w))
        cy = max(0, min(r.y, fh - h))
        if roi_id == "hp":
            s.hp_cap = Point(x=cx, y=max(0, cy - sura_hp))
            s.hp_cap_w, s.hp_cap_h = w, h
        elif roi_id == "mp":
            s.mp_cap = Point(x=cx, y=max(0, cy - sura_mp))
            s.mp_cap_w, s.mp_cap_h = w, h
        elif roi_id == "pk":
            s.pk.cap = Point(x=cx, y=cy)
            s.pk.cap_w, s.pk.cap_h = w, h
        elif roi_id == "potion":
            s.potion.cap = Point(x=cx, y=cy)
            s.potion.cap_w, s.potion.cap_h = w, h
        elif roi_id == "hp_text":
            s.hp_text_cap = Point(x=cx, y=cy)
            s.hp_text_cap_w, s.hp_text_cap_h = w, h
        elif roi_id == "mp_text":
            s.mp_text_cap = Point(x=cx, y=cy)
            s.mp_text_cap_w, s.mp_text_cap_h = w, h
        elif roi_id == "potion_text":
            s.potion_text_cap = Point(x=cx, y=cy)
            s.potion_text_cap_w, s.potion_text_cap_h = w, h
        elif roi_id.startswith("overlay:"):
            ov_id = roi_id.split(":", 1)[1]
            ov = (getattr(s, "overlay_closes", None) or {}).get(ov_id)
            if ov is not None:
                ov.cap = Point(x=cx, y=cy)
                ov.cap_w, ov.cap_h = w, h
        self.roi_changed.emit(roi_id)

    # ───────── frame updates ─────────
    def update_frame(self, frame_image: np.ndarray) -> None:
        """Called from the controller's analysis callback (UI thread).

        Zero-copy: holds a ref to the source numpy array (which lives
        in the capture pool's triple buffer — won't be overwritten for
        ≥2 grabs) and points QImage directly at its memory. This avoids
        the ~7MB per-frame churn that would otherwise dominate low-end
        CPUs running at higher fps.
        """
        if not frame_image.flags["C_CONTIGUOUS"]:
            frame_image = np.ascontiguousarray(frame_image)
        h, w = frame_image.shape[:2]
        self._frame_size = QSize(w, h)
        # Pin the array — QImage borrows its data pointer.
        self._frame_buf = frame_image
        qimg = QImage(frame_image.data, w, h, w * 4, QImage.Format_ARGB32)
        # QPixmap.fromImage forces a GPU upload but does not copy CPU
        # bytes a second time when the QImage already references our
        # buffer. The pixmap is what paintEvent draws.
        self._frame_pixmap = QPixmap.fromImage(qimg)
        self.update()

    def update_recognition(self, hp: int, mp: int,
                            pk_score: float, potion_score: float,
                            pk_thr: int, potion_thr: int,
                            pk_match_xy: tuple[int, int] = (-1, -1),
                            potion_match_xy: tuple[int, int] = (-1, -1),
                            pk_match_scale: float = 1.0,
                            potion_match_scale: float = 1.0) -> None:
        """Latest recognition values to overlay near each ROI — short labels only.

        Label keys follow the *active* ROI set: in OCR mode the HP / MP /
        potion readings are anchored to the text ROIs that are actually
        drawn; in legacy mode they're anchored to the original boxes.
        PK is the same key in both modes.

        ``*_match_xy`` is the top-left of the matcher's best hit in
        frame coords; ``(-1, -1)`` when not available (slot OFF or
        template missing). ``*_match_scale`` is the template scale that
        produced the hit (1.0 = native). Together they let the preview
        draw a true-size inner box showing where the icon was located.
        """
        self._match_xy["pk"] = pk_match_xy
        self._match_xy["potion"] = potion_match_xy
        self._match_scale["pk"] = float(pk_match_scale)
        self._match_scale["potion"] = float(potion_match_scale)
        pk_match = pk_score >= pk_thr
        potion_match = potion_score >= potion_thr
        if getattr(self.settings, "ocr_mode", False):
            self._roi_value_text = {
                "hp_text": f"HP {hp}%",
                "mp_text": f"MP {mp}%",
                "pk": "PK 전투" if pk_match else "PK 보통",
                "potion_text": "물약 없음" if potion_match else "물약 있음",
            }
        else:
            self._roi_value_text = {
                "hp": f"HP {hp}%",
                "mp": f"MP {mp}%",
                "pk": "PK 전투" if pk_match else "PK 보통",
                "potion": "물약 없음" if potion_match else "물약 있음",
            }
        self.update()

    # ───────── coordinate mapping ─────────
    def _compute_render_rect(self) -> QRect:
        """Letterboxed rect inside the widget where the frame is drawn."""
        wsz = self.size()
        fw, fh = self._frame_size.width(), self._frame_size.height()
        if fw <= 0 or fh <= 0:
            return QRect(0, 0, wsz.width(), wsz.height())
        scale = min(wsz.width() / fw, wsz.height() / fh)
        rw, rh = int(fw * scale), int(fh * scale)
        rx = (wsz.width() - rw) // 2
        ry = (wsz.height() - rh) // 2
        return QRect(rx, ry, rw, rh)

    def _frame_to_widget(self, fx: int, fy: int) -> QPoint:
        r = self._render_rect
        if r.width() == 0 or r.height() == 0:
            return QPoint(fx, fy)
        sx = r.width() / self._frame_size.width()
        sy = r.height() / self._frame_size.height()
        return QPoint(int(r.x() + fx * sx), int(r.y() + fy * sy))

    def _widget_to_frame(self, wx: int, wy: int) -> Optional[QPoint]:
        r = self._render_rect
        if r.width() == 0 or r.height() == 0:
            return None
        if not r.contains(wx, wy):
            return None
        sx = self._frame_size.width() / r.width()
        sy = self._frame_size.height() / r.height()
        fx = int((wx - r.x()) * sx)
        fy = int((wy - r.y()) * sy)
        return QPoint(fx, fy)

    # ───────── hit testing ─────────
    # PK / Potion ROIs are *search regions* — the user can grow them so
    # cv2.matchTemplate hunts for the icon inside. recognition.py
    # auto-enforces a minimum size (template native) when saved, so
    # shrinking below that is harmless. No size-locked IDs currently.
    _SIZE_LOCKED_IDS: tuple[str, ...] = ()

    def _active_roi_defs(self) -> list[tuple[str, str, QColor]]:
        """ROI set to draw / hit-test for the current mode.

        - OCR off (legacy): show HP / MP / PK / POTION.
        - OCR on: hide HP / MP / POTION (now read from text ROIs);
          show PK (no text source) and the three text ROIs.
        - Overlay-close ROIs: appended whenever their enabled flag is
          True, regardless of OCR mode — they're a separate detection
          pipeline and the user toggles them per-overlay.
        """
        if getattr(self.settings, "ocr_mode", False):
            defs: list[tuple[str, str, QColor]] = [
                d for d in ROI_DEFS if d[0] == "pk"
            ]
            defs.extend(OCR_ROI_DEFS)
        else:
            defs = list(ROI_DEFS)
        ovc = getattr(self.settings, "overlay_closes", None) or {}
        for roi_id, label, color in OVERLAY_ROI_DEFS:
            ov_key = roi_id.split(":", 1)[1]
            ov = ovc.get(ov_key)
            if ov is not None and getattr(ov, "enabled", False):
                defs.append((roi_id, label, color))
        return defs

    def _hit_test(self, fx: int, fy: int) -> tuple[Optional[str], int]:
        """Return (roi_id, hit_zone) for the topmost matching ROI under (fx, fy)."""
        # Iterate in reverse so larger-drawn-last ROIs win — but our ROIs are
        # all small so order barely matters. Prefer edges over interiors.
        for roi_id, _label, _col in reversed(self._active_roi_defs()):
            r = self._get_roi(roi_id)
            # Skip ROIs that aren't actually drawn (zero-size text ROIs).
            if roi_id.endswith("_text") and (r.w <= 0 or r.h <= 0):
                continue
            on_left = abs(fx - r.x) <= _EDGE_PX
            on_right = abs(fx - (r.x + r.w)) <= _EDGE_PX
            on_top = abs(fy - r.y) <= _EDGE_PX
            on_bot = abs(fy - (r.y + r.h)) <= _EDGE_PX
            inside = (r.x - _EDGE_PX <= fx <= r.x + r.w + _EDGE_PX
                      and r.y - _EDGE_PX <= fy <= r.y + r.h + _EDGE_PX)
            if not inside:
                continue
            # Size-locked ROIs (pk / potion) only accept whole-box drag
            # so the box can be moved but not resized.
            if roi_id in self._SIZE_LOCKED_IDS:
                if (r.x - _EDGE_PX <= fx <= r.x + r.w + _EDGE_PX
                        and r.y - _EDGE_PX <= fy <= r.y + r.h + _EDGE_PX):
                    return roi_id, _HIT_INSIDE
                continue
            if on_top and on_left:   return roi_id, _HIT_TL
            if on_top and on_right:  return roi_id, _HIT_TR
            if on_bot and on_left:   return roi_id, _HIT_BL
            if on_bot and on_right:  return roi_id, _HIT_BR
            if on_left:              return roi_id, _HIT_LEFT
            if on_right:             return roi_id, _HIT_RIGHT
            if on_top:               return roi_id, _HIT_TOP
            if on_bot:               return roi_id, _HIT_BOTTOM
            if r.x < fx < r.x + r.w and r.y < fy < r.y + r.h:
                return roi_id, _HIT_INSIDE
        return None, _HIT_NONE

    @staticmethod
    def _cursor_for(hit: int) -> Qt.CursorShape:
        return {
            _HIT_INSIDE: Qt.SizeAllCursor,
            _HIT_LEFT: Qt.SizeHorCursor,
            _HIT_RIGHT: Qt.SizeHorCursor,
            _HIT_TOP: Qt.SizeVerCursor,
            _HIT_BOTTOM: Qt.SizeVerCursor,
            _HIT_TL: Qt.SizeFDiagCursor,
            _HIT_BR: Qt.SizeFDiagCursor,
            _HIT_TR: Qt.SizeBDiagCursor,
            _HIT_BL: Qt.SizeBDiagCursor,
        }.get(hit, Qt.ArrowCursor)

    # ───────── mouse events ─────────
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._view_only:
            return super().mouseMoveEvent(e)
        fp = self._widget_to_frame(e.position().x(), e.position().y())
        if fp is None:
            self.unsetCursor()
            return

        if self._drag_id is None:
            roi_id, hit = self._hit_test(fp.x(), fp.y())
            self.setCursor(self._cursor_for(hit) if roi_id else Qt.ArrowCursor)
            return

        # Active drag
        start_pt = self._drag_start_pt
        start_r = self._drag_start_roi
        if start_pt is None or start_r is None:
            return
        dx = fp.x() - start_pt.x()
        dy = fp.y() - start_pt.y()

        new_r = _RoiRect(start_r.x, start_r.y, start_r.w, start_r.h)
        h = self._drag_hit
        if h == _HIT_INSIDE:
            new_r.x = start_r.x + dx
            new_r.y = start_r.y + dy
        else:
            if h in (_HIT_LEFT, _HIT_TL, _HIT_BL):
                new_r.x = start_r.x + dx
                new_r.w = start_r.w - dx
            if h in (_HIT_RIGHT, _HIT_TR, _HIT_BR):
                new_r.w = start_r.w + dx
            if h in (_HIT_TOP, _HIT_TL, _HIT_TR):
                new_r.y = start_r.y + dy
                new_r.h = start_r.h - dy
            if h in (_HIT_BOTTOM, _HIT_BL, _HIT_BR):
                new_r.h = start_r.h + dy

        # Clamp to frame and minimum size
        new_r.w = max(2, new_r.w)
        new_r.h = max(2, new_r.h)
        new_r.x = max(0, min(self._frame_size.width() - new_r.w, new_r.x))
        new_r.y = max(0, min(self._frame_size.height() - new_r.h, new_r.y))

        self._set_roi(self._drag_id, new_r)
        self.update()

    def set_view_only(self, on: bool) -> None:
        """Disable ROI drag/resize. Used by the fullscreen mirror."""
        self._view_only = bool(on)
        self.setCursor(Qt.ArrowCursor)

    def enter_pick_mode(self, idx: int) -> None:
        """Arm the next click to capture (x, y) into recovery step `idx`."""
        from quickcast.utils.logger import logger
        self._pick_mode_idx = int(idx)
        self._item_close_pick = False
        self.setCursor(Qt.CrossCursor)
        self.update()
        logger.debug(f"recovery: pick mode armed for step #{idx + 1}")

    def enter_item_close_pick_mode(self) -> None:
        """Arm the next click to capture (x, y) into the item-close coord."""
        from quickcast.utils.logger import logger
        self._item_close_pick = True
        self._pick_mode_idx = None
        self.setCursor(Qt.CrossCursor)
        self.update()
        logger.debug("item-close: pick mode armed")

    def _on_grace_changed(self, remaining: float) -> None:
        self._grace_remaining = max(0.0, float(remaining))
        self.update()

    def cancel_pick_mode(self) -> None:
        if self._pick_mode_idx is None:
            return
        from quickcast.utils.logger import logger
        logger.info("recovery: pick mode cancelled (no click)")
        self._pick_mode_idx = None
        self.unsetCursor()
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        # Pick mode short-circuits both view-only and ROI-drag paths.
        if self._pick_mode_idx is not None and e.button() == Qt.LeftButton:
            from quickcast.utils.logger import logger
            from quickcast.ui.design.signals import bus
            fp = self._widget_to_frame(e.position().x(), e.position().y())
            if fp is None:
                logger.warning(
                    f"recovery: click outside preview — pick ignored "
                    f"(widget=({int(e.position().x())},{int(e.position().y())}))"
                )
                return
            idx = self._pick_mode_idx
            self._pick_mode_idx = None
            self.unsetCursor()
            self.update()
            logger.debug(f"recovery: step #{idx + 1} coords ({fp.x()},{fp.y()})")
            bus.recovery_pick_done.emit(idx, int(fp.x()), int(fp.y()))
            e.accept()
            return

        # Item-close pick mode — same idea, single coord.
        if self._item_close_pick and e.button() == Qt.LeftButton:
            from quickcast.utils.logger import logger
            from quickcast.ui.design.signals import bus
            fp = self._widget_to_frame(e.position().x(), e.position().y())
            if fp is None:
                return
            self._item_close_pick = False
            self.unsetCursor()
            self.update()
            logger.debug(f"item-close: coords ({fp.x()},{fp.y()})")
            bus.item_close_pick_done.emit(int(fp.x()), int(fp.y()))
            e.accept()
            return

        if self._view_only or e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        # First, check label hit (label area drags the ROI as a "handle")
        wpt = QPoint(int(e.position().x()), int(e.position().y()))
        for roi_id, lrect in self._label_rects.items():
            if lrect.contains(wpt):
                fp = self._widget_to_frame(wpt.x(), wpt.y()) or QPoint(0, 0)
                self._drag_id = roi_id
                self._drag_hit = _HIT_INSIDE
                self._drag_start_pt = fp
                self._drag_start_roi = self._get_roi(roi_id)
                e.accept()
                return

        fp = self._widget_to_frame(e.position().x(), e.position().y())
        if fp is None:
            return
        roi_id, hit = self._hit_test(fp.x(), fp.y())
        if roi_id is None:
            return
        self._drag_id = roi_id
        self._drag_hit = hit
        self._drag_start_pt = fp
        self._drag_start_roi = self._get_roi(roi_id)
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag_id is not None:
            # Persist on release. Use the same `bus.settings_dirty` path
            # the rest of the UI uses so AppWindow's debounced save (which
            # writes the SAME mock_settings instance the UI mutated) picks
            # this up. Calling self.settings.save() directly bypasses the
            # debouncer but is harmless — both end up writing to disk.
            try:
                from quickcast.ui.design.signals import bus
                bus.settings_dirty.emit()
                self.settings.save()
            except Exception as exc:
                from quickcast.utils.logger import logger
                logger.exception(f"ROI drag save failed: {exc}")
            self._drag_id = None
            self._drag_hit = _HIT_NONE
            self._drag_start_pt = None
            self._drag_start_roi = None
        super().mouseReleaseEvent(e)

    # ───────── paint ─────────
    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))

        self._render_rect = self._compute_render_rect()

        if self._frame_pixmap is None:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "게임 창을 선택하면 미리보기가 표시됩니다\n"
                       "선택 후 ROI 사각형을 드래그해서 영역을 조정하세요")
            return

        p.drawPixmap(self._render_rect, self._frame_pixmap)

        # Draw ROI rectangles. Labels are placed OUTSIDE the rect so they
        # never cover the area being analyzed; placement is chosen per ROI
        # to avoid overlap.
        p.setRenderHint(QPainter.Antialiasing, False)
        self._label_rects.clear()

        # Pass 1: rectangles + edge ticks
        active_defs = self._active_roi_defs()
        rect_widget: dict[str, QRect] = {}
        # Drop OCR text ROIs whose dimensions are 0 (user hasn't run
        # auto-detect or dragged them yet) — drawing a zero-size box
        # produces a confusing pixel-sized dot at the origin.
        active_defs = [
            (rid, lab, col) for (rid, lab, col) in active_defs
            if not rid.endswith("_text") or
            (self._get_roi(rid).w > 0 and self._get_roi(rid).h > 0)
        ]
        # Draw small / icon-template ROIs LAST so they end up on top of
        # the larger HUD text rectangles — at 25×25 (PK) and 13×13
        # (POTION) they otherwise visually disappear under a bigger
        # text region that happens to overlap.
        active_defs.sort(key=lambda d: 1 if d[0] in ("pk", "potion") else 0)

        # Tiny icon ROIs (25×25, 13×13) shrink to ~10 widget pixels at
        # typical preview scale. We grow the *drawn* box around its
        # centre to at least MIN_VISIBLE_PX so the user can actually
        # see and target it — the underlying ROI / search region in
        # settings is unchanged.
        MIN_VISIBLE_PX = 22

        for roi_id, _label, color in active_defs:
            r = self._get_roi(roi_id)
            tl = self._frame_to_widget(r.x, r.y)
            br = self._frame_to_widget(r.x + r.w, r.y + r.h)
            wrect = QRect(tl, br)
            is_small = roi_id in ("pk", "potion")

            if is_small and (wrect.width() < MIN_VISIBLE_PX
                              or wrect.height() < MIN_VISIBLE_PX):
                cx = wrect.center().x()
                cy = wrect.center().y()
                nw = max(wrect.width(), MIN_VISIBLE_PX)
                nh = max(wrect.height(), MIN_VISIBLE_PX)
                wrect = QRect(cx - nw // 2, cy - nh // 2, nw, nh)

            rect_widget[roi_id] = wrect

            # Stroke: 3px for small icon ROIs, 1px otherwise.
            stroke = 3 if is_small else 1
            p.setPen(QPen(color, stroke)); p.setBrush(Qt.NoBrush)
            p.drawRect(wrect)
            if is_small:
                # 5-pixel solid corner squares anchor the box visually
                # even on busy game backgrounds.
                p.setBrush(color)
                p.setPen(Qt.NoPen)
                sz = 5
                for cx, cy in (
                    (wrect.left(), wrect.top()),
                    (wrect.right() - sz, wrect.top()),
                    (wrect.left(), wrect.bottom() - sz),
                    (wrect.right() - sz, wrect.bottom() - sz),
                ):
                    p.drawRect(cx, cy, sz, sz)
                p.setBrush(Qt.NoBrush)
            else:
                # Subtle edge midpoint ticks for the larger boxes.
                p.setPen(QPen(color, 2))
                mids = [
                    ((wrect.left() + wrect.right()) // 2, wrect.top()),
                    ((wrect.left() + wrect.right()) // 2, wrect.bottom()),
                    (wrect.left(), (wrect.top() + wrect.bottom()) // 2),
                    (wrect.right(), (wrect.top() + wrect.bottom()) // 2),
                ]
                for mx, my in mids:
                    p.drawPoint(mx, my)

        # Pass 1.5: match-position overlay for PK / potion search boxes.
        # Inside each enlarged search ROI we draw a thin white rectangle
        # at the matcher's best-hit location, true template scale. Lets
        # the user visually verify that template-matching locked onto
        # the actual icon (not a stray HUD element).
        for roi_id in ("pk", "potion"):
            if roi_id not in rect_widget:
                continue
            mxy = self._match_xy.get(roi_id, (-1, -1))
            if mxy[0] < 0 or mxy[1] < 0:
                continue
            tw_native, th_native = self._tmpl_size.get(roi_id, (0, 0))
            if tw_native <= 0 or th_native <= 0:
                continue
            s = self._match_scale.get(roi_id, 1.0) or 1.0
            mw = max(1, int(round(tw_native * s)))
            mh = max(1, int(round(th_native * s)))
            tl = self._frame_to_widget(mxy[0], mxy[1])
            br = self._frame_to_widget(mxy[0] + mw, mxy[1] + mh)
            inner = QRect(tl, br)
            # White stroke (high contrast against gold/green outer box
            # and any in-game backdrop). 2px so it stays visible even
            # when the search box is small.
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(inner)

        # Pass 2: labels — placed outside each ROI in a side that minimises
        # overlap with the other ROIs.
        # Preferred sides per ROI: HP/MP labels go to the right of the rect,
        # PK/POTION labels go above the rect (they're tiny boxes).
        SIDE_PREFS = {
            "hp": ("right", "below", "above", "left"),
            "mp": ("right", "below", "above", "left"),
            "pk": ("above", "left", "below", "right"),
            "potion": ("above", "right", "below", "left"),
            "hp_text": ("right", "below", "above", "left"),
            "mp_text": ("right", "below", "above", "left"),
            "potion_text": ("above", "right", "below", "left"),
        }
        # Default placement order for any ROI not in the hard-coded
        # prefs (e.g. "overlay:pet_whistle"). Overlay boxes live in
        # the top centre of the frame so "below" reads cleanly without
        # clipping off the top edge.
        _SIDE_FALLBACK = ("below", "above", "right", "left")
        widget_rect = QRect(0, 0, self.width(), self.height())

        def _place(side: str, around: QRect, sz_w: int, sz_h: int) -> QRect:
            if side == "right":
                return QRect(around.right() + 4, around.top() - 2, sz_w, sz_h)
            if side == "left":
                return QRect(around.left() - sz_w - 4, around.top() - 2, sz_w, sz_h)
            if side == "above":
                return QRect(around.left(), around.top() - sz_h - 2, sz_w, sz_h)
            return QRect(around.left(), around.bottom() + 2, sz_w, sz_h)  # below

        placed: list[QRect] = []
        for roi_id, label, color in active_defs:
            text = self._roi_value_text.get(roi_id) or label
            tw = p.fontMetrics().horizontalAdvance(text) + 12
            th = p.fontMetrics().height() + 4
            wrect = rect_widget[roi_id]

            best: Optional[QRect] = None
            sides = SIDE_PREFS.get(roi_id, _SIDE_FALLBACK)
            for side in sides:
                cand = _place(side, wrect, tw, th)
                if not widget_rect.contains(cand):
                    continue
                # Avoid overlap with already-placed labels and other ROI rects
                conflict = any(cand.intersects(prev) for prev in placed)
                conflict = conflict or any(
                    cand.intersects(rw) for rid, rw in rect_widget.items() if rid != roi_id
                )
                if not conflict:
                    best = cand; break
            if best is None:
                # Fallback: nudge slightly so it's at least visible
                first_side = SIDE_PREFS.get(roi_id, _SIDE_FALLBACK)[0]
                best = _place(first_side, wrect, tw, th)
                # Stack downward if it overlaps prior labels
                while any(best.intersects(prev) for prev in placed) and best.bottom() < self.height():
                    best.translate(0, th + 2)

            placed.append(best)
            self._label_rects[roi_id] = best

            # Pill background + colored text
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 210))
            p.drawRoundedRect(best, 4, 4)
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(color)
            p.drawText(best, Qt.AlignCenter, text)

        # ───────── recovery overlays ─────────
        # Saved recovery step markers — small numbered cyan circles so
        # the user can see where they've placed click points.
        try:
            settings_obj = self.settings
            steps = getattr(getattr(settings_obj, "recovery", None), "steps", []) or []
        except Exception:
            steps = []
        if steps:
            p.setRenderHint(QPainter.Antialiasing, True)
            for i, step in enumerate(steps, 1):
                # Key-only steps don't have a meaningful screen position,
                # so we don't paint a marker for them. Skip silently.
                if getattr(step, "key", ""):
                    continue
                wp = self._frame_to_widget(int(step.x), int(step.y))
                if wp is None:
                    continue
                # Outer halo for visibility on bright frames
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 180))
                p.drawEllipse(wp, 12, 12)
                p.setBrush(QColor(56, 189, 248, 230))    # cyan
                p.drawEllipse(wp, 9, 9)
                p.setPen(QColor(255, 255, 255))
                f = p.font(); f.setBold(True); f.setPointSize(8); p.setFont(f)
                p.drawText(QRect(wp.x() - 9, wp.y() - 9, 18, 18),
                           Qt.AlignCenter, str(i))

        # Master grace-period countdown overlay — large dimmed number
        # centered over the preview while the 3-second arming wait runs.
        if self._grace_remaining > 0:
            p.setRenderHint(QPainter.Antialiasing, True)
            # Translucent dark veil so the number stands out
            p.fillRect(self.rect(), QColor(0, 0, 0, 130))
            n = int(self._grace_remaining) + (1 if self._grace_remaining % 1 > 0 else 0)
            f = p.font(); f.setBold(True); f.setPointSize(72); p.setFont(f)
            p.setPen(QColor(91, 141, 239))
            p.drawText(self.rect(), Qt.AlignCenter, str(n))
            # Sub-label
            f2 = p.font(); f2.setBold(False); f2.setPointSize(14); p.setFont(f2)
            p.setPen(QColor(220, 220, 220))
            sub_rect = self.rect().adjusted(0, 80, 0, 0)
            p.drawText(sub_rect, Qt.AlignCenter,
                        "매크로 시작 대기 중…")

        # Pick-mode banner — bottom-center prompt while waiting for click.
        if self._pick_mode_idx is not None:
            msg = f"복귀 단계 #{self._pick_mode_idx + 1} 위치를 클릭하세요  (Esc 취소)"
            p.setRenderHint(QPainter.Antialiasing, True)
            f = p.font(); f.setBold(True); f.setPointSize(11); p.setFont(f)
            metrics = p.fontMetrics()
            tw = metrics.horizontalAdvance(msg) + 28
            th = metrics.height() + 14
            x = (self.width() - tw) // 2
            y = self.height() - th - 16
            banner = QRect(x, y, tw, th)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(91, 141, 239, 230))
            p.drawRoundedRect(banner, 6, 6)
            p.setPen(QColor(255, 255, 255))
            p.drawText(banner, Qt.AlignCenter, msg)


__all__ = ["InteractivePreview"]
