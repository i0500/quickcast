"""Design tokens — single source of truth.

All UI colors, spacing, typography, radii and motion live here. QSS is
generated from these tokens (see `qss_template.py`); custom-paint widgets
read directly from the active `TokenSet` via `T` (the global accessor).

Adding a theme = adding one new `TokenSet`. No QSS surgery elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# ── Spacing scale (4px base) ─────────────────────────────────────────
@dataclass(frozen=True)
class Spacing:
    s0: int = 0
    s1: int = 4
    s2: int = 8
    s3: int = 12
    s4: int = 16
    s5: int = 20
    s6: int = 24
    s8: int = 32
    s10: int = 40
    s12: int = 48


# ── Radii ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Radii:
    input: int = 4
    button: int = 6
    card: int = 8
    modal: int = 12
    pill: int = 9999


# ── Typography ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class Typography:
    sans: str = (
        "'Pretendard Variable', 'Pretendard', "
        "'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    )
    mono: str = "'JetBrains Mono', 'D2Coding', Consolas, monospace"

    # Sizes (pt for QFont, px for QSS use *.pt syntax in QSS)
    xs: int = 11
    sm: int = 12
    md: int = 13     # body default
    lg: int = 15
    xl: int = 18
    x2l: int = 22
    x3l: int = 28

    w_regular: int = 400
    w_medium: int = 500
    w_semibold: int = 600
    w_bold: int = 700


# ── Motion ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Motion:
    fast_ms: int = 120          # hover, focus
    med_ms: int = 180           # toggle
    slow_ms: int = 240          # panel slide

    ease_standard: str = "cubic-bezier(0.2, 0, 0, 1)"
    ease_emphasized: str = "cubic-bezier(0.3, 0, 0, 1)"


# ── Color palette (a TokenSet only differs in Palette + name) ─────────
@dataclass(frozen=True)
class Palette:
    # Backgrounds
    bg_canvas: str
    bg_surface: str
    bg_elevated: str
    bg_input: str
    bg_hover: str
    bg_pressed: str
    bg_selected: str

    # Borders
    border_subtle: str
    border_default: str
    border_strong: str
    border_focus: str

    # Text
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str
    text_inverse: str           # for use on accent fills

    # Accent (single)
    accent_default: str
    accent_hover: str
    accent_pressed: str
    accent_subtle: str          # 12% alpha equivalent for selected bg

    # State
    state_success: str
    state_warning: str
    state_danger: str
    state_info: str

    # Domain-specific
    hp_fill: str
    mp_fill: str
    pk_accent: str
    potion_accent: str

    # Title bar (frameless)
    title_bg: str
    title_fg: str


# ── Token bundle ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class TokenSet:
    name: str
    is_dark: bool
    palette: Palette
    spacing: Spacing = field(default_factory=Spacing)
    radii: Radii = field(default_factory=Radii)
    type: Typography = field(default_factory=Typography)
    motion: Motion = field(default_factory=Motion)


# ── Concrete palettes ─────────────────────────────────────────────────
GRAPHITE = Palette(
    bg_canvas="#0E1116",
    bg_surface="#14181F",
    bg_elevated="#1B2129",
    bg_input="#0A0D12",
    bg_hover="rgba(255, 255, 255, 0.04)",
    bg_pressed="rgba(255, 255, 255, 0.08)",
    bg_selected="rgba(91, 141, 239, 0.12)",

    border_subtle="rgba(255, 255, 255, 0.06)",
    border_default="rgba(255, 255, 255, 0.10)",
    border_strong="rgba(255, 255, 255, 0.18)",
    border_focus="#5B8DEF",

    text_primary="#E6EAF0",
    text_secondary="#9AA4B0",
    text_tertiary="#6B7280",
    text_disabled="#4B5563",
    text_inverse="#0E1116",

    accent_default="#5B8DEF",
    accent_hover="#6E9CF2",
    accent_pressed="#4A7BD8",
    accent_subtle="rgba(91, 141, 239, 0.16)",

    state_success="#10B981",
    state_warning="#F59E0B",
    state_danger="#EF4444",
    state_info="#3B82F6",

    hp_fill="#F43F5E",
    mp_fill="#38BDF8",
    pk_accent="#FB923C",
    potion_accent="#A3E635",

    title_bg="#0B0E13",
    title_fg="#E6EAF0",
)

PAPER = Palette(
    bg_canvas="#F4F6FA",
    bg_surface="#FFFFFF",
    bg_elevated="#FFFFFF",
    bg_input="#FFFFFF",
    bg_hover="rgba(0, 0, 0, 0.05)",
    bg_pressed="rgba(0, 0, 0, 0.10)",
    bg_selected="rgba(37, 99, 235, 0.12)",

    border_subtle="rgba(15, 23, 42, 0.08)",
    border_default="rgba(15, 23, 42, 0.16)",
    border_strong="rgba(15, 23, 42, 0.28)",
    border_focus="#2563EB",

    # Darker than dark theme's equivalents so they read on white surfaces
    text_primary="#0F172A",     # almost-black
    text_secondary="#334155",   # was 475569 — darken for stronger contrast
    text_tertiary="#64748B",    # was 94A3B8 — much darker, readable on white
    text_disabled="#94A3B8",    # was CBD5E1 — still legible
    text_inverse="#FFFFFF",

    accent_default="#2563EB",
    accent_hover="#3B82F6",
    accent_pressed="#1D4ED8",
    accent_subtle="rgba(37, 99, 235, 0.14)",

    state_success="#059669",
    state_warning="#D97706",
    state_danger="#DC2626",
    state_info="#2563EB",

    hp_fill="#E11D48",
    mp_fill="#0EA5E9",
    pk_accent="#EA580C",
    potion_accent="#65A30D",

    title_bg="#E2E8F0",
    title_fg="#0F172A",
)


GRAPHITE_TOKENS = TokenSet(name="Graphite", is_dark=True, palette=GRAPHITE)
PAPER_TOKENS = TokenSet(name="Paper", is_dark=False, palette=PAPER)


# ── Global accessor ──────────────────────────────────────────────────
class _ActiveTokens:
    """Lazy singleton holding the currently active TokenSet.

    Custom-paint widgets read `T.palette.hp_fill` etc. on every paint;
    `apply_theme()` swaps the underlying TokenSet and emits SignalBus.
    """
    def __init__(self) -> None:
        self._current: TokenSet = GRAPHITE_TOKENS

    def set(self, tokens: TokenSet) -> None:
        self._current = tokens

    @property
    def palette(self) -> Palette:
        return self._current.palette

    @property
    def spacing(self) -> Spacing:
        return self._current.spacing

    @property
    def radii(self) -> Radii:
        return self._current.radii

    @property
    def type(self) -> Typography:
        return self._current.type

    @property
    def motion(self) -> Motion:
        return self._current.motion

    @property
    def name(self) -> str:
        return self._current.name

    @property
    def is_dark(self) -> bool:
        return self._current.is_dark


T = _ActiveTokens()


__all__ = [
    "Palette", "Spacing", "Radii", "Typography", "Motion", "TokenSet",
    "GRAPHITE", "PAPER", "GRAPHITE_TOKENS", "PAPER_TOKENS", "T",
]
