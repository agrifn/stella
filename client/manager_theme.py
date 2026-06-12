"""Theme tokens and reusable widgets for the Command & Voice Manager GUI.

The visual design comes from a Claude Design handoff (iOS-Settings-like: grouped
cards, segmented controls, pill switches, light/dark). Tokens below are lifted
verbatim from the prototype's CSS variables so the Qt build matches it. Qt has
no box-shadow, so card depth is approximated with the cardBorder hairline.

Widgets here are pure presentation (no API calls) so command_manager.py stays
readable: Switch (iOS toggle), Segmented (single-choice pill group), Card
(rounded group container with separator rows), chip/badge label factories.
"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

# -- design tokens (mirror the prototype's :root / [data-theme=dark] blocks) ----
LIGHT = {
    "bg": "#F2F2F7", "card": "#FFFFFF", "cardBorder": "rgba(0,0,0,6%)",
    "text": "#1D1D1F", "text2": "#73737B", "text3": "#AEAEB2",
    "sep": "rgba(60,60,67,12%)", "chipBg": "rgba(120,120,128,10%)",
    "hover": "rgba(0,0,0,4%)", "accent": "#0782C6", "onAccent": "#FFFFFF",
    "accentSoft": "rgba(7,130,198,10%)", "green": "#34C759", "red": "#FF3B30",
    "orange": "#FF9500", "switchOff": "rgba(120,120,128,20%)",
    # QColor versions for painted widgets (QColor has no % alpha parsing)
    "_switchOff": QColor(120, 120, 128, 51), "_green": QColor(52, 199, 89),
    "_accent": QColor(7, 130, 198), "_knob": QColor(255, 255, 255),
}
DARK = {
    "bg": "#101013", "card": "#1C1C21", "cardBorder": "rgba(255,255,255,7%)",
    "text": "#F5F5F7", "text2": "#9C9CA3", "text3": "#636370",
    "sep": "rgba(255,255,255,9%)", "chipBg": "rgba(120,120,128,18%)",
    "hover": "rgba(255,255,255,5%)", "accent": "#64D2FF", "onAccent": "#062533",
    "accentSoft": "rgba(100,210,255,13%)", "green": "#30D158", "red": "#FF453A",
    "orange": "#FF9F0A", "switchOff": "rgba(120,120,128,32%)",
    "_switchOff": QColor(120, 120, 128, 82), "_green": QColor(48, 209, 88),
    "_accent": QColor(100, 210, 255), "_knob": QColor(255, 255, 255),
}

# Module-level current theme: painted widgets read this at paint time, so a
# theme flip just needs a global update() pass (see apply_theme).
_current = dict(LIGHT)


def tokens() -> dict:
    return _current


MONO = "'Cascadia Code', Consolas, monospace"


def build_stylesheet(t: dict) -> str:
    """One app-wide stylesheet driven by objectName / dynamic 'class' selectors.
    Inline per-widget styles are avoided so a theme switch is a single call."""
    return f"""
    QWidget {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', system-ui; font-size: 13px; color: {t['text']}; }}
    QMainWindow, #Root {{ background: {t['bg']}; }}
    #Header {{ background: {t['card']}; border-bottom: 1px solid {t['sep']}; }}
    #Logo {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #38B6E8, stop:1 #4A5FE0);
             color: white; border-radius: 8px; font-size: 13px; }}
    #Brand {{ font-size: 14px; font-weight: 700; letter-spacing: 2.5px; }}
    #Sidebar {{ border-right: 1px solid {t['sep']}; background: {t['bg']}; }}
    QLabel[class="h1"] {{ font-size: 24px; font-weight: 700; }}
    QLabel[class="caption"] {{ font-size: 13px; color: {t['text2']}; }}
    QLabel[class="caption2"] {{ font-size: 12px; color: {t['text2']}; }}
    QLabel[class="caption3"] {{ font-size: 11.5px; color: {t['text3']}; }}
    QLabel[class="group"] {{ font-size: 11px; font-weight: 600; letter-spacing: 0.7px; color: {t['text2']}; }}
    QLabel[class="field"] {{ font-size: 10.5px; font-weight: 600; letter-spacing: 0.6px; color: {t['text3']}; }}
    QLabel[class="chip"] {{ font-family: {MONO}; font-size: 11.5px; padding: 3px 8px;
                            border-radius: 6px; background: {t['chipBg']}; color: {t['text2']}; }}
    QLabel[class="confirmBadge"] {{ font-size: 10px; font-weight: 700; letter-spacing: 0.6px;
                            color: {t['red']}; background: rgba(255,69,58,12%); padding: 3px 7px; border-radius: 6px; }}
    QFrame[class="card"] {{ background: {t['card']}; border: 1px solid {t['cardBorder']}; border-radius: 13px; }}
    QFrame[class="inset"] {{ background: transparent; border: 1px solid {t['sep']}; border-radius: 10px; }}
    QFrame[class="sepline"] {{ background: {t['sep']}; border: none; }}
    QLineEdit, QPlainTextEdit, QSpinBox {{ padding: 7px 11px; border-radius: 9px; border: 1px solid {t['sep']};
                  background: {t['chipBg']}; selection-background-color: {t['accent']}; }}
    QLineEdit:focus, QSpinBox:focus {{ border-color: {t['accent']}; }}
    QLineEdit[class="mono"] {{ font-family: {MONO}; font-size: 12.5px; }}
    QComboBox {{ padding: 5px 10px; border-radius: 8px; border: 1px solid {t['sep']}; background: {t['chipBg']}; }}
    QComboBox QAbstractItemView {{ background: {t['card']}; color: {t['text']};
                  selection-background-color: {t['accentSoft']}; selection-color: {t['accent']}; }}
    QPushButton {{ border: none; background: transparent; padding: 6px 10px; border-radius: 9px; }}
    QPushButton[class="primary"] {{ background: {t['accent']}; color: {t['onAccent']};
                  padding: 7px 15px; font-weight: 600; }}
    QPushButton[class="primary"]:hover {{ background: {t['accent']}; }}
    QPushButton[class="soft"] {{ background: {t['accentSoft']}; color: {t['accent']};
                  font-weight: 600; padding: 7px 13px; }}
    QPushButton[class="chip"] {{ background: {t['chipBg']}; color: {t['text']}; padding: 7px 13px; }}
    QPushButton[class="chipMuted"] {{ background: {t['chipBg']}; color: {t['text2']}; font-weight: 600; }}
    QPushButton[class="link"] {{ color: {t['accent']}; font-weight: 500; }}
    QPushButton[class="danger"] {{ color: {t['red']}; }}
    QPushButton[class="danger"]:hover {{ background: rgba(255,69,58,8%); }}
    QPushButton[class="ghost"]:hover {{ background: {t['hover']}; }}
    QPushButton[class="keychip"] {{ font-family: {MONO}; font-size: 11.5px; border: 1px solid {t['sep']};
                  background: {t['chipBg']}; padding: 4px 8px; border-radius: 7px; }}
    QPushButton[class="navrow"] {{ padding: 7px 10px; border-radius: 9px; font-size: 13.5px;
                  font-weight: 500; text-align: left; }}
    QPushButton[class="navrow"]:hover {{ background: {t['hover']}; }}
    QPushButton[class="navrow"][active="true"] {{ background: {t['accentSoft']}; color: {t['accent']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QSlider::groove:horizontal {{ height: 5px; border-radius: 3px; background: {t['chipBg']}; }}
    QSlider::sub-page:horizontal {{ height: 5px; border-radius: 3px; background: {t['accent']}; }}
    QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
                  background: white; border: 1px solid {t['sep']}; }}
    QStatusBar {{ color: {t['text2']}; }}
    QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['sep']}; }}
    """


def apply_theme(app, name: str) -> None:
    """Switch light/dark: update the token dict in place (painted widgets read it
    live) and reinstall the app stylesheet (styled widgets restyle themselves)."""
    _current.clear()
    _current.update(DARK if name == "dark" else LIGHT)
    app.setStyleSheet(build_stylesheet(_current))


def set_class(w: QWidget, klass: str) -> QWidget:
    """Tag a widget for the stylesheet's [class=...] selectors."""
    w.setProperty("class", klass)
    return w


