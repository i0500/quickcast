"""DXGI-backed screen capture (mss) — supports both monitor and window modes.

Two capture sources:
  - `MonitorCapture`: full primary monitor (legacy fallback)
  - `WindowCapture`: crops to a chosen game window's client area, then
    resizes to the macro's normalised 1280x720 working frame so the
    embedded PK/potion templates match regardless of game resolution.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional, Protocol

import mss
import numpy as np

from quickcast.config import Point
from quickcast.utils.window_finder import (
    WindowRect, get_client_rect_screen, get_window_rect, is_window_alive,
)


# The original macro normalised the captured frame to 1280x720 before
# slicing ROIs. We do the same for coordinate compatibility with existing
# user configs (and the embedded PK/potion target images).
TARGET_W = 1280
TARGET_H = 720
_TARGET_ASPECT = TARGET_W / TARGET_H


def _letterbox_into(raw: np.ndarray, dst: np.ndarray) -> None:
    """Resize ``raw`` into ``dst`` (1280×720) preserving source aspect.

    Replaces the legacy ``cv2.resize`` stretch that warped non-16:9 sources
    onto the 16:9 frame — game UI elements got squished vertically (16:10
    fullscreen) or horizontally (5:4 laptop), and ROIs calibrated at one
    aspect didn't fit any other.

    Letterboxing keeps the source's pixel proportions intact: the content
    is uniformly scaled to fit inside ``dst`` and the remaining margin
    (top/bottom for wider-than-target, left/right for taller-than-target)
    is filled with black. Recognition still runs on a 1280×720 frame so
    no downstream code needs to change.
    """
    import cv2
    sh, sw = raw.shape[:2]
    if sw <= 0 or sh <= 0:
        dst.fill(0)
        return
    if sw == TARGET_W and sh == TARGET_H:
        np.copyto(dst, raw)
        return
    src_aspect = sw / sh
    # Tolerance band around 16:9 — anything within ±5 % stretches to the
    # full 1280×720 frame instead of letterboxing. Catches the bordered
    # / windowed-fullscreen case where the game's client area is
    # monitor_w × (monitor_h - title_bar) ≈ 1920×1040 (aspect 1.846).
    # Strict letterboxing there put ~10 px black bars top+bottom and
    # silently pushed every calibrated ROI down by the bar height —
    # the "테두리 있는 전체화면에서 ROI 위치 틀어짐" report. 5 % keeps
    # genuinely different aspects (5:4 / 16:10 / ultrawide) letterboxed.
    if abs(src_aspect - _TARGET_ASPECT) / _TARGET_ASPECT < 0.05:
        cv2.resize(raw, (TARGET_W, TARGET_H), dst=dst,
                    interpolation=cv2.INTER_AREA)
        return
    # Fit inside (the smaller dimension wins, the other gets bars).
    if src_aspect > _TARGET_ASPECT:
        # wider than 16:9 — bars top + bottom.
        new_w = TARGET_W
        new_h = max(1, int(round(TARGET_W / src_aspect)))
    else:
        # taller than 16:9 — bars left + right.
        new_h = TARGET_H
        new_w = max(1, int(round(TARGET_H * src_aspect)))
    interp = cv2.INTER_AREA if (new_w < sw or new_h < sh) else cv2.INTER_LINEAR
    resized = cv2.resize(raw, (new_w, new_h), interpolation=interp)
    dst.fill(0)
    ox = (TARGET_W - new_w) // 2
    oy = (TARGET_H - new_h) // 2
    dst[oy:oy + new_h, ox:ox + new_w] = resized


@dataclass
class Frame:
    """A single normalised 1280x720 BGRA frame."""
    image: np.ndarray  # (720, 1280, 4) uint8 BGRA

    def crop(self, point: Point, w: int, h: int, y_offset: int = 0) -> np.ndarray:
        """Return a contiguous BGRA slice, clamped to the frame bounds.

        Negative / out-of-range ROI coords (e.g. after a stale
        aspect-profile load before the user re-calibrates) used to
        return an empty array via Python negative indexing — which then
        looked like the ROI had "vanished" downstream. We clamp to
        keep at least an in-bounds rectangle whenever any of it
        overlaps the frame; if the ROI is fully outside the frame we
        return an empty (0, 0, 4) slice so callers can still operate
        without crashing.
        """
        fh, fw = self.image.shape[:2]
        x0 = max(0, min(int(point.x), fw))
        y0 = max(0, min(int(point.y + y_offset), fh))
        x1 = max(x0, min(int(point.x) + int(w), fw))
        y1 = max(y0, min(int(point.y + y_offset) + int(h), fh))
        return self.image[y0:y1, x0:x1].copy()


class CaptureSource(Protocol):
    """Anything that can produce a normalised Frame."""
    @property
    def description(self) -> str: ...
    @property
    def healthy(self) -> bool: ...
    def grab(self) -> Frame: ...
    def close(self) -> None: ...


class BlankCapture:
    """검은 프레임만 내보내는 캡처 소스. 캡처 대상을 '지우기' 했을 때 컨트롤러
    캡처를 이걸로 바꿔, 모니터링 화면이 이전 게임창에 멈춰 있지 않고 검은
    화면(=대상 없음)을 보이게 한다. 검은 프레임이라 HP/MP·PK·물약 인식은
    모두 무반응(매치 없음)이 되지만, 보통 대상을 지우는 시점은 매크로를 멈춘
    상태라 무해하다."""

    def __init__(self) -> None:
        self._frame = np.zeros((TARGET_H, TARGET_W, 4), dtype=np.uint8)
        self._frame[:, :, 3] = 255          # 불투명 알파
        self.last_source_size: tuple[int, int] = (0, 0)
        self.last_window_dpi: int = 96

    @property
    def description(self) -> str:
        return "대상 없음"

    @property
    def healthy(self) -> bool:
        return True

    def grab(self) -> Frame:
        return Frame(image=self._frame)

    def close(self) -> None:
        pass


class _MssBase:
    """Shared mss + per-thread isolation + resize helpers."""
    def __init__(self) -> None:
        self._tls = threading.local()
        # Triple-buffered resize destinations — see WindowPrintCapture
        # for the rationale. Eliminates per-frame allocation so 24h+
        # runs maintain steady memory.
        self._pool: list[np.ndarray] = [
            np.empty((TARGET_H, TARGET_W, 4), dtype=np.uint8) for _ in range(3)
        ]
        self._pool_idx: int = 0
        # Width/height of the most recent SOURCE grab (pre-resize). Used
        # by the controller to detect aspect-ratio changes so it can
        # swap to the matching ROI profile.
        self.last_source_size: tuple[int, int] = (0, 0)
        # Monitor-capture has no per-window DPI to query, so this stays
        # 96; the diagnostic UI just shows source size without DPI then.
        self.last_window_dpi: int = 96

    def _sct(self) -> mss.base.MSSBase:
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._tls.sct = sct
        return sct

    def _to_target_frame(self, raw: np.ndarray) -> Frame:
        dst = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % len(self._pool)
        _letterbox_into(raw, dst)
        return Frame(image=dst)

    def close(self) -> None:
        sct = getattr(self._tls, "sct", None)
        if sct is not None:
            sct.close()
            self._tls.sct = None


class MonitorCapture(_MssBase):
    """Capture an entire monitor — used when no game window is selected."""

    def __init__(self, monitor_index: int = 1) -> None:
        super().__init__()
        self.monitor_index = monitor_index

    @property
    def description(self) -> str:
        return f"모니터 {self.monitor_index} 전체"

    @property
    def healthy(self) -> bool:
        return True

    def grab(self) -> Frame:
        sct = self._sct()
        raw = np.asarray(sct.grab(sct.monitors[self.monitor_index]))
        self.last_source_size = (int(raw.shape[1]), int(raw.shape[0]))
        return self._to_target_frame(raw)


class WindowCapture(_MssBase):
    """Capture only the chosen window's client area.

    Resolves the window once on construction and re-resolves if the HWND
    becomes invalid (e.g. game restart). Use `set_target` to switch.
    """

    def __init__(self, hwnd: int, label: str = "") -> None:
        super().__init__()
        self.hwnd = hwnd
        self.label = label or f"hwnd 0x{hwnd:X}"

    @property
    def description(self) -> str:
        return f"창: {self.label}"

    @property
    def healthy(self) -> bool:
        return self.hwnd != 0 and is_window_alive(self.hwnd)

    def set_target(self, hwnd: int, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label or self.label

    def _rect(self) -> Optional[WindowRect]:
        # Prefer client rect (no title bar/borders) so coordinates match
        # what users see in the game.
        return get_client_rect_screen(self.hwnd) or get_window_rect(self.hwnd)

    def grab(self) -> Frame:
        if not self.healthy:
            raise RuntimeError("Target window not available")
        rect = self._rect()
        if rect is None or rect.width <= 0 or rect.height <= 0:
            raise RuntimeError("Target window has no usable rect")
        sct = self._sct()
        region = {"left": rect.left, "top": rect.top,
                  "width": rect.width, "height": rect.height}
        raw = np.asarray(sct.grab(region))
        self.last_source_size = (int(rect.width), int(rect.height))
        return self._to_target_frame(raw)


# Backwards-compat alias used by older callers
ScreenCapture = MonitorCapture


__all__ = [
    "CaptureSource", "MonitorCapture", "WindowCapture",
    "ScreenCapture",  # alias
    "Frame", "TARGET_W", "TARGET_H",
]
