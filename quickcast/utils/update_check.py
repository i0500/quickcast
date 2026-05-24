"""GitHub release update checker.

Polls the project's "latest" release via the public GitHub REST API and
emits a Qt signal when a newer version than the running build is
available. Designed to be silent on failure (offline / rate-limited /
GitHub down) so the user never sees a network error popup just because
they happen to be offline at start time.

Usage::

    checker = UpdateChecker(parent)
    checker.update_available.connect(on_update_found)
    checker.start_periodic()

``update_available`` carries ``(current_version, latest_tag, html_url)``
so the UI can render a "v1.0.3 → v1.0.4" label and open the release page
on click. ``check_now()`` is also exposed so a "Check for updates" menu
item can trigger an immediate request.
"""
from __future__ import annotations

import json
import sys
from typing import Optional, Tuple

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from quickcast import __version__ as CURRENT_VERSION
from quickcast.utils.logger import logger


GITHUB_REPO = "i0500/quickcast"
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# 6 hours between polls — well under the unauthenticated 60 req/h/IP
# limit, and slow enough that a casual user keeping the app open all day
# only sees ~4 quiet network blips.
_PERIODIC_MS = 6 * 60 * 60 * 1000
# Wait this long before the first check so app startup isn't slowed
# (and so transient DNS hiccups during boot don't waste the first poll).
_INITIAL_DELAY_MS = 5_000


def _parse_version(s: str) -> Optional[Tuple[int, ...]]:
    """Parse "v1.0.3" / "1.0.3" / "1.0.3-beta" into a comparable tuple.

    Returns None when the string doesn't look version-shaped at all —
    the caller treats that as "can't compare, assume no update" so the
    user never sees a bogus update prompt from a malformed tag.
    """
    if not s:
        return None
    s = s.strip()
    if s.lower().startswith("v"):
        s = s[1:]
    # Drop pre-release / build suffix ("1.0.3-beta", "1.0.3+build.5")
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            return None
    if not parts:
        return None
    return tuple(parts)


def is_newer(latest_tag: str, current: str) -> bool:
    """True iff ``latest_tag`` strictly newer than ``current``.

    Both inputs accept "v" prefix. Returns False on parse failure so a
    weird tag never triggers an "update available" prompt.
    """
    a = _parse_version(latest_tag)
    b = _parse_version(current)
    if a is None or b is None:
        return False
    return a > b


class UpdateChecker(QObject):
    """Non-blocking GitHub release poller.

    Lives for the lifetime of the AppWindow that owns it; the parent
    QObject hierarchy keeps the QNetworkAccessManager alive without us
    needing to manage its lifecycle by hand.
    """

    # (current_version, latest_tag, release_html_url)
    update_available = Signal(str, str, str)
    # Emitted on every check result so a "checked just now" indicator can
    # update — payload is (ok, message). Failures pass ok=False so the UI
    # can show "오프라인 / 확인 실패" without us throwing.
    check_finished = Signal(bool, str)

    def __init__(self, parent: Optional[QObject] = None,
                 *, only_in_frozen: bool = True) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        # The `_only_in_frozen` guard lets developers skip the network
        # call entirely while iterating in `python -m quickcast` —
        # otherwise every dev launch nags about being on a "newer"
        # untagged version (or fails because GitHub doesn't know
        # "1.4.0-printwindow"). Set to False to test wiring in dev.
        self._only_in_frozen = bool(only_in_frozen)
        self._timer = QTimer(self)
        self._timer.setInterval(_PERIODIC_MS)
        self._timer.timeout.connect(self.check_now)
        # Latest reply we issued — kept so we can ignore stale callbacks
        # if `check_now()` is invoked again while a prior request is in
        # flight (rare; protects the signal from firing twice).
        self._inflight: Optional[QNetworkReply] = None

    # ── lifecycle ──────────────────────────────────────────────────
    def start_periodic(self) -> None:
        """Schedule the first check after a short delay, then every 6h."""
        if self._only_in_frozen and not getattr(sys, "frozen", False):
            logger.info("update-check: 개발 모드 — 자동 체크 건너뜀")
            return
        QTimer.singleShot(_INITIAL_DELAY_MS, self.check_now)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # ── network ────────────────────────────────────────────────────
    def check_now(self) -> None:
        """Fire a single GET to the GitHub Releases API."""
        if self._only_in_frozen and not getattr(sys, "frozen", False):
            return
        req = QNetworkRequest(QUrl(LATEST_URL))
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        # Identifying UA is good GitHub etiquette and also avoids 403s
        # some proxies return for empty UA requests.
        req.setRawHeader(b"User-Agent", f"QuickCast/{CURRENT_VERSION}".encode())
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                          QNetworkRequest.NoLessSafeRedirectPolicy)
        if self._inflight is not None:
            try:
                self._inflight.abort()
            except Exception:
                pass
        reply = self._nam.get(req)
        self._inflight = reply
        reply.finished.connect(lambda r=reply: self._on_finished(r))

    def _on_finished(self, reply: QNetworkReply) -> None:
        try:
            if reply is not self._inflight:
                return    # superseded by a newer request
            self._inflight = None
            err = reply.error()
            if err != QNetworkReply.NoError:
                msg = reply.errorString()
                logger.info(f"update-check: 실패 — {msg}")
                self.check_finished.emit(False, msg)
                return
            raw = bytes(reply.readAll())
            if not raw:
                self.check_finished.emit(False, "빈 응답")
                return
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception as exc:
                logger.info(f"update-check: JSON 파싱 실패 — {exc}")
                self.check_finished.emit(False, "응답 파싱 실패")
                return
            tag = str(data.get("tag_name") or "")
            url = str(data.get("html_url") or "")
            if not tag:
                self.check_finished.emit(False, "tag_name 누락")
                return
            if is_newer(tag, CURRENT_VERSION):
                logger.info(
                    f"update-check: 새 버전 감지 — 현재 v{CURRENT_VERSION} → {tag}"
                )
                self.check_finished.emit(True, f"새 버전 {tag}")
                self.update_available.emit(CURRENT_VERSION, tag, url)
            else:
                logger.debug(
                    f"update-check: 최신 상태 — 현재 v{CURRENT_VERSION}, 원격 {tag}"
                )
                self.check_finished.emit(True, "최신 버전")
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass


__all__ = ["UpdateChecker", "is_newer", "CURRENT_VERSION"]
