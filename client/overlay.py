"""Always-on-top status overlay for STELLA.

Frameless, translucent, click-through (input passes to the game underneath), and
docked to a screen corner. Shows the current mode, a listening indicator, the
last recognized speech, and STELLA's last response.

Note: over an EXCLUSIVE-fullscreen game the overlay may not be visible; run Star
Citizen in borderless/windowed for the HUD to show.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
)

_MODE_COLORS = {"COMMAND": "#39d98a", "CHAT": "#f7b955"}


class Overlay(QWidget):
    def __init__(self, corner: str = "top-left", opacity: float = 0.85, margin: int = 24):
        super().__init__()
        self._corner = corner
        self._margin = margin

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(opacity)
        self.setFixedWidth(380)

        panel = QFrame(self)
        panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        # header: title + mode badge + listening dot
        header = QHBoxLayout()
        title = QLabel("STELLA")
        title.setObjectName("title")
        self.mode_label = QLabel("COMMAND")
        self.mode_label.setObjectName("mode")
        self.dot = QLabel("●")  # filled circle
        self.dot.setObjectName("dot")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.dot)
        header.addWidget(self.mode_label)
        lay.addLayout(header)

        self.status_label = QLabel("starting...")
        self.status_label.setObjectName("status")
        self.transcript_label = QLabel("")
        self.transcript_label.setObjectName("transcript")
        self.transcript_label.setWordWrap(True)
        self.response_label = QLabel("")
        self.response_label.setObjectName("response")
        self.response_label.setWordWrap(True)
        for w in (self.status_label, self.transcript_label, self.response_label):
            lay.addWidget(w)

        self.setStyleSheet("""
            #panel { background: rgba(12,16,24,235); border: 1px solid rgba(120,150,200,90);
                     border-radius: 12px; }
            #title { color: #cfe3ff; font-size: 16px; font-weight: 700; letter-spacing: 2px; }
            #mode  { color: #39d98a; font-size: 12px; font-weight: 700; }
            #dot   { color: #444b5a; font-size: 12px; }
            #status { color: #7f8aa3; font-size: 11px; }
            #transcript { color: #e6ebf5; font-size: 13px; }
            #response { color: #9bd0ff; font-size: 13px; font-style: italic; }
        """)
        self._set_dot(False)
        self.set_mode("COMMAND")

    # -- positioning ------------------------------------------------------
    def position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        w, h, m = self.width(), self.height(), self._margin
        x = screen.left() + m if "left" in self._corner else screen.right() - w - m
        y = screen.top() + m if "top" in self._corner else screen.bottom() - h - m
        self.move(x, y)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.position()

    # -- slots (called on the GUI thread via signals) ---------------------
    def set_mode(self, mode: str):
        self.mode_label.setText(mode)
        color = _MODE_COLORS.get(mode, "#cccccc")
        self.mode_label.setStyleSheet(f"color: {color};")

    def _set_dot(self, listening: bool):
        self.dot.setStyleSheet(f"color: {'#ff5d5d' if listening else '#444b5a'};")

    def set_listening(self, on: bool):
        self._set_dot(on)
        if on:
            self.status_label.setText("listening...")

    def set_status(self, text: str):
        self.status_label.setText(text)

    def set_transcript(self, text: str):
        self.transcript_label.setText(f"“{text}”" if text else "")
        self.position()

    def set_response(self, intent: str, text: str):
        self.response_label.setText(f"STELLA: {text}" if text else "")
        self.status_label.setText(f"intent: {intent}")
        self.position()
