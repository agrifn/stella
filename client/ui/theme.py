"""Design tokens and the app stylesheet for the iOS-style manager GUI.

The light/dark palettes are copied verbatim from the Claude Design prototype
(design bundle 'iOS UI Redesign', STELLA Manager.dc.html :root / [data-theme=dark]),
so the implementation stays pixel-faithful to the approved design. Qt stylesheets
have no CSS variables, so build_qss() bakes a Theme into a concrete stylesheet;
custom-painted widgets (Switch, nav icons) read the same Theme object directly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    card: str
    card_border: str
    text: str
    text2: str
    text3: str
    sep: str
    chip_bg: str
    hover: str
    accent: str
    on_accent: str
    accent_soft: str
    green: str
    red: str
    orange: str
    switch_off: str


LIGHT = Theme(
    name="light",
    bg="#F2F2F7", card="#FFFFFF", card_border="rgba(0,0,0,0.055)",
    text="#1D1D1F", text2="#73737B", text3="#AEAEB2",
    sep="rgba(60,60,67,0.12)", chip_bg="rgba(120,120,128,0.10)",
    hover="rgba(0,0,0,0.035)",
    accent="#0782C6", on_accent="#FFFFFF", accent_soft="rgba(7,130,198,0.10)",
    green="#34C759", red="#FF3B30", orange="#FF9500",
    switch_off="rgba(120,120,128,0.20)",
)

DARK = Theme(
    name="dark",
    bg="#101013", card="#1C1C21", card_border="rgba(255,255,255,0.07)",
    text="#F5F5F7", text2="#9C9CA3", text3="#636370",
    sep="rgba(255,255,255,0.09)", chip_bg="rgba(120,120,128,0.18)",
    hover="rgba(255,255,255,0.05)",
    accent="#64D2FF", on_accent="#062533", accent_soft="rgba(100,210,255,0.13)",
    green="#30D158", red="#FF453A", orange="#FF9F0A",
    switch_off="rgba(120,120,128,0.32)",
)

THEMES = {"light": LIGHT, "dark": DARK}

# Windows ships Segoe UI Variable; SF Pro is not redistributable, so the design's
# font stack falls through to it (the prototype lists it explicitly).
FONT_STACK = "'Segoe UI Variable Text', 'Segoe UI', 'SF Pro Text', sans-serif"
MONO_STACK = "'Cascadia Code', 'Consolas', 'SF Mono', monospace"


def build_qss(t: Theme) -> str:
    """The full application stylesheet for one theme. Widgets opt in to roles via
    objectName (e.g. #card, #chip, #sectionLabel) so the page code stays clean."""
    return f"""
    QWidget {{ font-family: {FONT_STACK}; font-size: 13px; color: {t.text}; }}
    QMainWindow, #root {{ background: {t.bg}; }}

    #header {{ background: {t.card}; border-bottom: 1px solid {t.sep}; }}
    #logoTitle {{ font-size: 14px; font-weight: 700; letter-spacing: 2.5px; }}
    #healthChip {{ background: {t.chip_bg}; border-radius: 12px; padding: 4px 10px;
                   font-size: 11px; color: {t.text2}; }}

    #sidebar {{ background: {t.bg}; border-right: 1px solid {t.sep}; }}
    #sidebarFootnote {{ font-size: 11px; color: {t.text3}; }}

    #card {{ background: {t.card}; border: 1px solid {t.card_border}; border-radius: 13px; }}
    #cardHeader {{ font-size: 14px; font-weight: 600; }}
    #rowSep {{ background: {t.sep}; min-height: 1px; max-height: 1px; border: none; }}

    #sectionLabel {{ font-size: 11px; font-weight: 600; letter-spacing: 0.7px;
                     color: {t.text2}; }}
    #pageTitle {{ font-size: 24px; font-weight: 700; }}
    #pageSub {{ font-size: 13px; color: {t.text2}; }}
    #rowTitle {{ font-size: 13px; }}
    #rowTitleStrong {{ font-size: 13px; font-weight: 500; }}
    #rowSub {{ font-size: 12px; color: {t.text2}; }}
    #hint {{ font-size: 11px; color: {t.text3}; }}
    #fieldLabel {{ font-size: 10px; font-weight: 600; letter-spacing: 0.6px; color: {t.text3}; }}

    #keyChip {{ font-family: {MONO_STACK}; font-size: 11px; padding: 3px 8px;
                border-radius: 6px; background: {t.chip_bg}; color: {t.text2}; }}
    #confirmBadge {{ font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
                     color: {t.red}; background: rgba(255,69,58,0.12);
                     padding: 3px 7px; border-radius: 6px; }}
    #chevron {{ color: {t.text3}; font-size: 14px; }}

    QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
        background: {t.chip_bg}; border: 1px solid {t.sep}; border-radius: 9px;
        padding: 7px 11px; font-size: 13px; color: {t.text};
        selection-background-color: {t.accent}; selection-color: {t.on_accent}; }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {t.accent}; }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}

    QComboBox {{ background: {t.chip_bg}; border: 1px solid {t.sep}; border-radius: 8px;
                 padding: 5px 10px; font-size: 12px; color: {t.text}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: {t.card}; color: {t.text};
                                   border: 1px solid {t.sep};
                                   selection-background-color: {t.accent_soft};
                                   selection-color: {t.text}; }}

    QPushButton {{ background: {t.chip_bg}; color: {t.text}; border: none;
                   border-radius: 9px; padding: 7px 14px; font-size: 13px;
                   font-weight: 500; }}
    QPushButton:hover {{ background: {t.hover}; }}
    QPushButton:disabled {{ color: {t.text3}; }}
    QPushButton#accent {{ background: {t.accent}; color: {t.on_accent}; font-weight: 600; }}
    QPushButton#accentSoft {{ background: {t.accent_soft}; color: {t.accent}; font-weight: 600; }}
    QPushButton#destructive {{ background: transparent; color: {t.red}; }}
    QPushButton#destructive:hover {{ background: rgba(255,69,58,0.08); }}
    QPushButton#ghost {{ background: transparent; color: {t.accent}; font-weight: 500; }}
    QPushButton#closeRound {{ background: {t.chip_bg}; color: {t.text2};
                              border-radius: 12px; padding: 0px; min-width: 24px;
                              max-width: 24px; min-height: 24px; max-height: 24px;
                              font-size: 11px; }}
    QPushButton#phraseChip {{ background: {t.chip_bg}; color: {t.text};
                              border-radius: 12px; padding: 4px 10px; font-size: 12px; }}

    QSlider::groove:horizontal {{ height: 4px; background: {t.chip_bg}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #FFFFFF; width: 18px; height: 18px;
                                  margin: -7px 0; border-radius: 9px;
                                  border: 1px solid rgba(0,0,0,0.12); }}

    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {t.chip_bg}; border-radius: 5px;
                                   min-height: 30px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.sep};
                padding: 4px 8px; }}
    """
