"""Telegram bot notifications (text + screenshot).

Uses httpx.Client (sync) running in a small worker thread so the
control loop never blocks on HTTP.
"""
from __future__ import annotations

import io
import queue
import threading
from dataclasses import dataclass
from typing import Optional, Union

import cv2
import httpx
import numpy as np

from quickcast.utils.logger import logger


@dataclass
class _Job:
    text: Optional[str] = None
    photo: Optional[bytes] = None
    caption: Optional[str] = None


class TelegramNotifier:
    """Fire-and-forget Telegram client."""

    BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self.token = token
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=10.0)
        self._queue: queue.Queue[Optional[_Job]] = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def configure(self, token: str, chat_id: str = "") -> None:
        self.token = token
        if chat_id:
            self.chat_id = chat_id

    def connect(self) -> bool:
        """Verify token and resolve chat_id from getUpdates if missing."""
        if not self.token:
            return False
        try:
            if not self.chat_id:
                r = self._client.get(self.BASE.format(token=self.token, method="getUpdates"))
                r.raise_for_status()
                results = r.json().get("result", [])
                if not results:
                    logger.error("Telegram: no chat history yet — send /start to your bot first")
                    return False
                self.chat_id = str(results[-1]["message"]["chat"]["id"])
            self._connected = True
            self._ensure_worker()
            self.send_text("✅ 텔레그램 연결 완료")
            logger.success(f"Telegram connected (chat_id={self.chat_id})")
            return True
        except Exception as e:
            logger.error(f"Telegram connect failed: {e}")
            self._connected = False
            return False

    # ───────── public API ─────────
    def send_text(self, text: str) -> None:
        if self._connected:
            self._queue.put(_Job(text=text))

    def send_photo(self, image: Union[np.ndarray, bytes], caption: str = "") -> None:
        if not self._connected:
            return
        if isinstance(image, np.ndarray):
            ok, buf = cv2.imencode(".png", image)
            if not ok:
                logger.warning("Telegram: failed to encode screenshot")
                return
            image = buf.tobytes()
        self._queue.put(_Job(photo=image, caption=caption))

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        self._client.close()

    # ───────── worker ─────────
    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._run, name="TelegramWorker", daemon=True
        )
        self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._queue.get()
            if job is None:
                break
            try:
                if job.photo is not None:
                    self._client.post(
                        self.BASE.format(token=self.token, method="sendPhoto"),
                        data={"chat_id": self.chat_id, "caption": job.caption or ""},
                        files={"photo": ("frame.png", io.BytesIO(job.photo), "image/png")},
                    )
                elif job.text is not None:
                    self._client.post(
                        self.BASE.format(token=self.token, method="sendMessage"),
                        data={"chat_id": self.chat_id, "text": job.text},
                    )
            except Exception as e:
                logger.warning(f"Telegram send failed: {e}")


__all__ = ["TelegramNotifier"]
