"""Render a complete app QSS string from a TokenSet.

Kept intentionally template-style (Python f-string) for readability.
QSS does not support variables, so we re-render the whole sheet on
every theme change. This is cheap.
"""
from __future__ import annotations

from quickcast.ui.design.tokens import TokenSet


def render(tokens: TokenSet) -> str:
    p = tokens.palette
    r = tokens.radii
    sp = tokens.spacing
    t = tokens.type
    return f"""
/* ───────────────── globals ───────────────── */
* {{
    font-family: {t.sans};
    font-size: {t.md}pt;
    color: {p.text_primary};
}}

QMainWindow, QWidget#central, QWidget#appShell {{
    background: {p.bg_canvas};
}}

QToolTip {{
    background: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_default};
    padding: 4px 8px;
    border-radius: {r.input}px;
}}

/* ───────────────── cards ───────────────── */
QFrame#card {{
    background: {p.bg_surface};
    border: 1px solid {p.border_subtle};
    border-radius: {r.card}px;
}}

QFrame#cardHeader {{
    background: transparent;
    border: none;
}}

QLabel#title {{
    color: {p.text_primary};
    font-size: {t.lg}pt;
    font-weight: {t.w_semibold};
}}

QLabel#subtitle, QLabel[role="dim"] {{
    color: {p.text_secondary};
    font-size: {t.sm}pt;
}}

QLabel#sectionHeading {{
    color: {p.text_primary};
    font-size: {t.md}pt;
    font-weight: {t.w_semibold};
    letter-spacing: -0.005em;
}}

QLabel[role="caption"] {{
    color: {p.text_tertiary};
    font-size: {t.xs}pt;
}}

/* ───────────────── buttons ───────────────── */
QPushButton {{
    background: {p.bg_surface};
    color: {p.text_primary};
    border: 1px solid {p.border_default};
    border-radius: {r.button}px;
    padding: 6px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background: {p.bg_elevated}; }}
QPushButton:pressed {{ background: {p.bg_pressed}; }}
QPushButton:disabled {{
    color: {p.text_disabled};
    background: {p.bg_surface};
}}

QPushButton[variant="primary"] {{
    background: {p.accent_default};
    color: {p.text_inverse};
    border: none;
    font-weight: {t.w_semibold};
}}
QPushButton[variant="primary"]:hover  {{ background: {p.accent_hover};  }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_pressed}; }}

QPushButton[variant="ghost"] {{
    background: transparent;
    border: none;
    color: {p.text_secondary};
    padding: 6px 8px;
}}
QPushButton[variant="ghost"]:hover  {{ color: {p.text_primary}; background: {p.bg_hover}; }}
QPushButton[variant="ghost"]:pressed {{ background: {p.bg_pressed}; }}

QPushButton[variant="danger"] {{
    background: {p.state_danger};
    color: #fff;
    border: none;
    font-weight: {t.w_semibold};
}}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {r.button}px;
    padding: 4px;
    color: {p.text_secondary};
}}
QToolButton:hover {{ background: {p.bg_hover}; color: {p.text_primary}; }}
QToolButton:pressed {{ background: {p.bg_pressed}; }}
QToolButton:checked {{
    background: {p.accent_subtle};
    color: {p.accent_default};
    border-color: transparent;
}}

/* ───────────────── inputs ───────────────── */
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit {{
    background: {p.bg_input};
    color: {p.text_primary};
    border: 1px solid {p.border_default};
    border-radius: {r.input}px;
    padding: 4px 8px;
    selection-background-color: {p.accent_default};
    selection-color: {p.text_inverse};
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
    border-color: {p.border_focus};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{
    color: {p.text_disabled};
    background: {p.bg_surface};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_default};
    selection-background-color: {p.accent_subtle};
    selection-color: {p.text_primary};
    outline: 0;
}}

/* ───────────────── checkboxes / radios ───────────────── */
QCheckBox, QRadioButton {{ color: {p.text_primary}; spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    background: {p.bg_input};
    border: 1px solid {p.border_default};
}}
QCheckBox::indicator {{ border-radius: {r.input}px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p.border_strong};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent_default};
    border-color: {p.accent_default};
}}

/* ───────────────── progress / scroll ───────────────── */
QProgressBar {{
    background: {p.bg_input};
    border: 1px solid {p.border_subtle};
    border-radius: {r.input}px;
    text-align: center;
    color: {p.text_primary};
    height: 18px;
}}
QProgressBar::chunk {{
    background: {p.accent_default};
    border-radius: 3px;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 4px 2px 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_default}; border-radius: 3px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.border_strong}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px 4px 2px 4px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_default}; border-radius: 3px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.border_strong}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ───────────────── menus ───────────────── */
QMenuBar {{
    background: {p.title_bg};
    color: {p.text_secondary};
    border-bottom: 1px solid {p.border_subtle};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: {r.input}px;
}}
QMenuBar::item:selected {{ background: {p.bg_hover}; color: {p.text_primary}; }}

QMenu {{
    background: {p.bg_elevated};
    color: {p.text_primary};
    border: 1px solid {p.border_default};
    border-radius: {r.button}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 28px 6px 18px;
    border-radius: {r.input}px;
}}
QMenu::item:selected {{ background: {p.accent_subtle}; color: {p.text_primary}; }}
QMenu::separator {{
    height: 1px;
    background: {p.border_subtle};
    margin: 4px 6px;
}}

/* ───────────────── splitter ───────────────── */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {p.border_subtle}; }}

/* ───────────────── status bar ───────────────── */
QFrame#statusBar {{
    background: {p.title_bg};
    border-top: 1px solid {p.border_subtle};
}}

/* ───────────────── activity bar ───────────────── */
QFrame#activityBar {{
    background: {p.bg_canvas};
    border-right: 1px solid {p.border_subtle};
}}
QFrame#activityBar QToolButton {{
    border-radius: {r.button}px;
    padding: 8px;
    color: {p.text_tertiary};
}}
QFrame#activityBar QToolButton:hover {{ color: {p.text_primary}; }}
QFrame#activityBar QToolButton:checked {{
    color: {p.text_primary};
    background: transparent;
}}

/* ───────────────── sidebar ───────────────── */
QFrame#sidebar {{
    background: {p.bg_surface};
    border-right: 1px solid {p.border_subtle};
}}

/* ───────────────── title bar ───────────────── */
QFrame#titleBar {{
    background: {p.title_bg};
    border-bottom: 1px solid {p.border_subtle};
}}
QFrame#titleBar QLabel#appName {{
    color: {p.text_primary};
    font-weight: {t.w_semibold};
}}

/* ───────────────── log view ───────────────── */
QTextEdit#logView, QPlainTextEdit#logView {{
    background: {p.bg_input};
    color: {p.text_secondary};
    border: 1px solid {p.border_subtle};
    border-radius: {r.input}px;
    font-family: {t.mono};
    font-size: {t.sm}pt;
}}
"""


__all__ = ["render"]
