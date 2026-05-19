"""
QuickCast UI 자동 캡처
- venv python으로 quickcast 모듈을 띄워 UAC 우회
- 띄운 프로세스의 PID 로 메인 창을 정확히 식별 (제목 매칭은 신뢰 불가)
- PrintWindow API 로 다른 창에 가려져도 깨끗하게 캡처
"""
from __future__ import annotations
from pathlib import Path
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time

import win32gui  # type: ignore
import win32con  # type: ignore
import win32ui   # type: ignore
import win32process  # type: ignore
from PIL import Image


PROJECT_ROOT = Path(r"F:/린w")
PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
OUT = PROJECT_ROOT / "dist" / "captures"
OUT.mkdir(parents=True, exist_ok=True)


# ────────── 창 탐색 ──────────
def bring_to_front(hwnd: int) -> None:
    """SetForegroundWindow 의 Windows 포커스 정책을 우회하기 위해
    AttachThreadInput 으로 현재 foreground 스레드와 입력 큐를 일시 결합한다."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        fg = ctypes.windll.user32.GetForegroundWindow()
        if fg == hwnd:
            return
        fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg, None)
        my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = bool(ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, True))
        try:
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(fg_tid, my_tid, False)
    except Exception:
        pass


def list_visible_windows_by_pid(pid: int) -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """주어진 PID 소유의 보이는 top-level 창들. (hwnd, title, rect)"""
    out: list[tuple[int, str, tuple[int, int, int, int]]] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        if wpid != pid:
            return
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if (r - l) < 200 or (b - t) < 200:
            return
        title = win32gui.GetWindowText(hwnd)
        out.append((hwnd, title, (l, t, r, b)))

    win32gui.EnumWindows(cb, None)
    return out


def _safe_print(s: str) -> None:
    """cp949 콘솔에서 깨질 문자를 ?로 치환해서 안전 출력."""
    try:
        print(s)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "cp949"
        print(s.encode(enc, "replace").decode(enc, "replace"))


def dump_all_quickcast_candidates() -> None:
    """진단용: 모든 큰 보이는 창 목록 + python/quickcast 프로세스의 PID."""
    import psutil  # type: ignore

    py_pids: set[int] = set()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            n = (p.info["name"] or "").lower()
            if "python" in n or "quickcast" in n:
                py_pids.add(p.info["pid"])
        except Exception:
            pass

    _safe_print(f"  python/quickcast PIDs: {sorted(py_pids)}")

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:
            return
        w, h = r - l, b - t
        if w < 400 or h < 300:
            return
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        title = win32gui.GetWindowText(hwnd)
        mark = "★" if wpid in py_pids else "  "
        _safe_print(f"  {mark} hwnd=0x{hwnd:X}  pid={wpid}  {w}x{h}  title={title!r}")

    win32gui.EnumWindows(cb, None)


def terminate_proc_tree(root_pid: int) -> None:
    import psutil  # type: ignore
    try:
        proc = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        return
    for child in proc.children(recursive=True):
        try:
            child.terminate()
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        pass
    gone, alive = psutil.wait_procs([proc], timeout=5)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass


def collect_pid_tree(root_pid: int) -> set[int]:
    """root_pid 와 그 모든 자손 프로세스 PID 수집."""
    import psutil  # type: ignore
    try:
        proc = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        return {root_pid}
    pids = {root_pid}
    for child in proc.children(recursive=True):
        pids.add(child.pid)
    return pids


def pick_main_window(root_pid: int, *, timeout_s: float = 45.0,
                     min_w: int = 900, min_h: int = 600) -> int:
    """root_pid 트리(자손 포함) 에서 메인 창이 등장할 때까지 polling.
    - 처음 큰 창(≥ min_w × min_h) 이 나타나면 그 hwnd 반환.
    - splash screen 같은 작은 창은 무시.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pids = collect_pid_tree(root_pid)
        candidates: list[tuple[int, str, tuple[int, int, int, int]]] = []
        for pid in pids:
            for hwnd, title, rect in list_visible_windows_by_pid(pid):
                l, t, r, b = rect
                if (r - l) >= min_w and (b - t) >= min_h:
                    candidates.append((hwnd, title, rect))
        if candidates:
            def area(rect):
                l, t, r, b = rect
                return (r - l) * (b - t)
            candidates.sort(key=lambda w: area(w[2]), reverse=True)
            hwnd, title, rect = candidates[0]
            l, t, r, b = rect
            _safe_print(f"  main window: hwnd=0x{hwnd:X}  {r-l}x{b-t}  title={title!r}")
            # 렌더링 안정화 시간 추가
            time.sleep(2.5)
            return hwnd
        time.sleep(0.5)
    print(f"  root_pid={root_pid} 트리의 메인 창(≥{min_w}x{min_h})을 찾지 못했습니다.")
    return 0


