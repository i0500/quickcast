"""Win32 helpers — locate a target window by title and read its rect.

Pure ctypes (no pywin32 dependency) so the bundled exe stays slim.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

user32 = ctypes.windll.user32

# IMPORTANT: declare every prototype so 64-bit HWNDs aren't truncated.
# Without these, ctypes defaults to c_int (32-bit) for return values,
# which silently corrupts large pointer-sized values on Win64.
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL


# Default substrings to look for. Lineage W (PURPLE) main window titles
# include "리니지W" or "Lineage W"; PURPLE launcher includes "퍼플".
# We deliberately do NOT include the bare "LINEAGE" substring — it
# matches dev artefacts like "lineage-w-macro-redesign" in IDE/terminal
# titles. "Lineage W" with the trailing space is specific enough.
DEFAULT_PATTERNS = ["리니지W", "Lineage W", "퍼플", "PURPLE"]


# Window titles we want to deliberately *exclude* even if they would
# otherwise match a pattern (mostly dev / browser tabs that spell out
# the project name).
EXCLUDE_PATTERNS = [
    "vscode", "visual studio code", "cursor", "explorer", "powershell",
    "terminal", "cmd.exe", "github", "claude", "lineage-w-macro",
]


@dataclass
class WindowRect:
    hwnd: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


# Minimum size for a window to be considered a game candidate. A
# "PURPLE" launcher's collapsed title bar registers as ~160×28 and
# would match the "PURPLE" pattern, but it's clearly not the game —
# anything smaller than this gate is dropped during auto-detect so a
# closed/minimised game doesn't auto-rebind to a launcher artefact.
#
# Threshold tuned down from 600×400 → 320×240 after a user-windowed
# Lineage W (smallish floating mode) got rejected and forced the app
# into monitor fallback. Both axes must clear the gate, so portrait
# 400×800 still passes and the launcher 160×28 still fails.
_MIN_GAME_W = 320
_MIN_GAME_H = 240


def _window_size(hwnd: int) -> tuple[int, int]:
    """Return (width, height) of hwnd's outer rect, (0,0) on failure.

    Iconified (minimised) windows have a parked rect at ~(-32000,-32000)
    with a tiny size — we fall back to ``GetWindowPlacement.rcNormalPosition``
    so the size gate sees the restored dimensions rather than the
    taskbar-thumb dimensions.
    """
    # IsIconic is missing from wintypes — bind ad-hoc.
    try:
        is_iconic = bool(user32.IsIconic(hwnd))
    except Exception:
        is_iconic = False
    if is_iconic:
        class _WP(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.UINT),
                ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT),
            ]
        wp = _WP(); wp.length = ctypes.sizeof(_WP)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            r = wp.rcNormalPosition
            return (r.right - r.left, r.bottom - r.top)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    return (rect.right - rect.left, rect.bottom - rect.top)


def _find_one(pattern: str, *, min_w: int = _MIN_GAME_W,
                min_h: int = _MIN_GAME_H) -> Optional[int]:
    """Single-pattern enumeration with EXCLUDE_PATTERNS + size filtering.

    Size gate keeps a launcher's collapsed title bar (~160×28) from being
    picked when the game itself isn't running. Pass ``min_w=0, min_h=0``
    to disable the gate (e.g. for the window-picker UI which lists every
    visible window regardless of size).
    """
    excludes = [p.lower() for p in EXCLUDE_PATTERNS]
    needle = pattern.lower()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd).lower()
        if not title:
            return True
        if any(x in title for x in excludes):
            return True
        if needle not in title:
            return True
        if min_w > 0 or min_h > 0:
            w, h = _window_size(hwnd)
            if w < min_w or h < min_h:
                return True
        found.append(hwnd)
        return False

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def find_window(patterns: list[str] | None = None,
                  *, enforce_min_size: bool = True) -> Optional[int]:
    """Return HWND of the first window matching one of `patterns`.

    Patterns are tried IN ORDER — the most specific / preferred pattern
    wins even if it appears later in window z-order. So `["리니지W",
    "Lineage W"]` will pick the Korean game window over a generic
    "Lineage" steam tile.

    `EXCLUDE_PATTERNS` always filters out IDE/terminal/browser windows.
    When ``enforce_min_size`` is True (the default), candidate windows
    must be at least _MIN_GAME_W × _MIN_GAME_H pixels — keeps launcher
    title-bar artefacts (~160×28) from being auto-picked when the
    game itself isn't running.
    """
    pats = patterns or DEFAULT_PATTERNS
    if enforce_min_size:
        kwargs = {}    # use module defaults
    else:
        kwargs = {"min_w": 0, "min_h": 0}
    for p in pats:
        hwnd = _find_one(p, **kwargs)
        if hwnd:
            return hwnd
    return None


def get_window_rect(hwnd: int) -> Optional[WindowRect]:
    """Get screen coordinates of `hwnd`'s outer rect; None if invalid."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return WindowRect(hwnd=hwnd, left=rect.left, top=rect.top,
                      right=rect.right, bottom=rect.bottom)


def is_window_alive(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd))


@dataclass
class WindowEntry:
    hwnd: int
    title: str


def list_visible_windows(min_size: int = 200) -> list[WindowEntry]:
    """Return all top-level visible windows that look big enough to be apps."""
    out: list[WindowEntry] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title.strip():
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if (rect.right - rect.left) < min_size or (rect.bottom - rect.top) < min_size:
            return True
        out.append(WindowEntry(hwnd=hwnd, title=title))
        return True

    user32.EnumWindows(cb, 0)
    # Sort by title for stable display order
    out.sort(key=lambda w: w.title.lower())
    return out


def get_client_rect_screen(hwnd: int) -> Optional[WindowRect]:
    """Get the client-area rect in screen coordinates (excludes title bar/borders)."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return WindowRect(
        hwnd=hwnd,
        left=pt.x, top=pt.y,
        right=pt.x + rect.right, bottom=pt.y + rect.bottom,
    )


__all__ = [
    "WindowRect", "WindowEntry", "DEFAULT_PATTERNS",
    "find_window", "get_window_rect", "get_client_rect_screen",
    "is_window_alive", "list_visible_windows",
]