def repolish(w: QWidget) -> None:
    """Re-evaluate stylesheet selectors after a dynamic property change."""
    w.style().unpolish(w)
    w.style().polish(w)


# -- widgets --------------------------------------------------------------------
class Switch(QWidget):
    """iOS-style pill toggle (44x27, sliding knob). Painted, so it animates and
    matches the design exactly; colors come from the live token dict."""
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(44, 27)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        if on == self._checked:
            return
        self._checked = on
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    knobPos = pyqtProperty(float, _get_pos, _set_pos)

    def mouseReleaseEvent(self, e):  # noqa: N802 (Qt signature)
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)

    def paintEvent(self, e):  # noqa: N802 (Qt signature)
        t = tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        off, on = t["_switchOff"], t["_green"]
        mix = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
            int(off.alpha() + (on.alpha() - off.alpha()) * self._pos))
        p.setBrush(mix)
        p.drawRoundedRect(0, 0, 44, 27, 13.5, 13.5)
        p.setBrush(t["_knob"])
        p.drawEllipse(int(2 + 17 * self._pos), 2, 23, 23)


class Segmented(QFrame):
    """Single-choice segmented control on a chip-colored track; the selected
    segment gets the card background, like the design's theme/mode pickers."""
    changed = pyqtSignal(str)

    def __init__(self, options: list[tuple[str, str]], value: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        self._buttons: dict[str, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        for key, label in options:
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_value(k, emit=True))
            lay.addWidget(b)
            self._buttons[key] = b
        self._value = value if value in self._buttons else options[0][0]
        self._restyle()

    def value(self) -> str:
        return self._value

    def set_value(self, key: str, emit: bool = False) -> None:
        if key not in self._buttons:
            return
        changed = key != self._value
        self._value = key
        self._restyle()
        if emit and changed:
            self.changed.emit(key)

    def _restyle(self) -> None:
        t = tokens()
        self.setStyleSheet(f"#Segmented {{ background: {t['chipBg']}; border-radius: 9px; }}")
        for k, b in self._buttons.items():
            sel = k == self._value
            b.setStyleSheet(
                f"QPushButton {{ padding: 4px 13px; border-radius: 7px; font-size: 12.5px; font-weight: 500;"
                f" background: {t['card'] if sel else 'transparent'};"
                f" color: {t['text'] if sel else t['text2']}; }}")

    def refresh_theme(self) -> None:
        self._restyle()


class Card(QFrame):
    """Rounded group container; add_row() inserts hairline separators between
    rows, matching the design's [data-rows] lists."""
    def __init__(self, parent=None):
        super().__init__(parent)
        set_class(self, "card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._count = 0

    def add_row(self, w: QWidget) -> QWidget:
        if self._count:
            line = QFrame()
            set_class(line, "sepline")
            line.setFixedHeight(1)
            self._lay.addWidget(line)
        self._lay.addWidget(w)
        self._count += 1
        return w


def row_widget(title: str, caption: str | None, control: QWidget | None,
               *extra: QWidget) -> QWidget:
    """Standard settings row: title + optional caption on the left, control(s)
    on the right. The design's most common building block."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 11, 14, 11)
    lay.setSpacing(12)
    text = QVBoxLayout()
    text.setSpacing(1)
    title_l = QLabel(title)
    title_l.setStyleSheet("font-size: 13.5px;")
    text.addWidget(title_l)
    if caption:
        cap = QLabel(caption)
        set_class(cap, "caption2")
        text.addWidget(cap)
    lay.addLayout(text, 1)
    for x in extra:
        lay.addWidget(x)
    if control is not None:
        lay.addWidget(control)
    return w


def group_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    set_class(lbl, "group")
    lbl.setContentsMargins(6, 0, 6, 0)
    return lbl


def nav_icon(symbol: str, color: str) -> QLabel:
    """The sidebar's 24px rounded icon square (iOS Settings style)."""
    lbl = QLabel(symbol)
    lbl.setFixedSize(24, 24)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background: {color}; color: white; border-radius: 6px; font-size: 13px;")
    return lbl