# ────────── 캡처 (PrintWindow) ──────────
PW_RENDERFULLCONTENT = 0x00000002


def capture_hwnd_printwindow(hwnd: int, out_path: Path) -> bool:
    """PrintWindow 로 hwnd 의 클라이언트+논클라이언트 영역을 직접 비트맵 캡처."""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w < 50 or h < 50:
            print(f"  창이 너무 작음 rect=({l},{t},{r},{b})")
            return False

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)

        ok = ctypes.windll.user32.PrintWindow(
            hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT
        )
        if not ok:
            # fallback: 플래그 없이 한 번 더
            ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]),
            data, "raw", "BGRX", 0, 1,
        )

        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        img.save(str(out_path))
        print(f"  saved: {out_path.name}  ({img.size[0]}x{img.size[1]})  ok={ok}")
        return True
    except Exception as exc:
        print(f"  실패: {exc}")
        return False


# ────────── 프리뷰 실행 ──────────
def launch_quickcast() -> subprocess.Popen:
    env = os.environ.copy()
    env["QUICKCAST_FORCE_TUTORIAL"] = "0"
    return subprocess.Popen(
        [str(PY), "-m", "quickcast"],
        cwd=str(PROJECT_ROOT),
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )


# ────────── 사이드바 자동 클릭 ──────────
import pyautogui  # type: ignore

# (id, 저장 파일명, 한글 라벨) — app_window.py SECTIONS 순서와 동일해야 함
SIDEBAR_ORDER = [
    ("dashboard", "01_dashboard.png", "대시보드"),
    ("capture",   "02_capture.png",   "캡처"),
    ("combat",    "03_combat.png",    "전투 대응"),
    ("slots",     "04_slots.png",     "스킬 슬롯"),
    ("alerts",    "05_alerts.png",    "알람"),
    ("settings",  "06_settings.png",  "설정"),
]

# 사이드바 아이콘 좌표 — 캡처 (1280×820 PrintWindow) 에서 직접 측정한 값.
# (윈도우 좌상단 기준 픽셀 오프셋)
SB_X = 28               # 사이드바 가로 중앙
SB_FIRST_Y = 75         # 첫 아이콘 중심 y
SB_STEP_Y = 46          # 아이콘 간격 (ITEM_H 44 + spacing 2)


def sidebar_screen_xy(hwnd: int, idx: int) -> tuple[int, int]:
    """사이드바 idx 번째 아이콘 중심의 화면 좌표 (윈도우 좌상단 기준 오프셋)."""
    l, t, _, _ = win32gui.GetWindowRect(hwnd)
    return l + SB_X, t + SB_FIRST_Y + idx * SB_STEP_Y


def click_sidebar(hwnd: int, idx: int) -> None:
    x, y = sidebar_screen_xy(hwnd, idx)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.15)
    pyautogui.click(x, y)


# Win32 가상 키 코드 — 포커스에 의존하지 않는 PostMessage 입력에 사용.
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


def send_ctrl_digit(hwnd: int, digit: int) -> None:
    """Ctrl + 숫자(1~9) 를 PostMessage 로 직접 윈도우에 전달.
    포커스/foreground 정책에 영향받지 않는 안정적 전환 방식이다."""
    if not (1 <= digit <= 9):
        return
    vk_digit = 0x30 + digit          # 0x31='1', 0x32='2' …
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_CONTROL, 0)
    time.sleep(0.04)
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, vk_digit, 0)
    time.sleep(0.04)
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, vk_digit, 0)
    time.sleep(0.04)
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_CONTROL, 0)


