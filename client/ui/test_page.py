"""Test Console: type a phrase, see how STELLA would handle it. No keys pressed.

Classification goes through the server's /command (speak=false) so the result is
exactly what the voice path would do, including the clarify gate. Decision badge:
EXECUTE (confident command), SAY AGAIN (borderline, clarify=true), CHAT (no
command). Requests run on a worker thread so a down backend never freezes the UI.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from .theme import Theme
from .widgets import AsyncCall, RowsCard, key_chip, section_label

_MAX_HISTORY = 8


def _decision(res: dict) -> str:
    if res.get("intent") == "chat" or not res.get("intent"):
        return "chat"
    if res.get("clarify"):
        return "sayagain"
    return "execute"


class TestPage(QWidget):
    def __init__(self, api, theme: Theme):
        super().__init__()
        self.api = api
        self.theme = theme
        self._history: list[dict] = []
        self._call: AsyncCall | None = None

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
        title = QLabel("Test Console")
        title.setObjectName("pageTitle")
        sub = QLabel("Type a phrase to see how STELLA would handle it — no keys are pressed.")
        sub.setObjectName("pageSub")
        outer.addWidget(title)
        outer.addWidget(sub)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "Say something… e.g. “lower shields and raise engine power”")
        self.input.returnPressed.connect(self.run_test)
        row.addWidget(self.input, 1)
        self.btn = QPushButton("Classify")
        self.btn.setObjectName("accent")
        self.btn.clicked.connect(self.run_test)
        row.addWidget(self.btn)
        outer.addLayout(row)

        # Result card
        self.result = QFrame()
        self.result.setObjectName("card")
        rl = QHBoxLayout(self.result)
        rl.setContentsMargins(18, 18, 18, 18)
        rl.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(3)
        self.r_phrase = QLabel("")
        self.r_phrase.setObjectName("rowSub")
        self.r_name = QLabel("")
        self.r_name.setStyleSheet("font-size: 17px; font-weight: 600;")
        meta = QHBoxLayout()
        meta.setSpacing(8)
        self.r_key = key_chip("")
        self.r_ack = QLabel("")
        meta.addWidget(self.r_key)
        meta.addWidget(self.r_ack)
        meta.addStretch(1)
        left.addWidget(self.r_phrase)
        left.addWidget(self.r_name)
        left.addLayout(meta)
        rl.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.r_conf = QLabel("")
        self.r_conf.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.r_conf.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.r_bar_bg = QFrame()
        self.r_bar_bg.setFixedSize(110, 5)
        self.r_bar = QFrame(self.r_bar_bg)
        self.r_bar.setFixedHeight(5)
        self.r_badge = QLabel("")
        self.r_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.r_conf)
        right.addWidget(self.r_bar_bg)
        right.addWidget(self.r_badge)
        rl.addLayout(right)
        self.result.setVisible(False)
        outer.addWidget(self.result)

        outer.addWidget(section_label("Recent tests"))
        self.history_card = RowsCard()
        outer.addWidget(self.history_card)
        self._render_history()

    # -- styling helpers -----------------------------------------------------
    def _badge_css(self, decision: str) -> tuple[str, str, str]:
        t = self.theme
        return {
            "execute": ("EXECUTE", "rgba(48,209,88,0.14)", t.green),
            "sayagain": ("SAY AGAIN?", "rgba(255,159,10,0.16)", t.orange),
            "chat": ("CHAT", t.chip_bg, t.text2),
        }[decision]

    def _style_badge(self, lbl: QLabel, decision: str, small: bool = False) -> None:
        text, bg, fg = self._badge_css(decision)
        lbl.setText(text)
        size = "10px" if small else "11px"
        lbl.setStyleSheet(f"font-size: {size}; font-weight: 700; letter-spacing: 0.4px;"
                          f" padding: 3px 9px; border-radius: 7px;"
                          f" background: {bg}; color: {fg};")

    # -- run -------------------------------------------------------------------
    def run_test(self) -> None:
        text = self.input.text().strip()
        if not text or self._call is not None:
            return
        self.btn.setEnabled(False)
        self.btn.setText("…")
        self._call = AsyncCall(self, self.api.test_phrase, text)
        self._call.done.connect(lambda ok, res: self._finish(text, ok, res))
        self._call.start()

    def _finish(self, text: str, ok: bool, res) -> None:
        self._call = None
        self.btn.setEnabled(True)
        self.btn.setText("Classify")
        if not ok:
            self.r_phrase.setText(f"“{text}”")
            self.r_name.setText("Backend unreachable")
            self.r_key.setVisible(False)
            self.r_ack.setText(str(res))
            self.r_conf.setText("")
            self.r_badge.setVisible(False)
            self.result.setVisible(True)
            return
        decision = _decision(res)
        conf = float(res.get("confidence", 0.0))
        is_chat = decision == "chat"
        self.r_phrase.setText(f"“{text}”")
        self.r_name.setText("No command — chat" if is_chat
                            else res.get("intent", "?"))
        key = res.get("keybind")
        steps = res.get("sequence") or []
        key_text = (f"{len(steps)} steps · macro" if steps
                    else (f"{key} · hold" if res.get("hold") and key else key or ""))
        self.r_key.setVisible(bool(key_text))
        self.r_key.setText(key_text)
        ack = res.get("response_text", "")
        self.r_ack.setText(f"“{ack}”" if ack and not is_chat else "")
        self.r_ack.setStyleSheet(f"font-size: 12px; color: {self.theme.accent};")
        self.r_conf.setText(f"{conf:.2f}")
        self.r_bar_bg.setStyleSheet(
            f"background: {self.theme.chip_bg}; border-radius: 2px;")
        self.r_bar.setFixedWidth(max(2, int(110 * min(1.0, conf))))
        self.r_bar.setStyleSheet(f"background: {self.theme.accent}; border-radius: 2px;")
        self.r_badge.setVisible(True)
        self._style_badge(self.r_badge, decision)
        self.result.setVisible(True)
        self._history.insert(0, {"q": text, "name": self.r_name.text(),
                                 "conf": conf, "decision": decision})
        del self._history[_MAX_HISTORY:]
        self._render_history()

    def _render_history(self) -> None:
        self.history_card.clear()
        if not self._history:
            empty = QLabel("Nothing tested yet. Try “max shields”.")
            empty.setObjectName("rowSub")
            empty.setContentsMargins(14, 12, 14, 12)
            self.history_card.add_row(empty)
            return
        for h in self._history:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(12)
            text = QVBoxLayout()
            text.setSpacing(1)
            q = QLabel(h["q"])
            q.setObjectName("rowTitle")
            name = QLabel(h["name"])
            name.setObjectName("rowSub")
            text.addWidget(q)
            text.addWidget(name)
            lay.addLayout(text, 1)
            conf = QLabel(f"{h['conf']:.2f}")
            conf.setObjectName("rowSub")
            lay.addWidget(conf)
            badge = QLabel()
            self._style_badge(badge, h["decision"], small=True)
            lay.addWidget(badge)
            self.history_card.add_row(row)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._render_history()
        if self.result.isVisible() and self.r_badge.isVisible():
            self.r_bar_bg.setStyleSheet(f"background: {theme.chip_bg}; border-radius: 2px;")
            self.r_bar.setStyleSheet(f"background: {theme.accent}; border-radius: 2px;")
