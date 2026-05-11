"""Application font registration.

Tries to load bundled Pretendard Variable + JetBrains Mono from
`quickcast/data/fonts/`. If files are missing, silently falls back to
the system font stack defined in `tokens.Typography`.

Bundling instructions (one-time, manual):
    https://github.com/orioncactus/pretendard/releases
        → PretendardVariable.ttf  → quickcast/data/fonts/
    https://github.com/JetBrains/JetBrainsMono/releases
        → JetBrainsMono-Regular.ttf, -Bold.ttf  → quickcast/data/fonts/
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase

from quickcast.utils.logger import logger

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "fonts"
_BUNDLED = (
    "PretendardVariable.ttf",
    "Pretendard-Regular.otf",
    "Pretendard-Medium.otf",
    "Pretendard-SemiBold.otf",
    "Pretendard-Bold.otf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Medium.ttf",
    "JetBrainsMono-Bold.ttf",
)


def register() -> list[str]:
    """Load every bundled font; return the list of family names registered."""
    families: list[str] = []
    for name in _BUNDLED:
        path = FONT_DIR / name
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            logger.warning(f"font load failed: {path.name}")
            continue
        for fam in QFontDatabase.applicationFontFamilies(font_id):
            if fam not in families:
                families.append(fam)
    if families:
        logger.info(f"fonts loaded: {', '.join(families)}")
    else:
        logger.info("no bundled fonts found — using system stack (Pretendard / Malgun Gothic)")
    return families


__all__ = ["register", "FONT_DIR"]
