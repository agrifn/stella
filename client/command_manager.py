"""STELLA Command & Voice Manager - the PyQt6 GUI over the server's HTTP API.

Visual structure comes from a Claude Design handoff (iOS-Settings-like): a header
with backend status and light/dark toggle, a sidebar with five sections -
Commands (grouped list + inline editor with a macro step builder), Test Console,
Voice, Settings, and Overlay HUD - all grouped cards with plain-language
captions. Theme tokens and reusable widgets live in manager_theme.py.

Everything on screen is wired to something real: commands edit through the
/commands CRUD (the server persists keybinds.json and rebuilds the classifier),
the test console classifies through /command with speak=false, voices go through
/voices, and Settings/Overlay write config/settings.json (the overlay reads it
at launch). Run:

    client\\.venv\\Scripts\\python -m client.command_manager
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QSettings, QSize, Qt
from PyQt6.QtGui import QColor, QPainter, QPolygon
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLayout, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .audio_player import AudioPlayer
from .command_sender import CommandSender
from .commands_api import CommandsAPI
from .config import CONFIG_DIR, REPO_ROOT, load_client_config
from .manager_theme import (
    Card, Segmented, Switch, apply_theme, group_label, nav_icon, repolish,
    row_widget, set_class, tokens,
)

# --- Qt key -> our keybind name (matches keybinds.json / pydirectinput aliases) ---
_SPECIAL = {
    Qt.Key.Key_Period: "period", Qt.Key.Key_Comma: "comma", Qt.Key.Key_Slash: "slash",
    Qt.Key.Key_Backslash: "backslash", Qt.Key.Key_Semicolon: "semicolon",
    Qt.Key.Key_Minus: "minus", Qt.Key.Key_Equal: "equals", Qt.Key.Key_QuoteLeft: "grave",
    Qt.Key.Key_BracketLeft: "leftbracket", Qt.Key.Key_BracketRight: "rightbracket",
    Qt.Key.Key_Tab: "tab", Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Space: "space", Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Escape: "esc", Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end", Qt.Key.Key_PageUp: "pageup",
    Qt.Key.Key_PageDown: "pagedown", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
}
_MOD_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta}


def keyname_from_event(e) -> str | None:
    k = Qt.Key(e.key())
    if k in _MOD_KEYS:
        return None
    mods = []
    m = e.modifiers()
    if m & Qt.KeyboardModifier.ControlModifier: mods.append("ctrl")
    if m & Qt.KeyboardModifier.AltModifier: mods.append("alt")
    if m & Qt.KeyboardModifier.ShiftModifier: mods.append("shift")
    if m & Qt.KeyboardModifier.MetaModifier: mods.append("win")

    name = None
    kv = e.key()
    if Qt.Key.Key_A.value <= kv <= Qt.Key.Key_Z.value:
        name = chr(kv).lower()
    elif Qt.Key.Key_0.value <= kv <= Qt.Key.Key_9.value:
        name = chr(kv)
    elif Qt.Key.Key_F1.value <= kv <= Qt.Key.Key_F12.value:
        name = "f" + str(kv - Qt.Key.Key_F1.value + 1)
    elif k in _SPECIAL:
        name = _SPECIAL[k]
    if not name:
        return None
    return "+".join(mods + [name])


class CaptureDialog(QDialog):
    """Captures one keypress and exposes it as `.result_key`."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capture key")
        self.result_key: str | None = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Press the key (or combo) to bind...\nEsc-only cancels."))
        self.setMinimumWidth(280)

    def keyPressEvent(self, e):  # noqa: N802 (Qt signature)
        if e.key() == Qt.Key.Key_Escape and not e.modifiers():
            self.reject()
            return
        name = keyname_from_event(e)
        if name:
            self.result_key = name
            self.accept()


def capture_key(parent) -> str | None:
    dlg = CaptureDialog(parent)
    return dlg.result_key if dlg.exec() else None


# --- settings.json access --------------------------------------------------------
class SettingsStore:
    """Read-modify-write access to config/settings.json. The GUI writes here and
    the overlay/engine read it at launch, so changes apply on the next start."""
    def __init__(self, path: Path):
        self._path = path
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def client(self) -> dict:
        return self.data.setdefault("client", {})

    def classifier(self) -> dict:
        return self.data.setdefault("classifier", {})

    def save(self) -> bool:
        try:
            self._path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            return True
        except OSError:
            return False


# --- command grouping -------------------------------------------------------------
# The registry has no category field; group by intent keywords so the list reads
# like the design (Power / Flight / Combat / Apps), with confirm-gated commands
# always surfacing under DANGEROUS regardless of name.
_CATEGORIES = [
    ("Flight", ("gear", "vtol", "decouple", "cruise", "quantum", "landing", "takeoff",
                "flight", "seat", "brake", "speed", "autoland", "dock")),
    ("Combat & Utility", ("flare", "noise", "gimbal", "target", "scan", "ping",
                          "light", "missile", "countermeasure", "fire", "weapon_group")),
    ("Apps", ("mobiglas", "mobi", "map", "comms", "chat", "scoreboard", "contract")),
    ("Power", ("power", "shields", "weapons", "engines", "thrusters")),
]
_CATEGORY_ORDER = ["Power", "Flight", "Combat & Utility", "Apps", "Other", "Dangerous"]


def categorize(cmd: dict) -> str:
    if cmd.get("confirm_required"):
        return "Dangerous"
    intent = cmd.get("intent", "").lower()
    for label, words in _CATEGORIES:
        if any(w in intent for w in words):
            return label
    return "Other"


def pretty(intent: str) -> str:
    return intent.replace("_", " ").title()


def key_label(cmd: dict) -> str:
    if cmd.get("sequence"):
        return f"{len(cmd['sequence'])} steps · macro"
    return (cmd.get("key") or "—") + (" · hold" if cmd.get("hold") else "")


