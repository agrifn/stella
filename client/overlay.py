"""Always-on-top status overlay for STELLA.

Frameless, translucent, click-through (input passes to the game underneath), and
docked to a screen corner. Shows the current mode, a listening indicator, the
last recognized speech, and STELLA's last response.

Note: over an EXCLUSIVE-fullscreen game the overlay may not be visible; run Star
Citizen in borderless/windowed for the HUD to show.
"""
from __future__ import annotations

from PyQt6.QtCore import QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
)

_MODE_COLORS = {"COMMAND": "#39d98a", "CHAT": "#f7b955"}


class Overlay(QWidget):
    def __init__(self, corner: str = "top-left", opacity: float = 0.85, margin: int = 24,
                 scale: float = 1.0, width: int = 340, auto_hide: bool = True,
                 hide_seconds: float = 4.0):
        super().__init__()
        self._corner = corner
        self._margin = margin
        self._opacity = opacity
        self._scale = max(0.5, min(2.0, scale))  # clamp to a sane range
        self._auto_hide = auto_hide
        self._hide_ms = max(500, int(hide_seconds * 1000))
        self._active = True       # awake (vs dimmed/SLEEP)
        self._listening = False   # keep the HUD up while capturing
        self._has_warn = False    # keep the HUD up while a warning is shown
        self._hide_gen = 0        # invalidates a pending fade-out when re-shown
        self._ready = False       # set True at end of __init__ so setters don't bump early

        def px(v: float) -> int:  # scale a base pixel value
            return max(1, round(v * self._scale))
        self._px = px

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # click-through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowOpacity(opacity)
        self.setFixedWidth(px(width))

        panel = QFrame(self)
        panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(px(16), px(12), px(16), px(12))
        lay.setSpacing(px(6))

        # header: title + mode badge + listening dot
        header = QHBoxLayout()
        self.title = QLabel("STELLA")
        self.title.setObjectName("title")
        self.state_label = QLabel("")
        self.state_label.setObjectName("state")
        self.mode_label = QLabel("COMMAND")
        self.mode_label.setObjectName("mode")
        self.dot = QLabel("●")  # filled circle
        self.dot.setObjectName("dot")
        header.addWidget(self.title)
        header.addWidget(self.state_label)
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
        # Persistent warning line (e.g. "not elevated"); hidden unless set.
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("warn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        for w in (self.status_label, self.transcript_label, self.response_label, self.warn_label):
            lay.addWidget(w)

        px = self._px
        self.setStyleSheet(f"""
            #panel {{ background: rgba(12,16,24,235); border: 1px solid rgba(120,150,200,90);
                     border-radius: {px(12)}px; }}
            #title {{ color: #cfe3ff; font-size: {px(16)}px; font-weight: 700; letter-spacing: 2px; }}
            #state {{ color: #6b7689; font-size: {px(11)}px; font-weight: 700; letter-spacing: 1px; }}
            #mode  {{ color: #39d98a; font-size: {px(12)}px; font-weight: 700; }}
            #dot   {{ color: #444b5a; font-size: {px(12)}px; }}
            #status {{ color: #7f8aa3; font-size: {px(11)}px; }}
            #transcript {{ color: #e6ebf5; font-size: {px(13)}px; }}
            #response {{ color: #9bd0ff; font-size: {px(13)}px; font-style: italic; }}
            #warn {{ color: #ff5d5d; font-size: {px(11)}px; font-weight: 700; }}
        """)
        self._set_dot(False)
        self.set_mode("COMMAND")

        # Steam-style auto-hide: fade in on activity, fade out after inactivity.
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(220)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timeout)
        self._ready = True

    # -- auto-hide --------------------------------------------------------
    def _target_opacity(self) -> float:
        return self._opacity if self._active else self._opacity * 0.55

    def _bump(self) -> None:
        """Activity happened: show the HUD (fading in) and restart the hide timer.
        With auto-hide off, the HUD just stays visible."""
        if not self._ready:
            return
        if not self._auto_hide:
            self.position()
            return
        self._hide_gen += 1  # cancel any pending fade-out
        self.position()
        if self.isHidden() or self.windowOpacity() < self._target_opacity():
            if self.isHidden():
                self.setWindowOpacity(0.0)
                self.show()
            self._fade.stop()
            self._fade.setStartValue(self.windowOpacity())
            self._fade.setEndValue(self._target_opacity())
            self._fade.start()
        self._hide_timer.start(self._hide_ms)

    def _on_hide_timeout(self) -> None:
        # Stay up while actively listening or while a warning is posted.
        if self._listening or self._has_warn:
            self._hide_timer.start(self._hide_ms)
            return
        self._hide_gen += 1
        gen = self._hide_gen
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        QTimer.singleShot(self._fade.duration() + 40, lambda: self._finish_hide(gen))

    def _finish_hide(self, gen: int) -> None:
        if gen == self._hide_gen and not self._listening and not self._has_warn:
            self.hide()

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
        self._bump()

    def set_active(self, active: bool):
        """Dim the panel and show a SLEEP badge when STELLA is dormant."""
        self._active = active
        self.title.setStyleSheet("color: #cfe3ff;" if active else "color: #6b7689;")
        self.state_label.setText("" if active else "· SLEEP")
        if self._auto_hide:
            self._bump()
        else:
            self.setWindowOpacity(self._target_opacity())
            self.position()

    def _set_dot(self, listening: bool):
        self.dot.setStyleSheet(f"color: {'#ff5d5d' if listening else '#444b5a'};")

    def set_listening(self, on: bool):
        self._listening = on
        self._set_dot(on)
        if on:
            self.status_label.setText("listening...")
        self._bump()

    def set_status(self, text: str):
        self.status_label.setText(text)
        self._bump()

    def set_warn(self, text: str):
        """Persistent warning line (red). Empty hides it."""
        self._has_warn = bool(text)
        self.warn_label.setText(text)
        self.warn_label.setVisible(bool(text))
        self._bump()

    def set_transcript(self, text: str):
        self.transcript_label.setText(f"“{text}”" if text else "")
        self._bump()

    def set_response(self, intent: str, text: str):
        self.response_label.setText(f"STELLA: {text}" if text else "")
        self.status_label.setText(f"intent: {intent}")
        self._bump()
