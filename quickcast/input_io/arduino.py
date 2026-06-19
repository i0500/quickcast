"""Arduino serial keystroke backend.

Wire protocol matches the original browser macro 1:1 so the existing
Arduino sketch needs no changes:
  - 9600 baud, 8N1
  - One ASCII character per key, terminated with '\\n'
  - 10 ms post-write settle delay
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

import serial
from serial.tools import list_ports

from quickcast.utils.logger import logger


class ArduinoBackend:
    """Serial backend with a worker thread and reconnection.

    The work queue exists because multiple slots can request actions in
    the same control-loop tick; processing them sequentially in a
    dedicated thread mirrors the JS `keyInputQueue` and keeps key order
    deterministic.
    """

    POST_WRITE_SETTLE = 0.010   # seconds (matches JS 10ms wait)

    def __init__(self, port: str = "", baud: int = 9600) -> None:
        self.port = port
        self.baud = baud

        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._queue: queue.Queue[Optional[tuple[str, float]]] = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ───────── connection ─────────
    @property
    def connected(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    def auto_detect(self) -> Optional[str]:
        """Pick the first plausible Arduino USB device.

        Tries hard to match: Arduino native, common clones (CH340, CH341,
        WCH, CP210x, FTDI, FT232/FT231), and falls back to any generic
        "USB Serial" port. Logs every visible COM port so the user can
        see why a board isn't being picked.
        """
        ports = list(list_ports.comports())
        logger.debug(f"Arduino auto_detect — scanning {len(ports)} COM ports")
        for p in ports:
            vid_s = f"0x{p.vid:04X}" if p.vid else "—"
            pid_s = f"0x{p.pid:04X}" if p.pid else "—"
            logger.debug(
                f"  {p.device}: desc='{p.description}', mfr='{p.manufacturer}', "
                f"vid={vid_s}, pid={pid_s}"
            )

        # Tier 1: explicit Arduino / common clone vendors.
        strong_kws = (
            "arduino", "ch340", "ch341", "ch9102", "wch.cn", "wch ",
            "cp210", "ft232", "ft231", "ftdi",
        )
        # Common Arduino-related VID:PIDs (subset).
        arduino_vids = {0x2341, 0x2A03, 0x1A86, 0x10C4, 0x0403, 0x239A}

        for p in ports:
            desc = (p.description or "").lower()
            mfr = (p.manufacturer or "").lower()
            if any(k in desc for k in strong_kws) or any(k in mfr for k in strong_kws):
                logger.success(f"🔌 Arduino 감지: {p.device}")
                return p.device
            if p.vid in arduino_vids:
                logger.success(f"🔌 Arduino 감지: {p.device}")
                return p.device

        # Tier 2: any "USB Serial Device" fallback (generic Windows driver).
        for p in ports:
            desc = (p.description or "").lower()
            if "usb serial" in desc or "usb-serial" in desc or "serial port" in desc:
                logger.warning(f"🔌 USB Serial 감지: {p.device} (Arduino인지 확인 필요)")
                return p.device

        logger.debug("Arduino auto_detect failed — no plausible COM port found")
        return None

    def connect(self, port: str = "") -> bool:
        # 저장/지정 포트를 먼저 시도하고, 열기에 실패하면 자동탐지 포트로 폴백한다.
        # USB 재꽂음·드라이버 재설치 등으로 COM 번호가 바뀌면 예전 포트가 안 열리는데,
        # 그때 새 포트를 자동으로 찾아 연결한다. (기존엔 한 포트만 골라 시도하고
        # 그게 실패하면 그대로 포기 — 자동탐지 폴백이 없었다.)
        candidates = []
        first = port or self.port
        if first:
            candidates.append(first)
        auto = self.auto_detect()
        if auto and auto not in candidates:
            candidates.append(auto)
        if not candidates:
            logger.warning("⚠️ Arduino 포트 미지정 + 자동 감지 실패")
            return False
        for target in candidates:
            try:
                with self._lock:
                    if self._serial and self._serial.is_open:
                        self._serial.close()
                    self._serial = serial.Serial(target, self.baud, timeout=0.5)
                    self.port = target
                self._ensure_worker()
                logger.success(f"🔌 Arduino 연결됨: {target} @ {self.baud}bps")
                return True
            except serial.SerialException as e:
                logger.error(f"❌ Arduino 연결 실패: {target}: {e}")
        return False

    def reconnect(self) -> bool:
        return self.connect(self.port)

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)  # wake worker
        if self._worker:
            self._worker.join(timeout=1.0)
            self._worker = None
        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
            self._serial = None

    # ───────── public API ─────────
    def send_key(self, key: str) -> None:
        """Queue a single key press. Returns immediately."""
        if not key:
            return
        self._queue.put((key, 0.0))

    def send_sequence(self, key: str, count: int, delay: float) -> None:
        """Queue a count-times key burst with `delay` seconds between presses.

        The trailing item gets delay=0 to avoid an unnecessary final wait.
        """
        for i in range(count):
            self._queue.put((key, delay if i < count - 1 else 0.0))

    # ───────── worker ─────────
    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, name="ArduinoWriter", daemon=True
        )
        self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            key, delay = item
            self._write(key)
            if delay > 0:
                time.sleep(delay)

    def _write(self, key: str) -> None:
        payload = (key + "\n").encode("ascii", errors="ignore")
        with self._lock:
            ser = self._serial
        if not ser or not ser.is_open:
            logger.warning(f"⚠️ Arduino 미연결 — 키 {key!r} 무시됨")
            return
        try:
            ser.write(payload)
            ser.flush()
            time.sleep(self.POST_WRITE_SETTLE)
        except serial.SerialException as e:
            logger.error(f"❌ Arduino 쓰기 실패: {e}")
            with self._lock:
                if self._serial:
                    try:
                        self._serial.close()
                    finally:
                        self._serial = None


__all__ = ["ArduinoBackend"]