# --- tiny flow layout for the phrase chips (Qt ships none) ------------------------
class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item): self._items.append(item)  # noqa: N802
    def count(self): return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None  # noqa: N802
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None  # noqa: N802
    def expandingDirections(self): return Qt.Orientation(0)  # noqa: N802
    def hasHeightForWidth(self): return True  # noqa: N802
    def heightForWidth(self, w): return self._do_layout(QRect(0, 0, w, 0), True)  # noqa: N802
    def sizeHint(self): return self.minimumSize()  # noqa: N802

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def _do_layout(self, rect: QRect, test: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() + 1 and line_h > 0:
                x = rect.x()
                y += line_h + self._space
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._space
            line_h = max(line_h, h)
        return y + line_h - rect.y()


# --- section: Commands -------------------------------------------------------------
class CommandsSection(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.commands: list[dict] = []
        self.selected: str | None = None
        self.draft: dict | None = None  # unsaved new command

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(18)

        head = QHBoxLayout()
        head.setSpacing(14)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h1 = QLabel("Commands")
        set_class(h1, "h1")
        self.caption = QLabel("")
        set_class(self.caption, "caption")
        titles.addWidget(h1)
        titles.addWidget(self.caption)
        head.addLayout(titles, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands or phrases")
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(lambda _t: self.rebuild_list())
        head.addWidget(self.search)
        new_btn = QPushButton("＋ New Command")
        set_class(new_btn, "primary")
        new_btn.clicked.connect(self.new_command)
        head.addWidget(new_btn)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(20)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 0, 8, 0)
        self.list_lay.setSpacing(22)
        self.list_scroll.setWidget(self.list_host)
        body.addWidget(self.list_scroll, 1)

        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFixedWidth(346)
        body.addWidget(self.editor_scroll)
        outer.addLayout(body, 1)

    # -- data ------------------------------------------------------------
    def reload(self):
        try:
            self.commands = self.win.api.list()
        except Exception as e:  # noqa: BLE001
            self.win.status(f"server error: {e}")
            self.commands = []
        if self.selected and not self._find(self.selected):
            self.selected = self.commands[0]["intent"] if self.commands else None
        elif not self.selected and self.commands:
            self.selected = self.commands[0]["intent"]
        self.rebuild_list()
        self.rebuild_editor()

    def _find(self, intent: str) -> dict | None:
        return next((c for c in self.commands if c["intent"] == intent), None)

    def patch(self, intent: str, **fields):
        """Apply one edit immediately (the server persists + rebuilds the
        classifier), mirror it locally, and refresh the list labels."""
        try:
            self.win.api.update(intent, fields)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Update failed", str(e))
            return False
        cmd = self._find(intent)
        if cmd:
            cmd.update(fields)
        self.rebuild_list()
        self.win.status(f"saved: {intent}")
        return True

    # -- list ------------------------------------------------------------
    def rebuild_list(self):
        scroll_pos = self.list_scroll.verticalScrollBar().value()
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        q = self.search.text().strip().lower()

        def match(c):
            return (not q or q in c["intent"].lower()
                    or any(q in p.lower() for p in c.get("examples", []))
                    or q in (c.get("key") or "").lower())

        self.caption.setText(
            f"Everything STELLA can do — {len(self.commands)} in total. "
            "Select one to edit its phrases, keybind, and spoken reply.")
        groups: dict[str, list[dict]] = {}
        for c in self.commands:
            if match(c):
                groups.setdefault(categorize(c), []).append(c)
        shown = 0
        for cat in _CATEGORY_ORDER:
            rows = groups.get(cat)
            if not rows:
                continue
            shown += 1
            block = QVBoxLayout()
            block.setSpacing(7)
            wrap = QWidget()
            wrap.setLayout(block)
            block.addWidget(group_label(cat))
            card = Card()
            for c in rows:
                card.add_row(self._row(c))
            block.addWidget(card)
            self.list_lay.addWidget(wrap)
        if not shown:
            empty = Card()
            lbl = QLabel("No commands match your search.")
            set_class(lbl, "caption")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(0, 32, 0, 32)
            empty.add_row(lbl)
            self.list_lay.addWidget(empty)
        self.list_lay.addStretch(1)
        # Keep the user's place: an inline edit must not snap the list to the top.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.list_scroll.verticalScrollBar().setValue(scroll_pos))

    def _row(self, c: dict) -> QWidget:
        t = tokens()
        w = QFrame()
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        sel = c["intent"] == self.selected
        w.setStyleSheet(
            f"QFrame {{ background: {t['accentSoft'] if sel else 'transparent'}; }}"
            f"QFrame:hover {{ background: {t['hover'] if not sel else t['accentSoft']}; }}")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(pretty(c["intent"]))
        name.setStyleSheet("font-size: 13.5px; font-weight: 500; background: transparent;")
        sub_text = "   ".join(f"“{p}”" for p in c.get("examples", [])) or "No phrases yet"
        sub = QLabel(sub_text)
        set_class(sub, "caption2")
        sub.setStyleSheet("background: transparent;")
        sub.setMaximumWidth(520)
        sub.setWordWrap(False)
        text.addWidget(name)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        if c.get("confirm_required"):
            badge = QLabel("CONFIRM")
            set_class(badge, "confirmBadge")
            lay.addWidget(badge)
        chip = QLabel(key_label(c))
        set_class(chip, "chip")
        lay.addWidget(chip)
        arrow = QLabel("›")
        arrow.setStyleSheet(f"color: {t['text3']}; font-size: 14px; background: transparent;")
        lay.addWidget(arrow)

        def click(_e, intent=c["intent"]):
            self.draft = None
            self.selected = intent
            self.rebuild_list()
            self.rebuild_editor()
        w.mouseReleaseEvent = click
        return w

    # -- editor ----------------------------------------------------------
    def new_command(self):
        self.draft = {"intent": "", "key": None, "confirm_required": False,
                      "hold": False, "hold_duration": None, "sequence": [],
                      "description": "", "examples": [], "ack": ""}
        self.selected = None
        self.rebuild_list()
        self.rebuild_editor()

    def rebuild_editor(self):
        cmd = self.draft if self.draft is not None else (
            self._find(self.selected) if self.selected else None)
        if cmd is None:
            host = Card()
            lbl = QLabel("⌘\n\nSelect a command to edit it")
            set_class(lbl, "caption")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setContentsMargins(24, 40, 24, 40)
            host.add_row(lbl)
            self.editor_scroll.setWidget(host)
            return
        self.editor_scroll.setWidget(self._editor(cmd))

    def _editor(self, cmd: dict) -> QWidget:
        is_draft = self.draft is not None
        card = QFrame()
        set_class(card, "card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 16)
        lay.setSpacing(16)

        head = QHBoxLayout()
        title = QLabel("New Command" if is_draft else "Edit Command")
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        head.addWidget(title, 1)
        close = QPushButton("✕")
        set_class(close, "ghost")
        close.setFixedSize(24, 24)
        close.clicked.connect(self._close_editor)
        head.addWidget(close)
        lay.addLayout(head)

        def field(label: str) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(6)
            lbl = QLabel(label)
            set_class(lbl, "field")
            col.addWidget(lbl)
            lay.addLayout(col)
            return col

        # NAME: the intent id is the API key and cannot be renamed in place, so
        # it is only editable while drafting a new command.
        name_col = field("NAME")
        name = QLineEdit(cmd["intent"] if is_draft else pretty(cmd["intent"]))
        if is_draft:
            name.setPlaceholderText("e.g. shields_max (letters, digits, underscore)")
            name.textChanged.connect(lambda txt: cmd.update(intent=txt.strip()))
        else:
            name.setReadOnly(True)
            name.setToolTip(f"intent id: {cmd['intent']} (cannot be renamed - create a new command instead)")
        name_col.addWidget(name)

        desc_col = field("DESCRIPTION")
        desc = QLineEdit(cmd.get("description", ""))
        desc.setPlaceholderText("What it does")
        desc_col.addWidget(desc)
        desc.editingFinished.connect(
            lambda: cmd.update(description=desc.text().strip()) if is_draft
            else self.patch(cmd["intent"], description=desc.text().strip()))

        # ACTION: Single Key vs Macro, per the design's segmented switch.
        action_col = field("ACTION")
        is_macro = bool(cmd.get("sequence"))
        seg = Segmented([("single", "Single Key"), ("macro", "Macro")],
                        "macro" if is_macro else "single")
        action_col.addWidget(seg)

        def set_type(kind: str):
            if kind == "macro" and not cmd.get("sequence"):
                # Seed the first step from the current keybind (design behavior).
                step = {"key": cmd.get("key") or "—", "hold": bool(cmd.get("hold")),
                        "taps": 1, "delay": 0.15}
                self._apply(cmd, sequence=[step])
            elif kind == "single" and cmd.get("sequence"):
                self._apply(cmd, sequence=[])
            self.rebuild_editor()
        seg.changed.connect(set_type)

        if not is_macro:
            keyrow = QHBoxLayout()
            keyrow.setSpacing(8)
            keychip = QLabel(cmd.get("key") or "—")
            keychip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            keychip.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 13px; padding: 9px;"
                f" border: 1px solid {tokens()['sep']}; border-radius: 9px;"
                f" background: {tokens()['chipBg']};")
            cap = QPushButton("Capture")
            set_class(cap, "soft")

            def do_capture():
                k = capture_key(self)
                if k:
                    keychip.setText(k)
                    self._apply(cmd, key=k)
            cap.clicked.connect(do_capture)
            keyrow.addWidget(keychip, 1)
            keyrow.addWidget(cap)
            action_col.addLayout(keyrow)

            hold_box = Card()
            hold_sw = Switch(bool(cmd.get("hold")))
            hold_box.add_row(row_widget("Hold the key", None, hold_sw))
            ms = int(round((cmd.get("hold_duration") or 1.5) * 1000))
            slider_row = QWidget()
            srl = QVBoxLayout(slider_row)
            srl.setContentsMargins(12, 10, 12, 10)
            srl.setSpacing(6)
            top = QHBoxLayout()
            cap1 = QLabel("Hold duration")
            set_class(cap1, "caption2")
            ms_lbl = QLabel(f"{ms / 1000:.2f} s")
            set_class(ms_lbl, "caption2")
            top.addWidget(cap1)
            top.addStretch(1)
            top.addWidget(ms_lbl)
            srl.addLayout(top)
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setRange(100, 2000)
            sld.setSingleStep(50)
            sld.setValue(max(100, min(2000, ms)))
            sld.valueChanged.connect(lambda v: ms_lbl.setText(f"{v / 1000:.2f} s"))
            sld.sliderReleased.connect(
                lambda: self._apply(cmd, hold_duration=sld.value() / 1000))
            srl.addWidget(sld)
            hold_box.add_row(slider_row)
            slider_row.setVisible(bool(cmd.get("hold")))

            def toggle_hold(on: bool):
                slider_row.setVisible(on)
                self._apply(cmd, hold=on)
            hold_sw.toggled.connect(toggle_hold)
            action_col.addWidget(hold_box)
        else:
            action_col.addWidget(self._macro_editor(cmd))
            hint = QLabel("Steps press in order. “ms” is the pause before the next step; ↑ reorders.")
            set_class(hint, "caption3")
            hint.setWordWrap(True)
            action_col.addWidget(hint)

        confirm_box = Card()
        confirm_sw = Switch(bool(cmd.get("confirm_required")))
        confirm_sw.toggled.connect(lambda on: self._apply(cmd, confirm_required=on))
        confirm_box.add_row(row_widget("Ask before firing", "STELLA waits for a spoken “yes”", confirm_sw))
        lay.addWidget(confirm_box)

        phr_col = field("SPOKEN PHRASES")
        chips_host = QWidget()
        chips = FlowLayout(chips_host)
        for i, p in enumerate(cmd.get("examples", [])):
            chips.addWidget(self._phrase_chip(cmd, i, p))
        phr_col.addWidget(chips_host)
        addrow = QHBoxLayout()
        addrow.setSpacing(8)
        new_phrase = QLineEdit()
        new_phrase.setPlaceholderText("Add a phrase…")
        add_btn = QPushButton("Add")
        set_class(add_btn, "chipMuted")

        def add_phrase():
            p = new_phrase.text().strip()
            if not p:
                return
            self._apply(cmd, examples=[*cmd.get("examples", []), p])
            self.rebuild_editor()
        add_btn.clicked.connect(add_phrase)
        new_phrase.returnPressed.connect(add_phrase)
        addrow.addWidget(new_phrase, 1)
        addrow.addWidget(add_btn)
        phr_col.addLayout(addrow)

        ack_col = field("SPOKEN REPLY")
        ack = QLineEdit(cmd.get("ack", ""))
        ack.setPlaceholderText("What STELLA says back")
        ack.editingFinished.connect(
            lambda: cmd.update(ack=ack.text().strip()) if is_draft
            else self.patch(cmd["intent"], ack=ack.text().strip()))
        ack_col.addWidget(ack)

        if is_draft:
            create = QPushButton("Create Command")
            set_class(create, "primary")
            create.clicked.connect(self._create_draft)
            lay.addWidget(create)
        else:
            delete = QPushButton("Delete Command")
            set_class(delete, "danger")
            delete.clicked.connect(lambda: self._delete(cmd["intent"]))
            lay.addWidget(delete)

        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(card)
        wl.addStretch(1)
        return wrap

    def _macro_editor(self, cmd: dict) -> QWidget:
        """Ordered step list: capturable key chip, tap/hold pill, delay (ms)
        before the next step, ↑ reorder, ✕ remove, ＋ Add Step."""
        t = tokens()
        box = Card()
        seq = cmd.get("sequence", [])

        def upd(i: int, **p):
            steps = [dict(s) for s in seq]
            steps[i].update(p)
            self._apply(cmd, sequence=steps)
            self.rebuild_editor()

        for i, st in enumerate(seq):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(7)
            n = QLabel(str(i + 1))
            n.setStyleSheet(f"color: {t['text3']}; font-family: Consolas, monospace; font-size: 11px;")
            rl.addWidget(n)
            keyb = QPushButton(st.get("key") or "—")
            set_class(keyb, "keychip")

            def cap(_=False, i=i):
                k = capture_key(self)
                if k:
                    upd(i, key=k)
            keyb.clicked.connect(cap)
            rl.addWidget(keyb)
            hold = bool(st.get("hold"))
            pill = QPushButton("hold" if hold else "tap")
            pill.setStyleSheet(
                f"QPushButton {{ padding: 4px 9px; border-radius: 99px; font-size: 11px; font-weight: 600;"
                f" background: {t['accentSoft'] if hold else t['chipBg']};"
                f" color: {t['accent'] if hold else t['text2']}; }}")
            pill.clicked.connect(lambda _=False, i=i, h=hold: upd(i, hold=not h))
            rl.addWidget(pill)
            delay = QSpinBox()
            delay.setRange(0, 5000)
            delay.setSingleStep(50)
            delay.setValue(int(round(float(st.get("delay", 0.1) or 0) * 1000)))
            delay.setFixedWidth(72)
            delay.editingFinished.connect(
                lambda i=i, sb=delay: upd(i, delay=sb.value() / 1000))
            rl.addWidget(delay)
            ms = QLabel("ms")
            set_class(ms, "caption3")
            rl.addWidget(ms)
            rl.addStretch(1)
            up = QPushButton("↑")
            set_class(up, "ghost")

            def move_up(_=False, i=i):
                if i == 0:
                    return
                steps = [dict(s) for s in seq]
                steps[i - 1], steps[i] = steps[i], steps[i - 1]
                self._apply(cmd, sequence=steps)
                self.rebuild_editor()
            up.clicked.connect(move_up)
            rl.addWidget(up)
            rm = QPushButton("✕")
            set_class(rm, "ghost")

            def remove(_=False, i=i):
                steps = [dict(s) for j, s in enumerate(seq) if j != i]
                self._apply(cmd, sequence=steps)
                self.rebuild_editor()
            rm.clicked.connect(remove)
            rl.addWidget(rm)
            box.add_row(row)

        add = QPushButton("＋ Add Step")
        add.setStyleSheet(f"color: {t['accent']}; font-weight: 600; padding: 8px;")

        def add_step():
            steps = [*[dict(s) for s in seq],
                     {"key": "—", "hold": False, "taps": 1, "delay": 0.15}]
            self._apply(cmd, sequence=steps)
            self.rebuild_editor()
        add.clicked.connect(add_step)
        box.add_row(add)
        return box

    def _phrase_chip(self, cmd: dict, idx: int, text: str) -> QWidget:
        t = tokens()
        chip = QFrame()
        chip.setStyleSheet(f"QFrame {{ background: {t['chipBg']}; border-radius: 12px; }}")
        cl = QHBoxLayout(chip)
        cl.setContentsMargins(9, 4, 7, 4)
        cl.setSpacing(6)
        lbl = QLabel(f"“{text}”")
        lbl.setStyleSheet("font-size: 12px; background: transparent;")
        cl.addWidget(lbl)
        x = QPushButton("✕")
        x.setStyleSheet(f"color: {t['text3']}; font-size: 10px; padding: 0;")
        x.setFixedSize(14, 14)

        def remove():
            ex = [p for j, p in enumerate(cmd.get("examples", [])) if j != idx]
            self._apply(cmd, examples=ex)
            self.rebuild_editor()
        x.clicked.connect(remove)
        cl.addWidget(x)
        return chip

    def _apply(self, cmd: dict, **fields):
        """Route an edit to the right place: drafts stay local until Create."""
        if self.draft is not None:
            cmd.update(fields)
            self.rebuild_list()
        else:
            self.patch(cmd["intent"], **fields)

    def _create_draft(self):
        d = self.draft or {}
        intent = (d.get("intent") or "").strip().lower().replace(" ", "_")
        if not intent:
            QMessageBox.warning(self, "Missing", "Give the command a name first.")
            return
        d["intent"] = intent
        try:
            self.win.api.create(d)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Create failed", str(e))
            return
        self.draft = None
        self.selected = intent
        self.reload()
        self.win.status(f"created: {intent}")

    def _delete(self, intent: str):
        if QMessageBox.question(self, "Delete", f"Delete command '{intent}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.win.api.delete(intent)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Delete failed", str(e))
            return
        self.selected = None
        self.reload()

    def _close_editor(self):
        self.draft = None
        self.selected = None
        self.rebuild_list()
        self.rebuild_editor()


# --- section: Test Console ----------------------------------------------------------
class TestSection(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.history: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(0)
        h1 = QLabel("Test Console")
        set_class(h1, "h1")
        cap = QLabel("Type a phrase to see how STELLA would handle it — no keys are pressed.")
        set_class(cap, "caption")
        outer.addWidget(h1)
        outer.addWidget(cap)
        outer.addSpacing(18)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Say something… e.g. “lower shields and raise engine power”")
        self.input.returnPressed.connect(self.run)
        run = QPushButton("Classify")
        set_class(run, "primary")
        run.clicked.connect(self.run)
        bar.addWidget(self.input, 1)
        bar.addWidget(run)
        outer.addLayout(bar)
        outer.addSpacing(16)

        self.result_host = QVBoxLayout()
        outer.addLayout(self.result_host)
        outer.addSpacing(6)
        outer.addWidget(group_label("Recent tests"))
        outer.addSpacing(7)
        self.hist_host = QVBoxLayout()
        outer.addLayout(self.hist_host)
        outer.addStretch(1)
        self._render()

    @staticmethod
    def _decide(d: dict) -> str:
        actionable = bool(d.get("keybind") or d.get("sequence"))
        if not actionable:
            return "chat"
        return "sayagain" if d.get("clarify") else "execute"

    @staticmethod
    def _badge(decision: str) -> tuple[str, str, str]:
        t = tokens()
        if decision == "execute":
            return "Executed", "rgba(48,209,88,15%)", t["green"]
        if decision == "sayagain":
            return "“Say again?”", "rgba(255,159,10,15%)", t["orange"]
        return "→ Chat", t["chipBg"], t["text2"]

    def run(self):
        q = self.input.text().strip()
        if not q:
            return
        try:
            d = self.win.api.test_phrase(q)
        except Exception as e:  # noqa: BLE001
            self.win.status(f"test error: {e}")
            return
        decision = self._decide(d)
        entry = {
            "q": q,
            "name": pretty(d["intent"]) if decision != "chat" else "No command — chat",
            "key": "" if decision == "chat" else (d.get("keybind") or "macro") + (" · hold" if d.get("hold") else ""),
            "ack": d.get("response_text", "") if decision == "execute" else "",
            "conf": float(d.get("confidence", 0.0)),
            "decision": decision,
        }
        self.history.insert(0, entry)
        self.history = self.history[:8]
        self._render()

    def _render(self):
        for host in (self.result_host, self.hist_host):
            while host.count():
                it = host.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
        t = tokens()
        if self.history:
            L = self.history[0]
            card = QFrame()
            set_class(card, "card")
            cl = QHBoxLayout(card)
            cl.setContentsMargins(18, 18, 18, 18)
            cl.setSpacing(18)
            left = QVBoxLayout()
            left.setSpacing(3)
            said = QLabel(f"You said: “{L['q']}”")
            set_class(said, "caption2")
            name = QLabel(L["name"])
            name.setStyleSheet("font-size: 17px; font-weight: 600;")
            left.addWidget(said)
            left.addWidget(name)
            meta = QHBoxLayout()
            meta.setSpacing(8)
            if L["key"]:
                kc = QLabel(L["key"])
                set_class(kc, "chip")
                meta.addWidget(kc)
            if L["ack"]:
                ak = QLabel(f"STELLA replies “{L['ack']}”")
                ak.setStyleSheet(f"font-size: 12.5px; color: {t['accent']};")
                meta.addWidget(ak)
            meta.addStretch(1)
            left.addSpacing(6)
            left.addLayout(meta)
            cl.addLayout(left, 1)
            right = QVBoxLayout()
            right.setSpacing(6)
            pct = QLabel(f"{round(L['conf'] * 100)}%")
            pct.setStyleSheet("font-size: 22px; font-weight: 700;")
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)
            bar = QFrame()
            bar.setFixedSize(110, 5)
            bar.setStyleSheet(f"background: {t['chipBg']}; border-radius: 2px;")
            fill = QFrame(bar)
            fill.setGeometry(0, 0, max(4, int(110 * min(1.0, L["conf"]))), 5)
            fill.setStyleSheet(f"background: {t['accent']}; border-radius: 2px;")
            label, bg, color = self._badge(L["decision"])
            badge = QLabel(label)
            badge.setStyleSheet(
                f"font-size: 11px; font-weight: 700; padding: 4px 9px; border-radius: 7px;"
                f" background: {bg}; color: {color};")
            badge.setAlignment(Qt.AlignmentFlag.AlignRight)
            right.addWidget(pct)
            right.addWidget(bar, alignment=Qt.AlignmentFlag.AlignRight)
            right.addWidget(badge, alignment=Qt.AlignmentFlag.AlignRight)
            cl.addLayout(right)
            self.result_host.addWidget(card)
            self.result_host.addItem(_spacer(22))

        hist = Card()
        if not self.history:
            lbl = QLabel("No tests yet — try “max shields”.")
            set_class(lbl, "caption")
            lbl.setContentsMargins(14, 14, 14, 14)
            hist.add_row(lbl)
        for h in self.history:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(14, 10, 14, 10)
            rl.setSpacing(12)
            text = QVBoxLayout()
            text.setSpacing(1)
            q = QLabel(f"“{h['q']}”")
            q.setStyleSheet("font-size: 13px;")
            n = QLabel(h["name"])
            set_class(n, "caption2")
            text.addWidget(q)
            text.addWidget(n)
            rl.addLayout(text, 1)
            conf = QLabel(f"{round(h['conf'] * 100)}%")
            set_class(conf, "caption2")
            rl.addWidget(conf)
            label, bg, color = self._badge(h["decision"])
            b = QLabel(label)
            b.setStyleSheet(
                f"font-size: 10.5px; font-weight: 700; padding: 3px 8px; border-radius: 6px;"
                f" background: {bg}; color: {color};")
            rl.addWidget(b)
            hist.add_row(row)
        self.hist_host.addWidget(hist)


def _spacer(h: int):
    from PyQt6.QtWidgets import QSpacerItem
    return QSpacerItem(1, h, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)


# --- section: Voice -------------------------------------------------------------------
# A few known-good Piper voices offered for one-click download (the API downloads
# by name); anything else can still be added by exact name via "Add by name…".
_CATALOG = [
    ("en_GB-jenny_dioco-medium", "Jenny (dioco)", "en_GB · medium"),
    ("en_US-lessac-medium", "Lessac", "en_US · medium"),
    ("en_US-amy-medium", "Amy", "en_US · medium"),
    ("en_US-ryan-high", "Ryan", "en_US · high"),
    ("en_GB-alba-medium", "Alba", "en_GB · medium"),
    ("en_US-kristin-medium", "Kristin", "en_US · medium"),
]


class VoiceSection(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.active = ""
        self.available: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(0)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        h1 = QLabel("Voice")
        set_class(h1, "h1")
        cap = QLabel("The voice STELLA replies with. Download more, or import one shared by a friend.")
        set_class(cap, "caption")
        titles.addWidget(h1)
        titles.addWidget(cap)
        head.addLayout(titles, 1)
        imp = QPushButton("Import Voice…")
        set_class(imp, "chip")
        imp.clicked.connect(self.import_voice)
        head.addWidget(imp)
        addn = QPushButton("Add by name…")
        set_class(addn, "chip")
        addn.clicked.connect(self.add_by_name)
        head.addWidget(addn)
        outer.addLayout(head)
        outer.addSpacing(18)

        self.body = QVBoxLayout()
        outer.addLayout(self.body)
        outer.addStretch(1)
        self.note = QLabel("Voices are free Piper models and run entirely on this PC. Each carries its own license.")
        set_class(self.note, "caption3")
        outer.addWidget(self.note)

    def reload(self):
        try:
            v = self.win.api.voices()
            self.active = v.get("active", "")
            self.available = v.get("available", [])
        except Exception as e:  # noqa: BLE001
            self.win.status(f"voices error: {e}")
        self._render()

    def _render(self):
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        t = tokens()

        cur = QFrame()
        set_class(cur, "card")
        cl = QHBoxLayout(cur)
        cl.setContentsMargins(18, 18, 18, 18)
        cl.setSpacing(14)
        icon = QLabel("∿")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background: {t['accentSoft']}; color: {t['accent']};"
            f" border-radius: 24px; font-size: 22px;")
        cl.addWidget(icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        tag = QLabel("CURRENT VOICE")
        tag.setStyleSheet(f"font-size: 11px; font-weight: 600; letter-spacing: 0.6px; color: {t['accent']};")
        nm = QLabel(self.active or "—")
        nm.setStyleSheet("font-size: 16px; font-weight: 600;")
        meta = QLabel("Piper · local")
        set_class(meta, "caption2")
        text.addWidget(tag)
        text.addWidget(nm)
        text.addWidget(meta)
        cl.addLayout(text, 1)
        prev = QPushButton("Preview")
        set_class(prev, "primary")
        prev.clicked.connect(self.preview)
        cl.addWidget(prev)
        exp = QPushButton("Export .zip")
        set_class(exp, "chip")
        exp.clicked.connect(self.export_voice)
        cl.addWidget(exp)
        self.body.addWidget(cur)
        self.body.addItem(_spacer(22))

        self.body.addWidget(group_label("Library"))
        self.body.addItem(_spacer(7))
        lib = Card()
        names = {n for n in self.available}
        rows = [(n, n, "installed") for n in self.available if n != self.active]
        for vid, label, meta in _CATALOG:
            if vid not in names:
                rows.append((vid, label, meta))
        if not rows:
            lbl = QLabel("No other voices yet — use Get or Import.")
            set_class(lbl, "caption")
            lbl.setContentsMargins(14, 14, 14, 14)
            lib.add_row(lbl)
        for vid, label, meta in rows:
            installed = vid in names
            btn = QPushButton("Use" if installed else "Get")
            btn.setStyleSheet(
                f"QPushButton {{ padding: 5px 13px; border-radius: 99px; font-size: 12.5px; font-weight: 600;"
                f" background: {t['accentSoft'] if installed else t['chipBg']};"
                f" color: {t['accent'] if installed else t['text2']}; }}")
            if installed:
                btn.clicked.connect(lambda _=False, v=vid: self.use(v))
            else:
                btn.clicked.connect(lambda _=False, v=vid: self.download(v))
            lib.add_row(row_widget(label, meta, btn))
        self.body.addWidget(lib)

    def use(self, voice: str):
        try:
            self.win.api.set_voice(voice)
            self.win.status(f"voice set: {voice}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Set voice failed", str(e))
        self.reload()

    def download(self, voice: str):
        self.win.status(f"downloading {voice} …")
        QApplication.processEvents()
        try:
            self.win.api.download_voice(voice)
            self.win.status(f"added voice: {voice}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Download failed", str(e))
        self.reload()

    def add_by_name(self):
        name, ok = QInputDialog.getText(
            self, "Add voice",
            "Piper voice name (e.g. en_US-amy-medium).\n"
            "Browse: https://rhasspy.github.io/piper-samples/")
        if ok and name.strip():
            self.download(name.strip())

    def preview(self):
        try:
            audio = self.win.sender.speak(f"STELLA online. This is the {self.active} voice.", route="ack")
            self.win.player.play_b64(audio, blocking=False)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Preview failed", str(e))

    def export_voice(self):
        if not self.active:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export voice bundle", f"{self.active}-stella-voice.zip", "Voice bundle (*.zip)")
        if not dest:
            return
        try:
            self.win.api.export_voice(self.active, dest)
            self.win.status(f"exported {self.active} -> {dest}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))

    def import_voice(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import voice bundle", "", "Voice bundle (*.zip)")
        if not path:
            return
        self.win.status("importing voice …")
        QApplication.processEvents()
        try:
            res = self.win.api.import_voice(path)
            self.win.status(f"imported voice (active: {res.get('active', '?')})")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(e))
        self.reload()


# --- section: Settings ------------------------------------------------------------------
_PTT_CHOICES = ["right ctrl", "right shift", "scroll lock", "pause", "caps lock",
                "f13", "f14", "left alt", "menu"]
_WHISPER_MODELS = ["large-v3-turbo", "distil-large-v3", "medium.en", "small.en", "small"]


class SettingsSection(QWidget):
    """Every control writes config/settings.json immediately; STELLA applies it
    the next launch (the status bar says so on each save)."""
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.store = win.store
        c, clf = self.store.client(), self.store.classifier()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(30, 26, 30, 48)
        lay.setSpacing(0)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        h1 = QLabel("Settings")
        set_class(h1, "h1")
        cap = QLabel("How STELLA listens, hears, and connects. Changes apply the next time STELLA starts.")
        set_class(cap, "caption")
        lay.addWidget(h1)
        lay.addWidget(cap)
        lay.addSpacing(18)

        def group(title: str) -> Card:
            lay.addWidget(group_label(title))
            lay.addSpacing(7)
            card = Card()
            lay.addWidget(card)
            lay.addSpacing(22)
            return card

        # ACTIVATION
        act = group("Activation")
        self.ptt = QComboBox()
        self.ptt.setEditable(True)
        self.ptt.addItems(_PTT_CHOICES)
        self.ptt.setCurrentText(c.get("ptt_key", "right ctrl"))
        # activated = picked from the list; editingFinished = typed a custom key.
        # (currentTextChanged would write settings.json on every keystroke.)
        self.ptt.activated.connect(
            lambda _i: self._set("client", "ptt_key", self.ptt.currentText().strip() or "right ctrl"))
        self.ptt.lineEdit().editingFinished.connect(
            lambda: self._set("client", "ptt_key", self.ptt.currentText().strip() or "right ctrl"))
        act.add_row(row_widget("Push-to-talk key", "Hold to speak a command", self.ptt))

        wake_sw = Switch(bool(c.get("wake_word_enabled", False)))
        wake_sw.toggled.connect(lambda on: self._set("client", "wake_word_enabled", on))
        act.add_row(row_widget("Wake word “Stella”", "Hands-free — just say her name", wake_sw))

        sens_row, self.sens = self._slider_row(
            "Wake sensitivity", 5, 60, int(float(c.get("wake_word_threshold", 0.5)) * 100),
            lambda v: f"{v / 100:.2f}",
            lambda v: self._set("client", "wake_word_threshold", v / 100),
            "Fewer false triggers", "Catches “Stella” more often")
        act.add_row(sens_row)

        fu_sw = Switch(bool(c.get("follow_up_enabled", True)))
        fu_sw.toggled.connect(lambda on: self._set("client", "follow_up_enabled", on))
        act.add_row(row_widget("Follow-up listening",
                               "After a command, keep listening briefly for the next one", fu_sw))

        # MODE
        mode = group("Mode")
        mode_seg = Segmented([("COMMAND", "Command"), ("CHAT", "Chat")],
                             str(c.get("default_mode", "COMMAND")).upper())
        mode_seg.changed.connect(lambda v: self._set("client", "default_mode", v))
        mode.add_row(row_widget("Default mode",
                                "Command fires keybinds · Chat types your words in-game", mode_seg))
        self.mode_key = QLineEdit(c.get("mode_toggle_key", "ctrl+alt+m"))
        set_class(self.mode_key, "mono")
        self.mode_key.setFixedWidth(160)
        self.mode_key.editingFinished.connect(
            lambda: self._set("client", "mode_toggle_key", self.mode_key.text().strip() or "ctrl+alt+m"))
        mode.add_row(row_widget("Mode toggle hotkey", None, self.mode_key))

        # AUDIO
        audio = group("Audio")
        self.mic = QComboBox()
        self._fill_devices(self.mic, "input", c.get("input_device"))
        self.mic.currentIndexChanged.connect(
            lambda _i: self._set("client", "input_device", self.mic.currentData()))
        audio.add_row(row_widget("Microphone", None, self.mic))
        self.out = QComboBox()
        self._fill_devices(self.out, "output", c.get("output_device"))
        self.out.currentIndexChanged.connect(
            lambda _i: self._set("client", "output_device", self.out.currentData()))
        audio.add_row(row_widget("Output device", None, self.out))

        # RECOGNITION
        rec = group("Recognition")
        self.model = QComboBox()
        self.model.addItems(_WHISPER_MODELS)
        self.model.setCurrentText(c.get("whisper_model", "large-v3-turbo"))
        self.model.currentTextChanged.connect(
            lambda txt: self._set("client", "whisper_model", txt))
        rec.add_row(row_widget("Speech model", "Runs on your GPU", self.model))

        thr_row, self.thr = self._slider_row(
            "Command confidence threshold", 20, 85,
            int(float(clf.get("reject_threshold", 0.45)) * 100),
            lambda v: f"{v / 100:.2f}",
            self._set_threshold,
            "More commands fire", "Below this, words go to chat")
        rec.add_row(thr_row)

        nb_sw = Switch(bool(c.get("nbest_enabled", True)))
        nb_sw.toggled.connect(lambda on: self._set("client", "nbest_enabled", on))
        rec.add_row(row_widget("Second-guess unclear speech",
                               "Re-scores alternate transcripts when unsure", nb_sw))

        # The "Say again?" gate is the clarify band above the reject threshold;
        # off = clarify==reject (the band is empty, borderline matches just fire).
        say_on = float(clf.get("clarify_threshold", 0.55)) > float(clf.get("reject_threshold", 0.45))
        say_sw = Switch(say_on)
        say_sw.toggled.connect(self._set_say_again)
        rec.add_row(row_widget("“Say again?” prompts",
                               "Ask instead of guessing on borderline matches", say_sw))

        # SERVER
        srv = group("Server")
        self.url = QLineEdit(self.store.data.get("server_url", "http://127.0.0.1:8420"))
        set_class(self.url, "mono")
        self.url.setFixedWidth(220)
        self.url.editingFinished.connect(self._set_url)
        srv.add_row(row_widget("Backend URL", None, self.url))
        self.health_lbl = QLabel("Checking…")
        self.health_lbl.setStyleSheet("font-size: 13.5px;")
        check = QPushButton("Check now")
        set_class(check, "link")
        check.clicked.connect(self.check_health)
        srv.add_row(row_widget("", None, check, self.health_lbl))
        lay.addStretch(1)

    # -- helpers -----------------------------------------------------------
    def _slider_row(self, title, lo, hi, value, fmt, on_commit, left_hint, right_hint):
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(14, 11, 14, 11)
        wl.setSpacing(6)
        top = QHBoxLayout()
        lbl = QLabel(title)
        set_class(lbl, "caption2")
        val = QLabel(fmt(value))
        set_class(val, "caption2")
        top.addWidget(lbl)
        top.addStretch(1)
        top.addWidget(val)
        wl.addLayout(top)
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(lo, hi)
        sld.setValue(max(lo, min(hi, value)))
        sld.valueChanged.connect(lambda v: val.setText(fmt(v)))
        sld.sliderReleased.connect(lambda: on_commit(sld.value()))
        wl.addWidget(sld)
        hints = QHBoxLayout()
        h1 = QLabel(left_hint)
        set_class(h1, "caption3")
        h2 = QLabel(right_hint)
        set_class(h2, "caption3")
        hints.addWidget(h1)
        hints.addStretch(1)
        hints.addWidget(h2)
        wl.addLayout(hints)
        return w, sld

    @staticmethod
    def _fill_devices(combo: QComboBox, kind: str, current):
        combo.addItem("System default", None)
        try:
            import sounddevice as sd  # lazy: device enumeration is best-effort
            field = "max_input_channels" if kind == "input" else "max_output_channels"
            for i, d in enumerate(sd.query_devices()):
                if d.get(field, 0) > 0:
                    combo.addItem(f"{i}: {d.get('name', '?')}", i)
        except Exception:  # noqa: BLE001
            pass
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _set(self, section: str, key: str, value):
        target = self.store.data if section == "root" else self.store.data.setdefault(section, {})
        if target.get(key) == value:
            return
        target[key] = value
        if self.store.save():
            self.win.status("settings saved — applies next launch")
        else:
            QMessageBox.critical(self, "Save failed", "Could not write settings.json")

    def _set_threshold(self, v: int):
        clf = self.store.classifier()
        reject = v / 100
        # Keep the clarify band's width when the floor moves (unless it was off).
        old_reject = float(clf.get("reject_threshold", 0.45))
        old_clarify = float(clf.get("clarify_threshold", 0.55))
        band = max(0.0, old_clarify - old_reject)
        clf["reject_threshold"] = reject
        clf["clarify_threshold"] = round(reject + band, 4)
        if self.store.save():
            self.win.status("settings saved — applies next launch")

    def _set_say_again(self, on: bool):
        clf = self.store.classifier()
        reject = float(clf.get("reject_threshold", 0.45))
        clf["clarify_threshold"] = round(reject + 0.10, 4) if on else reject
        if self.store.save():
            self.win.status("settings saved — applies next launch")

    def _set_url(self):
        self._set("root", "server_url", self.url.text().strip() or "http://127.0.0.1:8420")
        self.win.status("backend URL saved — restart the manager to use it")

    def check_health(self):
        t0 = time.time()
        try:
            h = self.win.api.health()
            n = len(self.win.api.list())
            ms = int((time.time() - t0) * 1000)
            self.health_lbl.setText(f"● Healthy · {ms} ms · {n} commands loaded")
            self.health_lbl.setStyleSheet(f"font-size: 13.5px; color: {tokens()['green']};")
            self.win.set_backend_ok(True)
        except Exception as e:  # noqa: BLE001
            self.health_lbl.setText(f"● Unreachable: {e}")
            self.health_lbl.setStyleSheet(f"font-size: 13.5px; color: {tokens()['red']};")
            self.win.set_backend_ok(False)


# --- section: Overlay HUD ------------------------------------------------------------
_HUD_STATES = {
    "idle": ("#98989D", "Idle", "", False),
    "listening": ("#FF453A", "Listening…", "", False),
    "command": ("#30D158", "“raise shields”", "Shields up.", True),
    "confirm": ("#FF9F0A", "“self destruct”", "Say “yes” to confirm", True),
}


class HudPreview(QFrame):
    """16:9 mock 'game capture' with the floating status pill, repositioned and
    faded live as the controls change. Painted stripes match the design."""
    def __init__(self):
        super().__init__()
        self.state = "command"
        self.corner = "bottom-right"
        self.opacity = 0.92
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.pill = QFrame(self)
        self.pill_lay = QHBoxLayout(self.pill)
        self.pill_lay.setContentsMargins(14, 9, 14, 9)
        self.pill_lay.setSpacing(10)
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        self.mode_lbl = QLabel("COMMAND")
        self.mode_lbl.setStyleSheet("font-size: 10px; font-weight: 700; letter-spacing: 1.2px; color: #7FD6FF; background: transparent;")
        self.line1 = QLabel()
        self.line1.setStyleSheet("font-size: 12.5px; color: rgba(255,255,255,235); background: transparent;")
        self.line2 = QLabel()
        self.line2.setStyleSheet("font-size: 12.5px; color: #7FD6FF; background: transparent;")
        self.pill_lay.addWidget(self.dot)
        self.pill_lay.addWidget(self.mode_lbl)
        self.pill_lay.addWidget(self.line1)
        self.pill_lay.addWidget(self.line2)
        self.refresh()

    def refresh(self):
        dot, l1, l2, reply = _HUD_STATES[self.state]
        self.dot.setStyleSheet(f"background: {dot}; border-radius: 4px;")
        self.line1.setText(l1)
        self.line2.setText(l2)
        self.line2.setVisible(reply)
        a = int(self.opacity * 200)  # pill base alpha 0.78 scaled by the slider
        self.pill.setStyleSheet(
            f"QFrame {{ background: rgba(18,20,26,{a}); border: 1px solid rgba(255,255,255,36);"
            f" border-radius: 13px; }}")
        self.pill.adjustSize()
        self._place()

    def _place(self):
        m = 16
        ps = self.pill.sizeHint()
        x = m if "left" in self.corner else self.width() - ps.width() - m
        y = m if "top" in self.corner else self.height() - ps.height() - m
        self.pill.setGeometry(x, y, ps.width(), ps.height())

    def resizeEvent(self, e):  # noqa: N802 (Qt signature)
        super().resizeEvent(e)
        self._place()

    def paintEvent(self, e):  # noqa: N802 (Qt signature)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#14161B"))
        p.drawRoundedRect(self.rect(), 14, 14)
        p.setClipRect(self.rect())
        p.setBrush(QColor("#171A20"))
        w, h = self.width(), self.height()
        x = -h
        while x < w:  # 45-degree stripes like the design's repeating gradient
            p.drawPolygon(QPolygon([
                QPoint(x, h), QPoint(x + h, 0), QPoint(x + h + 16, 0), QPoint(x + 16, h)]))
            x += 32
        p.setPen(QColor(255, 255, 255, 56))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "[ game capture ]")


class OverlaySection(QWidget):
    _CORNERS = {0: "top-left", 2: "top-right", 6: "bottom-left", 8: "bottom-right"}

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.store = win.store
        c = self.store.client()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(0)
        h1 = QLabel("Overlay HUD")
        set_class(h1, "h1")
        cap = QLabel("The little status pill that floats over the game. Click-through, always on top.")
        set_class(cap, "caption")
        outer.addWidget(h1)
        outer.addWidget(cap)
        outer.addSpacing(18)

        self.preview = HudPreview()
        self.preview.corner = c.get("overlay_corner", "bottom-right")
        self.preview.opacity = float(c.get("overlay_opacity", 0.85))
        self.preview.refresh()
        outer.addWidget(self.preview)
        outer.addSpacing(22)

        card = Card()
        states = Segmented([(k, k.capitalize()) for k in _HUD_STATES], "command")
        states.changed.connect(self._set_state)
        card.add_row(row_widget("Preview state", None, states))

        grid_host = QWidget()
        from PyQt6.QtWidgets import QGridLayout
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.cells: dict[int, QPushButton] = {}
        for i in range(9):
            b = QPushButton()
            b.setFixedSize(22, 22)
            if i in self._CORNERS:
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, i=i: self._set_corner(self._CORNERS[i]))
            else:
                b.setEnabled(False)
                b.setToolTip("The overlay docks to a corner")
            grid.addWidget(b, i // 3, i % 3)
            self.cells[i] = b
        self._style_cells()
        card.add_row(row_widget("Position on screen", "Pick a corner", grid_host))

        op_row = QWidget()
        ol = QVBoxLayout(op_row)
        ol.setContentsMargins(14, 11, 14, 11)
        ol.setSpacing(6)
        top = QHBoxLayout()
        lbl = QLabel("Opacity")
        set_class(lbl, "caption2")
        self.op_val = QLabel(f"{int(self.preview.opacity * 100)}%")
        set_class(self.op_val, "caption2")
        top.addWidget(lbl)
        top.addStretch(1)
        top.addWidget(self.op_val)
        ol.addLayout(top)
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(30, 100)
        sld.setValue(int(self.preview.opacity * 100))
        sld.valueChanged.connect(self._opacity_live)
        sld.sliderReleased.connect(lambda: self._save("overlay_opacity", sld.value() / 100))
        ol.addWidget(sld)
        card.add_row(op_row)

        # Click-through is how the overlay is built (not a setting) - shown as a
        # fact, with a permanently-on switch, so the design row stays truthful.
        ct = Switch(True)
        ct.setEnabled(False)
        card.add_row(row_widget("Click-through", "Mouse clicks pass straight to the game — always on", ct))

        ah = Switch(bool(c.get("overlay_auto_hide", True)))
        ah.toggled.connect(lambda on: self._save("overlay_auto_hide", on))
        card.add_row(row_widget("Auto-hide when idle", "Fades out after a few seconds of inactivity", ah))

        scale_row, _s = self._scale_row(c)
        card.add_row(scale_row)
        outer.addWidget(card)
        outer.addStretch(1)

    def _scale_row(self, c: dict):
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(14, 11, 14, 11)
        wl.setSpacing(6)
        top = QHBoxLayout()
        lbl = QLabel("HUD size")
        set_class(lbl, "caption2")
        val = QLabel(f"{int(float(c.get('overlay_scale', 0.8)) * 100)}%")
        set_class(val, "caption2")
        top.addWidget(lbl)
        top.addStretch(1)
        top.addWidget(val)
        wl.addLayout(top)
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setRange(50, 130)
        sld.setValue(int(float(c.get("overlay_scale", 0.8)) * 100))
        sld.valueChanged.connect(lambda v: val.setText(f"{v}%"))
        sld.sliderReleased.connect(lambda: self._save("overlay_scale", sld.value() / 100))
        wl.addWidget(sld)
        return w, sld

    def _style_cells(self):
        t = tokens()
        sel = self.preview.corner
        for i, b in self.cells.items():
            active = self._CORNERS.get(i) == sel
            b.setStyleSheet(
                f"QPushButton {{ border-radius: 6px;"
                f" background: {t['accent'] if active else t['chipBg']}; }}")

    def _set_state(self, state: str):
        self.preview.state = state
        self.preview.refresh()

    def _set_corner(self, corner: str):
        self.preview.corner = corner
        self.preview.refresh()
        self._style_cells()
        self._save("overlay_corner", corner)

    def _opacity_live(self, v: int):
        self.op_val.setText(f"{v}%")
        self.preview.opacity = v / 100
        self.preview.refresh()

    def _save(self, key: str, value):
        self.store.client()[key] = value
        if self.store.save():
            self.win.status("settings saved — applies next launch")

    def refresh_theme(self):
        self._style_cells()


# --- main window ----------------------------------------------------------------------
_NAV = [
    ("commands", "Commands", "⌘", "#0A84FF"),
    ("test", "Test Console", "⌖", "#34C759"),
    ("voices", "Voice", "∿", "#AF52DE"),
    ("settings", "Settings", "⚙", "#8E8E93"),
    ("overlay", "Overlay HUD", "▣", "#FF9F0A"),
]


class MainWindow(QMainWindow):
    def __init__(self, api: CommandsAPI, cfg):
        super().__init__()
        self.api = api
        self.cfg = cfg
        self.sender = CommandSender(cfg.server_url, cfg.api_token)
        self.player = AudioPlayer(cfg.output_device)
        self.store = SettingsStore(CONFIG_DIR / "settings.json")
        self.setWindowTitle("STELLA - Command & Voice Manager")
        self.resize(1180, 760)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        # -- header -------------------------------------------------------
        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(54)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 0, 18, 0)
        hl.setSpacing(12)
        logo = QLabel("✦")
        logo.setObjectName("Logo")
        logo.setFixedSize(27, 27)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand = QLabel("STELLA")
        brand.setObjectName("Brand")
        hl.addWidget(logo)
        hl.addWidget(brand)
        self.backend_chip = QLabel("● Checking backend…")
        hl.addWidget(self.backend_chip)
        hl.addStretch(1)
        self.theme_seg = Segmented([("light", "☀︎"), ("dark", "☾")], "light")
        self.theme_seg.changed.connect(self._set_theme)
        hl.addWidget(self.theme_seg)
        launch = QPushButton("Launch STELLA")
        set_class(launch, "primary")
        launch.clicked.connect(self.launch_stella)
        hl.addWidget(launch)
        rl.addWidget(header)

        # -- sidebar + stacked sections ------------------------------------
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(206)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(10, 14, 10, 14)
        sl.setSpacing(2)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label, sym, color in _NAV:
            b = QPushButton(label)
            set_class(b, "navrow")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(38)
            b.setStyleSheet("text-align: left; padding-left: 44px;")
            # The colored icon square floats over the button's left padding.
            ic = nav_icon(sym, color)
            ic.setParent(b)
            ic.setGeometry(10, 7, 24, 24)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            sl.addWidget(b)
            self.nav_buttons[key] = b
        sl.addStretch(1)
        ver = QLabel("v0.9 · runs fully local")
        set_class(ver, "caption3")
        ver.setContentsMargins(10, 8, 10, 0)
        sl.addWidget(ver)
        body.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.sections: dict[str, QWidget] = {
            "commands": CommandsSection(self),
            "test": TestSection(self),
            "voices": VoiceSection(self),
            "settings": SettingsSection(self),
            "overlay": OverlaySection(self),
        }
        for key, _l, _s, _c in _NAV:
            self.stack.addWidget(self.sections[key])
        body.addWidget(self.stack, 1)
        rl.addLayout(body, 1)

        self.statusBar().showMessage("Loading…")
        self.go("commands")
        self.sections["commands"].reload()
        self.sections["voices"].reload()
        self.refresh_health()
        self.sections["settings"].check_health()

    # -- plumbing -----------------------------------------------------------
    def status(self, msg: str):
        self.statusBar().showMessage(msg, 6000)

    def go(self, key: str):
        self.stack.setCurrentWidget(self.sections[key])
        for k, b in self.nav_buttons.items():
            b.setProperty("active", "true" if k == key else "false")
            repolish(b)

    def refresh_health(self):
        try:
            self.api.health()
            self.set_backend_ok(True)
        except Exception:  # noqa: BLE001
            self.set_backend_ok(False)

    def set_backend_ok(self, ok: bool):
        t = tokens()
        color = t["green"] if ok else t["red"]
        self.backend_chip.setText("● Backend healthy" if ok else "● Backend offline")
        self.backend_chip.setStyleSheet(
            f"padding: 4px 10px; border-radius: 11px; background: {t['chipBg']};"
            f" font-size: 11.5px; color: {color};")

    def launch_stella(self):
        bat = REPO_ROOT / "start_stella.bat"
        if not bat.exists():
            QMessageBox.warning(self, "Not found", f"{bat} not found")
            return
        try:
            os.startfile(str(bat))  # the launcher self-elevates via UAC
            self.status("Launching STELLA… accept the UAC prompt.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Launch failed", str(e))

    def _set_theme(self, name: str):
        apply_theme(QApplication.instance(), name)
        QSettings("STELLA", "Manager").setValue("theme", name)
        # Stylesheet-driven widgets restyle automatically; refresh the painted /
        # inline-styled ones (segmented controls, switches, list rows, chips).
        for seg in self.findChildren(Segmented):
            seg.refresh_theme()
        for sw in self.findChildren(Switch):
            sw.update()
        self.sections["commands"].rebuild_list()
        self.sections["commands"].rebuild_editor()
        self.sections["voices"]._render()  # noqa: SLF001 - same module family
        self.sections["test"]._render()  # noqa: SLF001
        self.sections["overlay"].refresh_theme()
        self.refresh_health()


def main():
    cfg = load_client_config()
    api = CommandsAPI(cfg.server_url, cfg.api_token)
    app = QApplication(sys.argv)
    theme = QSettings("STELLA", "Manager").value("theme", "light")
    apply_theme(app, theme)
    win = MainWindow(api, cfg)
    win.theme_seg.set_value(theme)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
