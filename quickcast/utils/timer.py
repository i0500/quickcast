"""Cooldown / timing utilities."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Cooldown:
    """Tracks per-key cooldown using monotonic time (no datetime drift)."""
    _expires: dict[str, float] = field(default_factory=dict)

    def is_ready(self, key: str) -> bool:
        return time.monotonic() >= self._expires.get(key, 0.0)

    def trigger(self, key: str, seconds: float) -> None:
        self._expires[key] = time.monotonic() + seconds

    def remaining(self, key: str) -> float:
        return max(0.0, self._expires.get(key, 0.0) - time.monotonic())

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._expires.clear()
        else:
            self._expires.pop(key, None)


__all__ = ["Cooldown"]
