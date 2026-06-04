"""Windows.Graphics.Capture (WGC) wrapper — true hwnd-only window capture.

When PrintWindow is refused (Vulkan/D3D 가속 게임) and mss falls back to
monitor-region capture (which picks up any other window placed on top of
the game), this WGC path uses the modern Win10 1809+ Windows.Graphics.
Capture API to grab the game window's GPU surface directly. The result:

  • hwnd-bound — overlapping windows are NOT in the frame.
  • Works on PrintWindow-refusing games (DirectX 11/12, Vulkan).
  • Background / minimised windows still produce frames (depends on
    compositor — Win11 generally yes).

Backed by the ``windows-capture`` PyPI package (Rust ↔ WinRT binding).
Callback-driven, so we run it on its own thread (``start_free_threaded``)
and expose a synchronous ``grab()`` that returns the latest cached frame
— matching the same ``CaptureSource`` shape as WindowPrintCapture /
WindowCapture so the controller doesn't care which backend it's on.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from quickcast.core.capture import (
    Frame, TARGET_H, TARGET_W, _letterbox_into,
)
from quickcast.utils.logger import logger


class WGCWindowCapture:
    """Capture a window via Windows.Graphics.Capture.

    Lifecycle:
      • __init__ starts a background WGC session, waits briefly for the
        first frame so grab() can return immediately.
      • on_frame_arrived (WGC thread) caches the latest BGRA buffer.
      • grab() copies + letterboxes into the shared 1280×720 frame pool.
      • close() stops the WGC session.
    """

    def __init__(self, hwnd: int, label: str = "") -> None:
        from windows_capture import WindowsCapture

        self.hwnd = int(hwnd)
        self.label = label
        self._lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        # Triple-buffered destinations — same pattern as MonitorCapture /
        # WindowPrintCapture so 24h+ runs don't allocate per frame.
        self._pool: list[np.ndarray] = [
            np.empty((TARGET_H, TARGET_W, 4), dtype=np.uint8) for _ in range(3)
        ]
        self._pool_idx: int = 0
        self.last_source_size: tuple[int, int] = (0, 0)
        # WGC reads the actual GPU surface — DPI is whatever the window
        # was created at, but we don't query it here (downstream UI uses
        # it for label diagnostics only).
        self.last_window_dpi: int = 96
        self._closed: bool = False

        cap = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=int(hwnd),
        )

        @cap.event
        def on_frame_arrived(frame, control):  # noqa: ARG001 — control kept for API
            try:
                buf = frame.frame_buffer
                # frame_buffer is (H, W, 4) uint8 BGRA from WGC.
                with self._lock:
                    # copy() so the WGC layer is free to recycle its
                    # internal texture buffer the moment this callback
                    # returns. grab() reads from this stable copy.
                    self._latest = np.array(buf, copy=True)
                    self.last_source_size = (int(buf.shape[1]), int(buf.shape[0]))
            except Exception:
                logger.exception(f"WGC frame handler error ({self.label[:24]})")

        @cap.event
        def on_closed():
            self._closed = True
            logger.info(f"WGC 세션 종료: {self.label[:24]}")

        self._capture = cap
        try:
            self._control = cap.start_free_threaded()
        except Exception as e:
            logger.exception("WGC start_free_threaded 실패")
            raise RuntimeError(f"WGC 세션 시작 실패: {e}") from e

        # Wait up to 1.5s for the first frame so the first grab() doesn't
        # immediately raise. Most games deliver within 50–100 ms.
        for _ in range(30):
            with self._lock:
                if self._latest is not None:
                    break
            time.sleep(0.05)

    @property
    def description(self) -> str:
        return f"WGC: {self.label}" if self.label else "WGC capture"

    @property
    def healthy(self) -> bool:
        return not self._closed

    def grab(self) -> Frame:
        with self._lock:
            raw = self._latest
            if raw is None:
                raise RuntimeError("WGC: 아직 첫 프레임 수신 전")
        # Letterbox into the shared 1280×720 normalised frame pool.
        dst = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % len(self._pool)
        _letterbox_into(raw, dst)
        return Frame(image=dst)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._control.stop()
        except Exception:
            logger.exception("WGC stop failed")
        self._closed = True
