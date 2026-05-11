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


@dataclass
class Frame:
    """A single normalised 1280x720 BGRA frame."""
    image: np.ndarray  # (720, 1280, 4) uint8 BGRA

    def crop(self, point: Point, w: int, h: int, y_offset: int = 0) -> np.ndarray:
        """Return a contiguous BGRA slice (caller owns the view-or-copy)."""
        y = point.y + y_offset
        return self.image[y : y + h, point.x : point.x + w].copy()


class CaptureSource(Protocol):
    """Anything that can produce a normalised Frame."""
    @property
    def description(self) -> str: ...
    @property
    def healthy(self) -> bool: ...
    def grab(self) -> Frame: ...
    def close(self) -> None: ...


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

    def _sct(self) -> mss.base.MSSBase:
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._tls.sct = sct
        return sct

    def _to_target_frame(self, raw: np.ndarray) -> Frame:
        dst = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % len(self._pool)
        if raw.shape[1] != TARGET_W or raw.shape[0] != TARGET_H:
            import cv2
            cv2.resize(raw, (TARGET_W, TARGET_H),
                       dst=dst, interpolation=cv2.INTER_AREA)
        else:
            np.copyto(dst, raw)
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
        return self._to_target_frame(raw)


# Backwards-compat alias used by older callers
ScreenCapture = MonitorCapture


__all__ = [
    "CaptureSource", "MonitorCapture", "WindowCapture",
    "ScreenCapture",  # alias
    "Frame", "TARGET_W", "TARGET_H",
]
