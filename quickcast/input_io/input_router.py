"""Input backend abstraction.

Lets us swap Arduino HID for vJoy/Interception/etc later without
touching slot or controller code.
"""
from __future__ import annotations

from typing import Protocol


class InputBackend(Protocol):
    """Anything that can deliver a single keystroke."""
    @property
    def connected(self) -> bool: ...
    def send_key(self, key: str) -> None: ...
    def close(self) -> None: ...


class NullBackend:
    """Used when no hardware is connected — logs only."""
    @property
    def connected(self) -> bool:
        return False

    def send_key(self, key: str) -> None:
        from quickcast.utils.logger import logger
        logger.debug(f"[NullBackend] dropped key: {key!r}")

    def close(self) -> None:
        pass


__all__ = ["InputBackend", "NullBackend"]