def send_escape(hwnd: int) -> None:
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYDOWN, VK_ESCAPE, 0)
    time.sleep(0.04)
    ctypes.windll.user32.PostMessageW(hwnd, WM_KEYUP, VK_ESCAPE, 0)


def dismiss_tutorial(hwnd: int) -> None:
    """튜토리얼/팝업이 떠 있으면 ESC 로 닫음. PostMessage 방식."""
    for _ in range(3):
        send_escape(hwnd)
        time.sleep(0.2)


# ────────── 메인 ──────────
def main() -> int:
    if not PY.exists():
        print(f"venv python 없음: {PY}")
        return 2

    print("QuickCast 프리뷰 실행 중…")
    proc = launch_quickcast()
    print(f"  pid = {proc.pid}")

    hwnd = pick_main_window(proc.pid)
    if hwnd == 0:
        print("--- 진단: 모든 후보 창 ---")
        dump_all_quickcast_candidates()
        proc.terminate()
        return 3

    # 포커스를 강제로 확보 (AttachThreadInput) 후 시작
    bring_to_front(hwnd)
    time.sleep(0.4)

    # 튜토리얼 가림 방지 — Qt ApplicationShortcut + SendInput ESC
    pyautogui.press("escape")
    time.sleep(0.2)
    pyautogui.press("escape")
    time.sleep(0.3)

    # 각 섹션을 Ctrl+1..6 으로 전환하며 캡처
    for idx, (sid, fname, label) in enumerate(SIDEBAR_ORDER):
        bring_to_front(hwnd)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", str(idx + 1))
        time.sleep(0.8)               # 섹션 전환 + 렌더링 안정화
        ok = capture_hwnd_printwindow(hwnd, OUT / fname)
        _safe_print(f"  [{idx+1}/{len(SIDEBAR_ORDER)}] {label} → {fname}  ok={ok}")

    # ─── 추가 캡처 ───────────────────────────────────────────────
    # 설정 탭에서 좌측 서브메뉴 ‘입력 방식’ 클릭 후 캡처
    # 좌표는 캡처(06_settings.png) 에서 직접 측정한 윈도우 기준 오프셋.
    bring_to_front(hwnd)
    time.sleep(0.4)
    l, t, _, _ = win32gui.GetWindowRect(hwnd)
    cx, cy = l + 165, t + 184
    _safe_print(f"  click for input-backend: window=({l},{t}) → screen=({cx},{cy})")
    pyautogui.click(cx, cy)
    time.sleep(1.0)
    bring_to_front(hwnd)
    time.sleep(0.3)
    capture_hwnd_printwindow(hwnd, OUT / "07_settings_input.png")
    _safe_print("  [extra] 설정 → 입력 방식 → 07_settings_input.png")

    # 종료 전에 대시보드에서 마스터·플로팅 토글 영역만 crop 으로 잘라 저장
    crop_toggles(OUT / "01_dashboard.png", OUT / "08_toggles.png")

    time.sleep(0.5)
    terminate_proc_tree(proc.pid)

    print(f"done.  out: {OUT}")
    return 0


def crop_toggles(src: Path, dst: Path) -> None:
    """대시보드 풀 캡처에서 우상단의 Floating + Master 토글 영역만 잘라낸다."""
    try:
        from PIL import Image
        if not src.exists():
            print(f"  toggles crop skip: {src} 없음")
            return
        img = Image.open(src)
        # (left, top, right, bottom) — 1280x820 PrintWindow 기준 우상단 영역
        crop = img.crop((850, 0, 1185, 48))
        crop.save(str(dst))
        print(f"  saved (crop): {dst.name}  ({crop.size[0]}x{crop.size[1]})")
    except Exception as exc:
        print(f"  toggles crop 실패: {exc}")


if __name__ == "__main__":
    sys.exit(main())
