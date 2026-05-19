"""Centralized logging — file rotation + UI signal bridge."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from loguru import logger


def _resolve_log_dir() -> Path:
    """Pick a writable log directory.

    Mirrors :func:`quickcast.config.quickcast_data_dir` so logs always
    sit next to ``userdata.json``. Both frozen and dev share the same
    LOCALAPPDATA location — splitting them silently caused two parallel
    userdata files to drift apart (see config.py docstring). Import
    lazily to avoid a logger ↔ config cycle at module load.
    """
    try:
        from quickcast.config import quickcast_data_dir
        return quickcast_data_dir() / "logs"
    except Exception:
        # Fallback used only during very-early bootstrap errors where
        # config isn't importable yet.
        if getattr(sys, "frozen", False):
            appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            if appdata:
                return Path(appdata) / "QuickCast" / "logs"
        return Path(__file__).resolve().parent.parent / "data" / "logs"


_LOG_DIR = _resolve_log_dir()


def _safe_console_write(msg: str) -> None:
    """Write to stdout without crashing on cp949 (Windows console + emojis)."""
    try:
        sys.stdout.write(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
        sys.stdout.write(msg.encode(enc, errors="replace").decode(enc, errors="replace"))
    try:
        sys.stdout.flush()
    except Exception:
        pass


def setup(log_dir: Path | None = None) -> None:
    """Configure loguru with daily rotating file + console."""
    log_dir = log_dir or _LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # Try to switch stdout to UTF-8 on Windows (PEP 528). Best-effort.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    logger.remove()
    logger.add(
        log_dir / "quickcast_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="1 day",
        encoding="utf-8",
        format="{time:HH:mm:ss.SSS} | {level: <7} | {message}",
        level="DEBUG",
    )
    logger.add(
        _safe_console_write,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
        level="INFO",
        colorize=True,
    )


def add_ui_sink(callback: Callable[[str, str], None]) -> int:
    """Attach a UI callback. Returns sink id for later removal."""
    return logger.add(
        lambda msg: callback(msg.record["level"].name, msg.record["message"]),
        level="INFO",
        format="{message}",
    )


__all__ = ["logger", "setup", "add_ui_sink"]
