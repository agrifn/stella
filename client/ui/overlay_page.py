"""Overlay HUD page: a live preview of the in-game pill plus its real controls.

Implements the design's Overlay screen wired to the keys overlay.py actually
reads: corner (4 of the design's 9 grid cells; edges and center are disabled
with a tooltip), opacity, scale, and Steam-style auto-hide. The preview pill
mirrors overlay.py's visual language (mode badge color, listening dot) and the
Preview State segmented control flips it between Idle, Listening, Command, and
Chat so you can see each state without launching the game.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

from .theme import Theme
from .widgets import RowsCard, Segmented, Switch, setting_row

_CORNERS = {(0, 0): "top-left", (0, 2): "top-right",
            (2, 0): "bottom-left", (2, 2): "bottom-right"}
_STATES = [("idle", "Idle"), ("listening", "Listening"),
           ("command", "Command"), ("chat", "Chat")]


class OverlayPage(QWidget):
    def __init__(self, settings_path: Path, theme: Theme):
        super().__init__()
        self.theme = theme
        self._path = settings_path
        try:
            self._data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        c = self._data.get("client", {})
        self._corner = c.get("overlay_corner", "bottom-right")
        self._state = "command"

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        _scroll = QScrollArea()
        _scroll.setWidgetResizable(True)
        shell.addWidget(_scroll)
        _content = QWidget()
        _scroll.setWidget(_content)
        outer = QVBoxLayout(_content)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        t = QLabel("Overlay HUD")
        t.setObjectName("pageTitle")
        sub = QLabel("The little status pill that floats over the game. "
                     "Click-through, always on top.")
        sub.setObjectName("pageSub")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        save = QPushButton("Save Changes")
        save.setObjectName("accent")
        save.clicked.connect(self._save)
        head.addWidget(save, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        # Preview area: a fake game capture with the pill docked in the corner.
        self.preview = QFrame()
        self.preview.setMinimumHeight(280)
        self.preview.setStyleSheet(
            "background: #14161B; border-radius: 14px; border: 1px solid rgba(255,255,255,0.07);")
        pv = QGridLayout(self.preview)
        pv.setContentsMargins(18, 18, 18, 18)
        watermark = QLabel("[ game capture ]")
        watermark.setStyleSheet("color: rgba(255,255,255,0.22); font-family: Consolas,"
                                " monospace; font-size: 11px; letter-spacing: 1px;"
                                " border: none; background: transparent;")
        pv.addWidget(watermark, 1, 1, Qt.AlignmentFlag.AlignCenter)
        for r in range(3):
            pv.setRowStretch(r, 1)
        for col in range(3):
            pv.setColumnStretch(col, 1)
        self.pill = QWidget(self.preview)
        pl = QHBoxLayout(self.pill)
        pl.setContentsMargins(14, 9, 14, 9)
        pl.setSpacing(10)
        self.dot = QLabel("●")
        self.mode_badge = QLabel("COMMAND")
        self.pill_text = QLabel("“max shields” → Shields at maximum.")
        pl.addWidget(self.dot)
        pl.addWidget(self.mode_badge)
        pl.addWidget(self.pill_text)
        outer.addWidget(self.preview)

        controls = RowsCard()
        self.state_seg = Segmented(theme, _STATES, current="command")
        self.state_seg.changed.connect(self._set_state)
        controls.add_row(setting_row("Preview state", "", self.state_seg))

        grid_holder = QWidget()
        gl = QGridLayout(grid_holder)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(4)
        self._cells: dict[str, QPushButton] = {}
        for r in range(3):
            for col in range(3):
                b = QPushButton()
                b.setFixedSize(22, 22)
                corner = _CORNERS.get((r, col))
                if corner:
                    b.setCursor(Qt.CursorShape.PointingHandCursor)
                    b.clicked.connect(lambda _=False, v=corner: self._set_corner(v))
                    self._cells[corner] = b
                else:
                    b.setEnabled(False)
                    b.setToolTip("The overlay docks to a corner")
                gl.addWidget(b, r, col)
        controls.add_row(setting_row("Position on screen", "Pick a corner", grid_holder))

        op_row = QWidget()
        ol = QVBoxLayout(op_row)
        ol.setContentsMargins(14, 11, 14, 11)
        otop = QHBoxLayout()
        o_lab = QLabel("Opacity")
        o_lab.setObjectName("rowSub")
        self.op_val = QLabel("")
        self.op_val.setObjectName("rowSub")
        otop.addWidget(o_lab, 1)
        otop.addWidget(self.op_val)
        ol.addLayout(otop)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setValue(int(round(float(c.get("overlay_opacity", 0.85)) * 100)))
        self.opacity.valueChanged.connect(self._opacity_changed)
        ol.addWidget(self.opacity)
        controls.add_row(op_row)

        sc_row = QWidget()
        scl = QVBoxLayout(sc_row)
        scl.setContentsMargins(14, 11, 14, 11)
        stop = QHBoxLayout()
        s_lab = QLabel("Size")
        s_lab.setObjectName("rowSub")
        self.sc_val = QLabel("")
        self.sc_val.setObjectName("rowSub")
        stop.addWidget(s_lab, 1)
        stop.addWidget(self.sc_val)
        scl.addLayout(stop)
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(50, 150)
        self.scale.setValue(int(round(float(c.get("overlay_scale", 0.8)) * 100)))
        self.scale.valueChanged.connect(lambda v: self.sc_val.setText(f"{v}%"))
        self.sc_val.setText(f"{self.scale.value()}%")
        scl.addWidget(self.scale)
        controls.add_row(sc_row)

        self.auto_hide = Switch(theme, bool(c.get("overlay_auto_hide", True)))
        hide_s = float(c.get("overlay_hide_seconds", 4.0))
        controls.add_row(setting_row(
            "Auto-hide when idle",
            f"Fades out after {hide_s:g}s; stays up while listening or on a warning",
            self.auto_hide))
        outer.addWidget(controls)
        note = QLabel("Changes apply the next time STELLA launches.")
        note.setObjectName("hint")
        outer.addWidget(note)

        self._opacity_changed(self.opacity.value())
        self._render()

    # -- preview ------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        self._state = state
        self._render()

    def _set_corner(self, corner: str) -> None:
        self._corner = corner
        self._render()

    def _opacity_changed(self, v: int) -> None:
        self.op_val.setText(f"{v}%")
        self._render()

    def _render(self) -> None:
        t = self.theme
        for corner, b in self._cells.items():
            active = corner == self._corner
            b.setStyleSheet(f"QPushButton {{ background: {t.accent if active else t.chip_bg};"
                            f" border-radius: 6px; border: none; }}")
        op = self.opacity.value() / 100.0
        listening = self._state == "listening"
        mode = "CHAT" if self._state == "chat" else "COMMAND"
        mode_color = "#f7b955" if mode == "CHAT" else "#7FD6FF"
        self.dot.setStyleSheet(f"color: {'#ff5d5d' if listening else '#444b5a'};"
                               " background: transparent; border: none;")
        self.mode_badge.setStyleSheet(
            f"color: {mode_color}; font-size: 10px; font-weight: 700;"
            " letter-spacing: 1.2px; background: transparent; border: none;")
        self.mode_badge.setText(mode)
        text = {"idle": "", "listening": "listening…",
                "command": "“max shields” → Shields at maximum.",
                "chat": "typing: “on my way to Hurston”"}[self._state]
        self.pill_text.setText(text)
        self.pill_text.setVisible(bool(text))
        self.pill_text.setStyleSheet("color: rgba(255,255,255,0.92); font-size: 12px;"
                                     " background: transparent; border: none;")
        self.pill.setStyleSheet(
            f"background: rgba(18,20,26,{0.78 * op:.2f}); border-radius: 13px;"
            " border: 1px solid rgba(255,255,255,0.14);")
        self.pill.adjustSize()
        margin = 18
        pw, ph = self.pill.sizeHint().width(), self.pill.sizeHint().height()
        w, h = self.preview.width() or 700, self.preview.height() or 280
        x = margin if "left" in self._corner else max(margin, w - pw - margin)
        y = margin if "top" in self._corner else max(margin, h - ph - margin)
        self.pill.setGeometry(x, y, pw, ph)

    def resizeEvent(self, e) -> None:  # noqa: N802 (Qt signature)
        super().resizeEvent(e)
        self._render()

    # -- save ---------------------------------------------------------------
    def _save(self) -> None:
        c = self._data.setdefault("client", {})
        c["overlay_corner"] = self._corner
        c["overlay_opacity"] = round(self.opacity.value() / 100.0, 2)
        c["overlay_scale"] = round(self.scale.value() / 100.0, 2)
        c["overlay_auto_hide"] = self.auto_hide.isChecked()
        try:
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.window().toast("Overlay settings saved — relaunch STELLA to apply")

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.auto_hide.set_theme(theme)
        self.state_seg.set_theme(theme)
        self._render()
