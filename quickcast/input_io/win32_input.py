"""Win32-based key input backends — focus-free PostMessage + AttachThreadInput.

Two strategies:

PostMessageBackend
  - Sends WM_KEYDOWN/WM_KEYUP directly to a target HWND.
  - Works without focus, even when the window is in the background.
  - Many anti-cheat systems (incl. NCsoft's) silently ignore these.

AttachInputBackend
  - Briefly attaches our thread's input queue to the target's thread.
  - Sets focus to the target, fires SendInput, detaches, restores prior focus.
  - Causes a sub-frame focus flash but bypasses PostMessage filtering.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Optional

from quickcast.utils.logger import logger
from quickcast.utils.window_finder import is_window_alive

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── Win32 message constants ─────────────────────────────────────────
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
MK_LBUTTON = 0x0001


def click_at(hwnd: int, x: int, y: int, button: str = "left",
              hold_ms: int = 150, frame_size: tuple = None,
              method: str = "attach") -> None:
    """Click at game-window-CLIENT-relative (x,y) WITHOUT moving the
    real cursor (user explicitly forbade it).

    `frame_size` (frame_w, frame_h) is the size of the captured frame
    the user picked the coord against. When it differs from the game's
    real GetClientRect we apply the inverse scale before posting.

    `method`:
      "attach"      — DEFAULT. AttachThreadInput briefly so our posted
                      mouse messages appear to come from the game's
                      own input queue, then PostMessage the click.
                      No visible cursor movement.
      "postmessage" — Plain PostMessage WM_LBUTTONDOWN/UP. Fastest but
                      many games filter these out.
    """
    if not hwnd:
        return
    fx, fy = float(x), float(y)
    try:
        rect = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rect)):
            cw = rect.right - rect.left
            ch = rect.bottom - rect.top
            if frame_size and frame_size[0] > 0 and frame_size[1] > 0:
                fw, fh = frame_size
                if (fw, fh) != (cw, ch):
                    sx = cw / fw
                    sy = ch / fh
                    fx, fy = int(round(fx * sx)), int(round(fy * sy))
            elif not (0 <= x <= cw and 0 <= y <= ch):
                logger.warning(
                    f"⚠️ 클릭 좌표 ({x},{y})가 게임창 {cw}×{ch} 밖"
                )
    except Exception:
        logger.exception("click_at: client-rect probe failed")
    ix, iy = int(fx), int(fy)

    if method == "postmessage":
        _click_via_postmessage(hwnd, ix, iy, hold_ms)
    else:
        _click_via_attach(hwnd, ix, iy, hold_ms)


def _post_click_messages(hwnd: int, x: int, y: int, hold_ms: int) -> None:
    """Post the full hardware-click message sequence to `hwnd`.

    Real Windows mouse interaction generates many messages when the
    cursor moves and clicks: NCHITTEST → SETCURSOR → repeated
    MOUSEMOVE (the hardware fires dozens of intermediate moves
    during a real human click) → LBUTTONDOWN → LBUTTONUP. Some
    Lineage W menu icons (메뉴 / 던전 in particular) only enable
    their click handler AFTER the hover state has been stable for
    several frames, so a single MOUSEMOVE + immediate DOWN drops
    the click. Multiple MOUSEMOVEs spread over ~80 ms emulate the
    settle a real hardware click would produce, and a longer hold
    (min 150 ms) ensures the DOWN/UP straddle at least one full UI
    update tick.
    """
    lparam = (y & 0xFFFF) << 16 | (x & 0xFFFF)
    # Hover settle: 3 MOUSEMOVE messages with 30 ms gaps so the
    # game registers a stable hover. Each carries the same lParam —
    # the redundancy is what convinces the UI's hover debounce.
    for _ in range(3):
        user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.030)
    # Long hold so menu icons that require press-and-hold (or that
    # debounce DOWN/UP within the same frame) actually catch the
    # click. 150 ms is a single-digit frame at 60 fps but bullet-proof
    # against engines that batch input on a 30 fps cadence.
    user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
    time.sleep(max(0.150, hold_ms / 1000))
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam)
    # Trailing settle so the next click's MOUSEMOVE doesn't land in
    # the same message-loop tick as our UP.
    time.sleep(0.050)


def _click_via_postmessage(hwnd: int, x: int, y: int, hold_ms: int) -> None:
    """Plain PostMessage — no thread attach, no cursor move."""
    _post_click_messages(hwnd, x, y, hold_ms)


def _click_via_attach(hwnd: int, x: int, y: int, hold_ms: int) -> None:
    """AttachThreadInput → PostMessage → detach. No cursor move, no
    foreground change — works against hidden / background Lineage W
    windows. Attaching makes our posted messages appear to come from
    the game's own input queue, which is the difference that gets
    games like Lineage W to actually act on the click rather than
    just flashing the visual ripple effect."""
    with attach_input_scope(hwnd):
        _post_click_messages(hwnd, x, y, hold_ms)


class _AttachScope:
    """Context manager: AttachThreadInput on enter, detach on exit.

    Use around an entire click batch so the input-queue link stays
    open across multiple clicks. Without this, clicks #1 and #2 of
    a recovery sequence drop their first MOUSEMOVE while the queue
    is still being plumbed — only clicks #3+ land reliably.
    """

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self._our_tid = 0
        self._target_tid = 0
        self._attached = False

    def __enter__(self) -> "_AttachScope":
        if not self.hwnd:
            return self
        self._our_tid = kernel32.GetCurrentThreadId()
        self._target_tid = user32.GetWindowThreadProcessId(self.hwnd, None) or 0
        if self._target_tid and self._target_tid != self._our_tid:
            self._attached = bool(user32.AttachThreadInput(
                self._our_tid, self._target_tid, True
            ))
            if self._attached:
                # Warm-up — give the OS a moment to finish plumbing the
                # shared queue before the first message goes out.
                # 100 ms is ~6 frames at 60 fps, enough for any game
                # to settle its input-thread state machine even on the
                # very first attach after launch.
                time.sleep(0.100)
        return self

    def __exit__(self, *_exc) -> None:
        if self._attached:
            user32.AttachThreadInput(self._our_tid, self._target_tid, False)
            self._attached = False


def attach_input_scope(hwnd: int) -> _AttachScope:
    """Public entry point — `with attach_input_scope(hwnd): clicks()`."""
    return _AttachScope(hwnd)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# Virtual-key map for the keys typically used in macros
_VK_MAP = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "enter": 0x0D, "return": 0x0D,
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "backspace": 0x08,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    # Numpad — distinct from main-row digits/operators
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    "nummul": 0x6A, "numadd": 0x6B, "numsub": 0x6D,
    "numdec": 0x6E, "numdiv": 0x6F, "numenter": 0x0D,
}


def _vk_for(key: str) -> int:
    """Resolve a single-key string to a virtual-key code."""
    if not key:
        return 0
    k = key.lower()
    if k in _VK_MAP:
        return _VK_MAP[k]
    if len(key) == 1:
        ch = key.upper()
        if ch.isalnum():
            return ord(ch)
    # Fallback: use VkKeyScan
    return user32.VkKeyScanW(ctypes.c_wchar(key)) & 0xFF


# ── PostMessage backend ─────────────────────────────────────────────
class PostMessageBackend:
    """Sends WM_KEY* directly to the target window (no focus needed)."""

    KEY_HOLD_S = 0.04   # how long the key stays "down"

    def __init__(self, hwnd: int = 0, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label
        self.port = label or f"hwnd 0x{hwnd:X}"
        self.baud = 0
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.hwnd != 0 and is_window_alive(self.hwnd)

    def auto_detect(self) -> Optional[str]:
        return None

    def connect(self, _port: str = "") -> bool:
        return self.connected

    def close(self) -> None:
        pass

    def set_target(self, hwnd: int, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label
        self.port = label or f"hwnd 0x{hwnd:X}"

    def send_key(self, key: str) -> None:
        if not self.connected:
            logger.warning(f"⚠️ 게임창 미선택 — 키 {key!r} 무시됨")
            return
        vk = _vk_for(key)
        if not vk:
            logger.warning(f"⚠️ 알 수 없는 키: {key!r}")
            return
        scan = user32.MapVirtualKeyW(vk, 0)
        lparam_down = 0x00000001 | (scan << 16)
        lparam_up = lparam_down | (1 << 30) | (1 << 31)
        with self._lock:
            user32.PostMessageW(self.hwnd, WM_KEYDOWN, vk, lparam_down)
            time.sleep(self.KEY_HOLD_S)
            user32.PostMessageW(self.hwnd, WM_KEYUP, vk, lparam_up)

    def send_sequence(self, key: str, count: int, delay: float) -> None:
        for i in range(count):
            self.send_key(key)
            if i < count - 1 and delay > 0:
                time.sleep(delay)


# ── AttachThreadInput backend ───────────────────────────────────────
class AttachInputBackend:
    """Quick focus-attach + SendInput. Works even on hidden games but flashes."""

    def __init__(self, hwnd: int = 0, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label
        self.port = label or f"hwnd 0x{hwnd:X}"
        self.baud = 0
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.hwnd != 0 and is_window_alive(self.hwnd)

    def auto_detect(self) -> Optional[str]:
        return None

    def connect(self, _port: str = "") -> bool:
        return self.connected

    def close(self) -> None:
        pass

    def set_target(self, hwnd: int, label: str = "") -> None:
        self.hwnd = hwnd
        self.label = label
        self.port = label or f"hwnd 0x{hwnd:X}"

    def send_key(self, key: str) -> None:
        if not self.connected:
            logger.warning(f"⚠️ 게임창 미선택 — 키 {key!r} 무시됨")
            return
        vk = _vk_for(key)
        if not vk:
            logger.warning(f"⚠️ 알 수 없는 키: {key!r}")
            return

        target_pid = wintypes.DWORD()
        target_tid = user32.GetWindowThreadProcessId(self.hwnd, ctypes.byref(target_pid))
        current_tid = kernel32.GetCurrentThreadId()
        prev_focus = user32.GetForegroundWindow()

        with self._lock:
            attached = bool(user32.AttachThreadInput(current_tid, target_tid, True))
            try:
                user32.BringWindowToTop(self.hwnd)
                user32.SetForegroundWindow(self.hwnd)
                user32.SetFocus(self.hwnd)
                # Use keybd_event (simpler than INPUT struct)
                user32.keybd_event(vk, 0, 0, 0)
                time.sleep(0.03)
                user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            finally:
                if attached:
                    user32.AttachThreadInput(current_tid, target_tid, False)
                if prev_focus and prev_focus != self.hwnd:
                    user32.SetForegroundWindow(prev_focus)

    def send_sequence(self, key: str, count: int, delay: float) -> None:
        for i in range(count):
            self.send_key(key)
            if i < count - 1 and delay > 0:
                time.sleep(delay)


__all__ = ["PostMessageBackend", "AttachInputBackend"]
