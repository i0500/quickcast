"""On-disk store for learned digit / glyph templates used by core/ocr.py.

Multi-instance store
--------------------
Each glyph gets its own subdirectory under
``%LOCALAPPDATA%\\QuickCast\\digits\\`` (or the dev ``data/`` folder in
non-frozen runs):

    digits/0/0.png  digits/0/1.png  digits/0/2.png  ...
    digits/1/0.png  digits/1/1.png  ...
    digits/slash/0.png  digits/slash/1.png  ...

Storing many instances per glyph improves OCR robustness: when the user
trains "1234/5678" once and later trains "9999/9999", both passes
contribute distinct "9" / "/" appearances. The matcher picks the
highest-scoring instance per glyph at inference time, so a single bad
template can no longer drag the recognition rate down.

Legacy flat layout (one PNG per glyph at the directory root) is still
accepted on load — older installs migrate seamlessly into the new
multi-instance dict on first training.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# Filesystem-safe label → directory / filename mapping. '/' is illegal
# in filenames so we spell it out; digit labels are their own name.
_DIR_NAME_OVERRIDES = {"/": "slash"}
_FILE_NAME_OVERRIDES = _DIR_NAME_OVERRIDES   # alias for legacy paths


def _label_to_dirname(label: str) -> str:
    return _DIR_NAME_OVERRIDES.get(label, label)


def _label_to_filename(label: str) -> str:
    """Legacy flat-layout filename for backward-compat loading."""
    return _FILE_NAME_OVERRIDES.get(label, label) + ".png"


def _filename_to_label(name: str) -> Optional[str]:
    stem = Path(name).stem
    for label, dn in _DIR_NAME_OVERRIDES.items():
        if stem == dn:
            return label
    if len(stem) == 1 and stem.isdigit():
        return stem
    return None


def _dirname_to_label(name: str) -> Optional[str]:
    for label, dn in _DIR_NAME_OVERRIDES.items():
        if name == dn:
            return label
    if len(name) == 1 and name.isdigit():
        return name
    return None


def digits_dir() -> Path:
    """Return the writable directory for learned digit PNGs."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "QuickCast" / "digits"
    return Path(__file__).resolve().parent.parent / "data" / "digits"


def _read_png(path: Path) -> Optional[np.ndarray]:
    """Read a PNG into a grayscale uint8 mask. None on any failure."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return None
    return img


def load_templates(path: Optional[Path] = None) -> dict[str, list[np.ndarray]]:
    """Read every learned glyph instance into ``{label: [mask, ...]}``.

    Supports both layouts:
      - new: ``digits/<label>/<idx>.png`` (preferred, multi-instance)
      - legacy: ``digits/<label>.png`` (one mask per glyph)

    Legacy entries are folded in as a single-element list. Empty dict
    when nothing is on disk — the OCR path no-ops gracefully.
    """
    p = path or digits_dir()
    if not p.exists():
        return {}
    out: dict[str, list[np.ndarray]] = {}
    for entry in p.iterdir():
        if entry.is_dir():
            label = _dirname_to_label(entry.name)
            if label is None:
                continue
            instances: list[np.ndarray] = []
            for png in sorted(entry.iterdir()):
                if png.suffix.lower() != ".png":
                    continue
                mask = _read_png(png)
                if mask is not None:
                    instances.append(mask)
            if instances:
                out[label] = instances
        elif entry.suffix.lower() == ".png":
            label = _filename_to_label(entry.name)
            if label is None:
                continue
            mask = _read_png(entry)
            if mask is None:
                continue
            out.setdefault(label, []).append(mask)
    return out


def _write_png(mask: np.ndarray, path: Path) -> bool:
    if mask is None or mask.size == 0:
        return False
    ok, buf = cv2.imencode(".png", mask)
    if not ok:
        return False
    buf.tofile(str(path))
    return True


def save_templates(templates: dict[str, list[np.ndarray]],
                    path: Optional[Path] = None) -> Path:
    """Persist every label's full instance list to disk.

    Existing per-label directories are wiped and rewritten with the new
    instance set, so the caller is expected to *merge* old + new before
    calling save_templates() (see OcrCalibrationDialog._on_save).
    Legacy root-level *.png files for any rewritten label are removed
    so the new directory layout becomes the single source of truth.
    """
    p = path or digits_dir()
    p.mkdir(parents=True, exist_ok=True)
    for label, instances in templates.items():
        if not instances:
            continue
        dn = p / _label_to_dirname(label)
        dn.mkdir(parents=True, exist_ok=True)
        # Wipe stale instance files (only the ones we recognise — leave
        # unrelated user files alone).
        for old in list(dn.iterdir()):
            if old.suffix.lower() == ".png":
                try:
                    old.unlink()
                except OSError:
                    pass
        # Write fresh instance set, zero-indexed.
        for i, mask in enumerate(instances):
            if mask is None or mask.size == 0:
                continue
            _write_png(mask, dn / f"{i}.png")
        # Drop the legacy single-file form for this label, now superseded.
        legacy = p / _label_to_filename(label)
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
    return p


def clear_label(label: str, path: Optional[Path] = None) -> int:
    """Delete every saved instance of one glyph. Returns files removed.

    Removes both the new multi-instance directory (digits/<label>/) and
    the legacy flat file (digits/<label>.png) if present.
    """
    p = path or digits_dir()
    n = 0
    if not p.exists():
        return 0
    # Multi-instance directory
    dn = p / _label_to_dirname(label)
    if dn.exists() and dn.is_dir():
        for png in list(dn.iterdir()):
            if png.suffix.lower() == ".png":
                try:
                    png.unlink(); n += 1
                except OSError:
                    pass
        try:
            dn.rmdir()
        except OSError:
            pass
    # Legacy flat file
    legacy = p / _label_to_filename(label)
    if legacy.exists():
        try:
            legacy.unlink(); n += 1
        except OSError:
            pass
    return n


def clear_templates(path: Optional[Path] = None) -> int:
    """Delete every learned mask (both layouts). Returns files removed."""
    p = path or digits_dir()
    if not p.exists():
        return 0
    n = 0
    for entry in list(p.iterdir()):
        if entry.is_dir():
            label = _dirname_to_label(entry.name)
            if label is None:
                continue
            for png in list(entry.iterdir()):
                if png.suffix.lower() == ".png":
                    try:
                        png.unlink(); n += 1
                    except OSError:
                        pass
            try:
                entry.rmdir()
            except OSError:
                pass
        elif entry.suffix.lower() == ".png":
            if _filename_to_label(entry.name) is None:
                continue
            try:
                entry.unlink(); n += 1
            except OSError:
                pass
    return n


def instance_counts(path: Optional[Path] = None) -> dict[str, int]:
    """Per-label count of stored instances. Convenient for UI display."""
    return {lab: len(insts) for lab, insts in load_templates(path).items()}


__all__ = [
    "digits_dir", "load_templates", "save_templates",
    "clear_label", "clear_templates", "instance_counts",
]
