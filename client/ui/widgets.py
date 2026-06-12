"""Reusable iOS-style widgets for the manager GUI.

Everything here is theme-aware: widgets take the current Theme at construction
and expose set_theme() so the light/dark toggle restyles the whole window
without rebuilding it. Custom painting (Switch) follows the design's exact
geometry: 44x27 track, 23px knob, 2px inset.
"""
from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QObject, QPropertyAnimation, Qt,
                          pyqtProperty, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                             QPushButton, QVBoxLayout, QWidget)

from .theme import Theme


class Switch(QAbstractButton):
    """iOS toggle: 44x27 rounded track, white knob that slides on toggle.
    On-color is the theme green (iOS convention), off-color the switchOff token."""

    def __init__(self, theme: Theme, checked: bool = False, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 27)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knobPos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_pos(self) -> float:
        return self._pos

    def _set_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    knobPos = pyqtProperty(float, fget=_get_pos, fset=_set_pos)

    def paintEvent(self, _e) -> None:  # noqa: N802 (Qt signature)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, off = QColor(self._theme.green), QColor(self._theme.switch_off)
        # Blend track color with the animation so the color fades with the slide.
        t = self._pos
        track = QColor(int(off.red() + (on.red() - off.red()) * t),
                       int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t),
                       int(off.alpha() + (on.alpha() - off.alpha()) * t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, 44, 27, 13.5, 13.5)
        p.setBrush(QColor("#FFFFFF"))
        x = 2 + (44 - 27) * self._pos
        p.drawEllipse(int(x), 2, 23, 23)


class Segmented(QWidget):
    """iOS segmented control on a chip background. changed(str) emits the value."""

    changed = pyqtSignal(str)

    def __init__(self, theme: Theme, options: list[tuple[str, str]],
                 current: str | None = None, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._value = current if current is not None else options[0][0]
        self._buttons: dict[str, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        for value, label in options:
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=value: self.set_value(v, emit=True))
            self._buttons[value] = b
            lay.addWidget(b)
        self._restyle()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        if value not in self._buttons:
            return
        changed = value != self._value
        self._value = value
        self._restyle()
        if emit and changed:
            self.changed.emit(value)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._restyle()

    def _restyle(self) -> None:
        t = self._theme
        self.setStyleSheet(f"background: {t.chip_bg}; border-radius: 9px;")
        for value, b in self._buttons.items():
            active = value == self._value
            b.setStyleSheet(
                f"QPushButton {{ background: {t.card if active else 'transparent'};"
                f" color: {t.text if active else t.text2}; border-radius: 7px;"
                f" padding: 4px 13px; font-size: 12px; font-weight: 500; border: none; }}")


class RowsCard(QFrame):
    """The design's grouped inset list: a card whose rows are separated by
    hairlines ([data-rows]). add_row() appends any widget as a row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._count = 0

    def add_row(self, w: QWidget) -> QWidget:
        if self._count:
            sep = QFrame()
            sep.setObjectName("rowSep")
            sep.setFrameShape(QFrame.Shape.HLine)
            self._lay.addWidget(sep)
        self._lay.addWidget(w)
        self._count += 1
        return w

    def clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._count = 0


def setting_row(title: str, sub: str, control: QWidget | None) -> QWidget:
    """A standard settings row: title + optional caption on the left, control on
    the right. The plain-language captions are a design requirement."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(14, 11, 14, 11)
    lay.setSpacing(12)
    text = QVBoxLayout()
    text.setSpacing(1)
    t = QLabel(title)
    t.setObjectName("rowTitle")
    text.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("rowSub")
        s.setWordWrap(True)
        text.addWidget(s)
    lay.addLayout(text, 1)
    if control is not None:
        lay.addWidget(control, 0, Qt.AlignmentFlag.AlignRight)
    return w


def section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionLabel")
    lbl.setContentsMargins(6, 0, 6, 0)
    return lbl


def key_chip(text: str) -> QLabel:
    chip = QLabel(text)
    chip.setObjectName("keyChip")
    return chip


class AsyncCall(QObject):
    """Run a blocking callable on a daemon thread and deliver
    (ok, result_or_error) via the done signal on the GUI thread. Every server
    request in the GUI goes through this so a slow or down backend never
    freezes the window.

    Deliberately a plain Python thread, not a QThread: the worker emits `done`
    from its thread and Qt queues the delivery to the receivers' (GUI) thread,
    which is all that is needed. A QThread parented into the widget tree gets
    destroyed at shutdown while a slow request still runs, which fail-fasts
    the whole process (0xC0000409); a daemon thread just dies with it."""

    done = pyqtSignal(bool, object)

    def __init__(self, owner: QObject, fn, *args, **kwargs):
        super().__init__(owner)  # parented: cleaned up with its page/window
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def start(self) -> "AsyncCall":
        import threading
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self) -> None:
        try:
            self.done.emit(True, self._fn(*self._args, **self._kwargs))
        except Exception as e:  # noqa: BLE001 - errors are delivered, not raised
            self.done.emit(False, e)
