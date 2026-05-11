"""On-disk store for learned digit/glyph templates used by core/ocr.py.

Templates are stored as individual PNG files under
``%LOCALAPPDATA%\\QuickCast\\digits\\`` (or the dev `data/` folder in
non-frozen runs). One file per glyph (``0.png`` .. ``9.png``,
``slash.png``). Keeping them as PNGs — rather than JSON-encoded numpy
blobs — lets the user inspect / hand-edit / replace them with a paint
tool if a template ends up wrong.

The store is intentionally tiny: load() returns a ``dict[str, np.ndarray]``
ready to feed straight into ``ocr.recognise()``; save() writes whatever
you give it. Missing files are silently skipped so partial learning
state (e.g. only "0".."5" trained so far) still works.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# Filesystem-safe label → filename mapping. '/' would be illegal so we
# spell it out; other glyphs are their own name.
_FILE_NAME_OVERRIDES = {"/": "slash"}


def _label_to_filename(label: str) -> str:
    return _FILE_NAME_OVERRIDES.get(label, label) + ".png"


def _filename_to_label(name: str) -> Optional[str]:
    stem = Path(name).stem
    for label, fn in _FILE_NAME_OVERRIDES.items():
        if stem == fn:
            return label
    if len(stem) == 1 and stem.isdigit():
        return stem
    return None


def digits_dir() -> Path:
    """Return the writable directory for learned digit PNGs.

    PyInstaller-frozen builds keep state under %LOCALAPPDATA% so the
    onefile temp extraction doesn't wipe learning between runs. Dev
    builds stash next to the bundled targets/.
    """
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "QuickCast" / "digits"
    return Path(__file__).resolve().parent.parent / "data" / "digits"


def load_templates(path: Optional[Path] = None) -> dict[str, np.ndarray]:
    """Read every glyph PNG into a dict ready for ocr.recognise().

    Each value is a uint8 (H, W) mask — same form the learner produces.
    Returns an empty dict when the directory is missing / empty so the
    OCR path gracefully no-ops until the user trains.
    """
    p = path or digits_dir()
    if not p.exists():
        return {}
    out: dict[str, np.ndarray] = {}
    for entry in p.iterdir():
        if entry.suffix.lower() != ".png":
            continue
        label = _filename_to_label(entry.name)
        if label is None:
            continue
        # Read as numpy fromfile so non-ASCII path roots (Korean
        # usernames) work. cv2.imread on Windows fails on those.
        try:
            data = np.fromfile(str(entry), dtype=np.uint8)
        except OSError:
            continue
        img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            continue
        out[label] = img
    return out


def save_templates(templates: dict[str, np.ndarray],
                    path: Optional[Path] = None) -> Path:
    """Persist every entry in `templates` to its labelled PNG.

    Existing files are overwritten. Empty / oversize masks are
    silently skipped — the learner is expected to pre-filter, but we
    don't want a single bad glyph to nuke a successful learning pass.
    """
    p = path or digits_dir()
    p.mkdir(parents=True, exist_ok=True)
    for label, mask in templates.items():
        if mask is None or mask.size == 0:
            continue
        fn = p / _label_to_filename(label)
        ok, buf = cv2.imencode(".png", mask)
        if not ok:
            continue
        # Use tofile to write unicode paths reliably on Windows.
        buf.tofile(str(fn))
    return p


def clear_templates(path: Optional[Path] = None) -> int:
    """Delete all stored glyph PNGs. Returns number of files removed."""
    p = path or digits_dir()
    if not p.exists():
        return 0
    n = 0
    for entry in p.iterdir():
        if entry.suffix.lower() != ".png":
            continue
        if _filename_to_label(entry.name) is None:
            continue
        try:
            entry.unlink()
            n += 1
        except OSError:
            pass
    return n


__all__ = ["digits_dir", "load_templates", "save_templates", "clear_templates"]
