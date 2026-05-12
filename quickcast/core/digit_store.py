"""On-disk store for learned digit / glyph templates used by core/ocr.py.

Per-domain, multi-instance store
--------------------------------
HP / MP / potion HUD regions render the same digits at slightly
different sizes and on different backgrounds. Mixing their learned
templates in one pool causes cross-pollination misreads (e.g. an
"8" template captured from the MP bar wins a column in the potion
ROI it doesn't belong to). Each domain therefore gets its own
subdirectory:

    digits/hp/0/0.png  digits/hp/0/1.png  ...
    digits/hp/.canonical    (text file: "WxH" target size)
    digits/mp/0/0.png  ...
    digits/potion/0/0.png  ...

Plus an optional **legacy flat pool** at the root for backward
compatibility with installs that trained before this split:

    digits/0/0.png  digits/0.png  ...

``load_templates(domain="hp")`` reads only that domain's pool;
``load_templates(domain=None)`` reads the legacy root pool (the
recognizer falls back to this when a domain hasn't been trained).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


# Filesystem-safe label mapping. '/' is illegal so spell it out.
_DIR_NAME_OVERRIDES = {"/": "slash"}
_FILE_NAME_OVERRIDES = _DIR_NAME_OVERRIDES

DOMAINS: tuple[str, ...] = ("hp", "mp", "potion")
_CANONICAL_FILE = ".canonical"


def _label_to_dirname(label: str) -> str:
    return _DIR_NAME_OVERRIDES.get(label, label)


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


def _dirname_to_label(name: str) -> Optional[str]:
    for label, dn in _DIR_NAME_OVERRIDES.items():
        if name == dn:
            return label
    if len(name) == 1 and name.isdigit():
        return name
    return None


def digits_dir() -> Path:
    """Return the writable root for learned digit PNGs."""
    if getattr(sys, "frozen", False):
        appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "QuickCast" / "digits"
    return Path(__file__).resolve().parent.parent / "data" / "digits"


def domain_dir(domain: Optional[str] = None) -> Path:
    """Subdirectory for one domain ("hp"/"mp"/"potion"), or root for None."""
    if not domain:
        return digits_dir()
    return digits_dir() / domain


def _read_png(path: Path) -> Optional[np.ndarray]:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if img is None or img.size == 0:
        return None
    return img


def load_templates(domain: Optional[str] = None,
                     path: Optional[Path] = None,
                     ) -> dict[str, list[np.ndarray]]:
    """Read every learned glyph instance for one domain into a dict.

    ``domain``: "hp" / "mp" / "potion", or None for the legacy root
    pool. Returns ``{label: [mask, ...]}``. Supports both new
    per-label-subdir layout and legacy flat-file layout.
    """
    p = path or domain_dir(domain)
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
                    domain: Optional[str] = None,
                    path: Optional[Path] = None) -> Path:
    """Persist a domain's full instance set."""
    p = path or domain_dir(domain)
    p.mkdir(parents=True, exist_ok=True)
    for label, instances in templates.items():
        if not instances:
            continue
        dn = p / _label_to_dirname(label)
        dn.mkdir(parents=True, exist_ok=True)
        # Wipe stale instance files first so the layout always reflects
        # the merged set the caller passed in.
        for old in list(dn.iterdir()):
            if old.suffix.lower() == ".png":
                try:
                    old.unlink()
                except OSError:
                    pass
        for i, mask in enumerate(instances):
            if mask is None or mask.size == 0:
                continue
            _write_png(mask, dn / f"{i}.png")
        # Drop the legacy single-file form for this label inside the
        # current domain dir if it lingers.
        legacy = p / _label_to_filename(label)
        if legacy.exists():
            try:
                legacy.unlink()
            except OSError:
                pass
    return p


def clear_label(label: str, domain: Optional[str] = None,
                  path: Optional[Path] = None) -> int:
    """Delete every saved instance of one glyph in one domain."""
    p = path or domain_dir(domain)
    n = 0
    if not p.exists():
        return 0
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
    legacy = p / _label_to_filename(label)
    if legacy.exists():
        try:
            legacy.unlink(); n += 1
        except OSError:
            pass
    return n


def clear_templates(domain: Optional[str] = None,
                     path: Optional[Path] = None) -> int:
    """Delete every learned mask in one domain (or the legacy root)."""
    p = path or domain_dir(domain)
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


def instance_counts(domain: Optional[str] = None,
                      path: Optional[Path] = None) -> dict[str, int]:
    """Per-label instance counts for one domain."""
    return {lab: len(insts) for lab, insts in load_templates(domain, path).items()}


# ───────── Canonical-size metadata ─────────
# Stored as a plain "WxH" text file at the domain root so the user
# can inspect it. Set when the user trains the first batch for a
# domain (derived from segmented box medians); used at inference to
# normalise both glyph and template to the same pixel size before
# matchTemplate.

def read_canonical(domain: Optional[str] = None) -> Optional[tuple[int, int]]:
    p = domain_dir(domain) / _CANONICAL_FILE
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8").strip()
        w, h = txt.split("x", 1)
        return (int(w), int(h))
    except Exception:
        return None


def write_canonical(width: int, height: int,
                      domain: Optional[str] = None) -> None:
    if width <= 0 or height <= 0:
        return
    p = domain_dir(domain)
    p.mkdir(parents=True, exist_ok=True)
    (p / _CANONICAL_FILE).write_text(
        f"{int(width)}x{int(height)}", encoding="utf-8",
    )


def ensure_canonical_from_boxes(domain: str,
                                  boxes: list[tuple[int, int, int, int]],
                                  ) -> tuple[int, int]:
    """If no canonical exists for this domain, derive one from the
    median width / height of the supplied boxes and persist it.

    Returns the (w, h) pair used (either the freshly-computed median
    or the previously-stored value).
    """
    existing = read_canonical(domain)
    if existing is not None:
        return existing
    if not boxes:
        # Reasonable default if learner had no boxes (shouldn't happen)
        return (12, 18)
    ws = sorted(b[2] for b in boxes)
    hs = sorted(b[3] for b in boxes)
    mw = max(4, ws[len(ws) // 2])
    mh = max(6, hs[len(hs) // 2])
    write_canonical(mw, mh, domain=domain)
    return (mw, mh)


__all__ = [
    "DOMAINS", "digits_dir", "domain_dir",
    "load_templates", "save_templates",
    "clear_label", "clear_templates", "instance_counts",
    "read_canonical", "write_canonical", "ensure_canonical_from_boxes",
]
