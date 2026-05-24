"""PrintWindow-based window capture — equivalent to browser getDisplayMedia.

The browser's `navigator.mediaDevices.getDisplayMedia` uses Windows
Graphics Capture / PrintWindow under the hood: it grabs the *rendered*
content of a window even when the window is minimised, occluded by
other windows, or off-screen. This module reproduces that with pure
ctypes so we don't need extra dependencies.

Algorithm (matches what Chromium does for window capture):
  1. GetClientRect → window dimensions
  2. CreateCompatibleDC + CreateCompatibleBitmap (GDI surface)
  3. PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT) — asks the window to
     paint itself onto our surface, even if it's hidden.
  4. GetDIBits → numpy array
  5. Resize to 1280x720 to match the original macro's coordinate system.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

import cv2
import numpy as np

from quickcast.core.capture import Frame, TARGET_H, TARGET_W, _letterbox_into
from quickcast.utils.window_finder import is_window_alive

# ── Win32 prototypes ────────────────────────────────────────────────
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
SRCCOPY = 0xCC0020
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

# GetDpiForWindow — Win10 1607+. Used to detect DPI-aware/unaware
# mismatch between our PMv2 process and the target game window. When
# the game is DPI-unaware (or running on a different scale monitor),
# GetClientRect returns a virtualised/logical size while PrintWindow
# renders the actual physical pixels — so the bitmap we allocated is
# the wrong size and PrintWindow draws into the top-left corner only.
# We scale the client rect by the window's DPI ratio to keep the
# allocation in step with what PrintWindow will actually paint.
try:
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    _HAS_GET_DPI_FOR_WINDOW = True
except Exception:
    _HAS_GET_DPI_FOR_WINDOW = False

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int


class WindowMinimizedError(RuntimeError):
    """Raised when GetClientRect returns 0x0 AND we have no cached
    frame to return. Capture loop catches it specifically so the
    error message can be rate-limited instead of spamming the log."""


# ── Capture class ───────────────────────────────────────────────────
class WindowPrintCapture:
    """Capture a window's rendered content via PrintWindow API.

    Works on:
      - background windows (occluded by other windows)
      - minimised windows (most apps; some games refuse)
      - windows on secondary monitors with negative coordinates
      - windows that opt out of being captured by mss
    """

    def __init__(self, hwnd: int, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label or f"hwnd 0x{hwnd:X}"
        # Last successful frame + its size — re-served when the window
        # is minimised AND PrintWindow refuses to render fresh frames.
        self._last_frame: Optional[Frame] = None
        # Last KNOWN restored size (client area), used to allocate a
        # bitmap when the window is minimised (GetClientRect=0×0).
        self._last_w: int = 0
        self._last_h: int = 0
        # Reusable GetDIBits destination buffer. Allocated lazily on the
        # first grab and resized only when the source window's client
        # area changes — eliminates the 3.7MB-per-frame ctypes alloc
        # that would otherwise dominate memory churn over 24h+ runs.
        self._buf = None                  # type: ignore[assignment]
        self._buf_view: Optional[np.ndarray] = None
        self._buf_w: int = 0
        self._buf_h: int = 0
        # BITMAPINFO is fixed except for w/h — cache the struct so we
        # only stamp those two fields per call.
        self._bmi = BITMAPINFO()
        self._bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self._bmi.bmiHeader.biPlanes = 1
        self._bmi.bmiHeader.biBitCount = 32
        self._bmi.bmiHeader.biCompression = BI_RGB
        # Triple-buffered cv2.resize destinations. Round-robin index
        # advances per grab; with Qt QueuedConnection depth ≤ 2 this
        # guarantees no in-flight frame is ever overwritten.
        self._pool: list[np.ndarray] = [
            np.empty((TARGET_H, TARGET_W, 4), dtype=np.uint8) for _ in range(3)
        ]
        self._pool_idx: int = 0
        # Width/height of the most recent SOURCE client rect (pre-resize).
        # Read by the controller to detect aspect-ratio changes and swap
        # ROI profiles accordingly.
        self.last_source_size: tuple[int, int] = (0, 0)
        # Most recently observed window DPI (96 = 100% scaling). Exposed
        # for the capture UI's diagnostic label so users can spot DPI
        # mismatch problems at a glance.
        self.last_window_dpi: int = 96

    @property
    def description(self) -> str:
        return f"창: {self.label}"

    @property
    def healthy(self) -> bool:
        return self.hwnd != 0 and is_window_alive(self.hwnd)

    def grab(self) -> Frame:
        if not self.healthy:
            raise RuntimeError(f"Target window not available (hwnd 0x{self.hwnd:X})")

        rect = wintypes.RECT()
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise RuntimeError("GetClientRect failed")
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        # ── DPI mismatch correction ──
        # Our process is PMv2 (see main._force_pmv2_dpi); the target game
        # window may be running with a different DPI awareness context
        # (System-aware, Unaware, or a different per-monitor monitor).
        # In those cases GetClientRect returns a virtualised/logical size
        # while PrintWindow actually paints at the window's true physical
        # size — so the bitmap we allocate ends up larger than the painted
        # area and PrintWindow draws into the top-left corner only,
        # producing the "캡처 화면 1/4 좌상단에 게임이 작게 잡힘" symptom
        # reported under Windows 125%/150% scaling.
        # Solve it by querying the window's own DPI and scaling the client
        # rect by win_dpi/96 so the bitmap matches what PrintWindow paints.
        win_dpi = 96
        if _HAS_GET_DPI_FOR_WINDOW and w > 0 and h > 0:
            try:
                d = int(user32.GetDpiForWindow(self.hwnd) or 96)
                if d > 0:
                    win_dpi = d
            except Exception:
                win_dpi = 96
            if win_dpi != 96:
                w = max(1, int(round(w * win_dpi / 96.0)))
                h = max(1, int(round(h * win_dpi / 96.0)))
        self.last_window_dpi = int(win_dpi)
        minimized = (w <= 0 or h <= 0)
        if minimized:
            # Use the cached restored-state size so we can still ask
            # PrintWindow to render. This is the trick that lets games
            # update the macro's recognition pipeline even while the
            # window is minimised to the taskbar.
            if self._last_w > 0 and self._last_h > 0:
                w, h = self._last_w, self._last_h
            else:
                # Try GetWindowPlacement as a last-resort size source.
                from ctypes.wintypes import RECT, UINT
                class _WP(ctypes.Structure):
                    _fields_ = [("length", UINT), ("flags", UINT),
                                ("showCmd", UINT),
                                ("ptMinPosition", wintypes.POINT),
                                ("ptMaxPosition", wintypes.POINT),
                                ("rcNormalPosition", RECT)]
                wp = _WP(); wp.length = ctypes.sizeof(_WP)
                if user32.GetWindowPlacement(self.hwnd, ctypes.byref(wp)):
                    nw = wp.rcNormalPosition.right - wp.rcNormalPosition.left
                    nh = wp.rcNormalPosition.bottom - wp.rcNormalPosition.top
                    if nw > 0 and nh > 0:
                        # Approximate client size — close enough for
                        # bitmap allocation; PrintWindow will render
                        # whatever the window can produce.
                        w, h = nw, nh
                if w <= 0 or h <= 0:
                    if self._last_frame is not None:
                        return self._last_frame
                    raise WindowMinimizedError(
                        f"Window minimized (no cached size yet)"
                    )

        hdc_window = user32.GetDC(self.hwnd)
        if not hdc_window:
            raise RuntimeError("GetDC failed")

        hdc_mem = 0
        hbm = 0
        old_obj = 0
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
            if not hdc_mem:
                raise RuntimeError("CreateCompatibleDC failed")

            hbm = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
            if not hbm:
                raise RuntimeError("CreateCompatibleBitmap failed")

            old_obj = gdi32.SelectObject(hdc_mem, hbm)

            # PW_RENDERFULLCONTENT = 0x2 forces the window to paint its full
            # content (DirectComposition / DWM included). Some game windows
            # only render with PW_CLIENTONLY=0x1, so we try both.
            ok = user32.PrintWindow(self.hwnd, hdc_mem, PW_RENDERFULLCONTENT)
            if not ok:
                ok = user32.PrintWindow(self.hwnd, hdc_mem, PW_CLIENTONLY)
            if not ok:
                # Both PrintWindow flavours refused. We *used* to fall back
                # to BitBlt(hdc_window) — but that reads from the window's
                # actual screen position, so when another window is on top
                # of the game the matcher saw the OVERLAPPING window's
                # pixels (the "다른 화면이 같이 캡처됨" bug). Returning the
                # last known good frame keeps recognition on rails until
                # PrintWindow recovers; if we have nothing cached yet, we
                # raise so the controller's error path takes over.
                if self._last_frame is not None:
                    return self._last_frame
                raise RuntimeError(
                    "PrintWindow refused and no cached frame to fall back on"
                )

            # Pull the bitmap bits into the reusable destination buffer.
            # Reallocate only when the source size changes — typically
            # never after the first frame.
            if self._buf is None or self._buf_w != w or self._buf_h != h:
                self._buf = (ctypes.c_uint8 * (w * h * 4))()
                self._buf_view = np.frombuffer(
                    self._buf, dtype=np.uint8,
                ).reshape((h, w, 4))
                self._buf_w, self._buf_h = w, h
            self._bmi.bmiHeader.biWidth = w
            self._bmi.bmiHeader.biHeight = -h          # negative = top-down

            scan_lines = gdi32.GetDIBits(
                hdc_mem, hbm, 0, h, self._buf,
                ctypes.byref(self._bmi), DIB_RGB_COLORS,
            )
            if scan_lines == 0:
                raise RuntimeError("GetDIBits returned 0 scan lines")

            arr = self._buf_view
        finally:
            if old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            if hbm:
                gdi32.DeleteObject(hbm)
            if hdc_mem:
                gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(self.hwnd, hdc_window)

        # Normalise to 1280x720 into the next pool slot. Round-robin
        # advance keeps in-flight frames (held by the Qt signal queue
        # or the controller's _latest_frame slot) from being overwritten
        # mid-render. Frame.image references the pool slot directly —
        # zero alloc per grab on the steady-state path. ``_letterbox_into``
        # preserves source aspect (top/bottom or left/right bars) so a
        # 16:10 or 5:4 source doesn't get vertically/horizontally
        # squished — the previous cv2.resize stretch was the root of
        # "ROI 사이즈가 모드 바뀌면 달라짐" complaint.
        dst = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % len(self._pool)
        _letterbox_into(arr, dst)
        frame = Frame(image=dst)
        # Cache so subsequent grabs survive transient minimisation.
        self._last_frame = frame
        self.last_source_size = (int(w), int(h))
        return frame

    def close(self) -> None:
        # Nothing held between calls — every grab acquires/releases its DC.
        pass


__all__ = ["WindowPrintCapture"]
