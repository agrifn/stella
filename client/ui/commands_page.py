"""Commands page: searchable grouped command list + an inline edit panel.

Implements the design's Commands screen: groups derived by grouping.categorize,
rows with name / phrase preview / CONFIRM badge / keycap / chevron, and a sticky
330px editor on the right with the Single Key vs Macro segmented control. The
prototype mutated state live; over a real HTTP CRUD API edits apply on an
explicit 'Save Changes' instead, so a half-typed phrase never hits the server.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QScrollArea, QSlider, QSpinBox,
                             QVBoxLayout, QWidget)

from .grouping import group_commands
from .widgets import RowsCard, Segmented, Switch, key_chip, section_label
from .theme import Theme

# Reuse the existing Qt key-capture dialog so keybind names stay consistent.
_HOLD_DEFAULT_MS = 1500


def _key_label(cmd: dict) -> str:
    if cmd.get("sequence"):
        return f"{len(cmd['sequence'])} steps · macro"
    key = cmd.get("key") or "—"
    return f"{key} · hold" if cmd.get("hold") else key


class _CommandRow(QWidget):
    """One clickable row in the grouped list."""

    def __init__(self, page: "CommandsPage", cmd: dict):
        super().__init__()
        self._page = page
        self.intent = cmd["intent"]
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(cmd.get("description") or cmd["intent"])
        name.setObjectName("rowTitleStrong")
        phrases = cmd.get("examples", [])
        preview = " · ".join(f"“{p}”" for p in phrases[:2])
        sub = QLabel(f"{len(phrases)} phrases" + (f" · {preview}" if preview else ""))
        sub.setObjectName("rowSub")
        text.addWidget(name)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        if cmd.get("confirm_required"):
            badge = QLabel("CONFIRM")
            badge.setObjectName("confirmBadge")
            lay.addWidget(badge)
        lay.addWidget(key_chip(_key_label(cmd)))
        chev = QLabel("›")
        chev.setObjectName("chevron")
        lay.addWidget(chev)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        t = self._page.theme
        self.setStyleSheet(f"background: {t.accent_soft if on else 'transparent'};")

    def mousePressEvent(self, _e) -> None:  # noqa: N802 (Qt signature)
        self._page.select(self.intent)


class _MacroStepRow(QWidget):
    """One macro step: index, capturable key, tap/hold pill, taps, delay, ↑, ✕."""

    def __init__(self, editor: "_Editor", index: int, step: dict):
        super().__init__()
        self._editor = editor
        self.index = index
        t = editor.page.theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(7)
        n = QLabel(str(index + 1))
        n.setObjectName("hint")
        lay.addWidget(n)
        self.key_btn = QPushButton(step.get("key") or "key…")
        self.key_btn.setStyleSheet(
            f"QPushButton {{ font-family: Consolas, monospace; font-size: 11px;"
            f" background: {t.chip_bg}; border: 1px solid {t.sep}; border-radius: 7px;"
            f" padding: 4px 8px; }}")
        self.key_btn.clicked.connect(self._capture)
        lay.addWidget(self.key_btn)
        self.hold_btn = QPushButton()
        self.hold = bool(step.get("hold"))
        self.hold_btn.clicked.connect(self._toggle_hold)
        lay.addWidget(self.hold_btn)
        self.taps = QSpinBox()
        self.taps.setRange(1, 9)
        self.taps.setValue(int(step.get("taps", 1) or 1))
        self.taps.setPrefix("x")
        self.taps.setFixedWidth(46)
        self.taps.valueChanged.connect(editor.mark_dirty)
        lay.addWidget(self.taps)
        self.delay = QSpinBox()
        self.delay.setRange(0, 5000)
        self.delay.setSingleStep(50)
        self.delay.setValue(int(round(float(step.get("delay", 0.1) or 0) * 1000)))
        self.delay.setFixedWidth(64)
        self.delay.valueChanged.connect(editor.mark_dirty)
        lay.addWidget(self.delay)
        ms = QLabel("ms")
        ms.setObjectName("hint")
        lay.addWidget(ms)
        lay.addStretch(1)
        up = QPushButton("↑")
        up.setObjectName("ghost")
        up.setFixedWidth(26)
        up.clicked.connect(lambda: editor.move_step_up(self.index))
        lay.addWidget(up)
        rm = QPushButton("✕")
        rm.setObjectName("destructive")
        rm.setFixedWidth(26)
        rm.clicked.connect(lambda: editor.remove_step(self.index))
        lay.addWidget(rm)
        self._style_hold()

    def _capture(self) -> None:
        from ..command_manager import CaptureDialog
        dlg = CaptureDialog(self)
        if dlg.exec() and dlg.result_key:
            self.key_btn.setText(dlg.result_key)
            self._editor.mark_dirty()

    def _toggle_hold(self) -> None:
        self.hold = not self.hold
        self._style_hold()
        self._editor.mark_dirty()

    def _style_hold(self) -> None:
        t = self._editor.page.theme
        if self.hold:
            self.hold_btn.setText("HOLD")
            self.hold_btn.setStyleSheet(
                f"QPushButton {{ background: {t.accent_soft}; color: {t.accent};"
                f" border-radius: 11px; padding: 4px 9px; font-size: 11px; font-weight: 600; }}")
        else:
            self.hold_btn.setText("TAP")
            self.hold_btn.setStyleSheet(
                f"QPushButton {{ background: {t.chip_bg}; color: {t.text2};"
                f" border-radius: 11px; padding: 4px 9px; font-size: 11px; font-weight: 600; }}")

    def data(self) -> dict:
        key = self.key_btn.text().strip()
        return {"key": "" if key == "key…" else key, "hold": self.hold,
                "taps": self.taps.value(), "delay": self.delay.value() / 1000.0}


class _Editor(QFrame):
    """The right-hand 'Edit Command' card."""

    def __init__(self, page: "CommandsPage"):
        super().__init__()
        self.page = page
        self.setObjectName("card")
        self.setFixedWidth(330)
        self._cmd: dict | None = None
        self._is_new = False
        self._steps: list[dict] = []
        self._dirty = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 13, 16, 13)
        self.title = QLabel("Edit Command")
        self.title.setObjectName("cardHeader")
        hl.addWidget(self.title, 1)
        close = QPushButton("✕")
        close.setObjectName("closeRound")
        close.clicked.connect(page.deselect)
        hl.addWidget(close)
        outer.addWidget(head)
        sep = QFrame()
        sep.setObjectName("rowSep")
        outer.addWidget(sep)

        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(16, 16, 16, 16)
        self._body.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        def field_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("fieldLabel")
            return lbl

        self._body.addWidget(field_label("INTENT ID"))
        self.intent = QLineEdit()
        self.intent.setPlaceholderText("e.g. shields_max (letters, digits, underscore)")
        self._body.addWidget(self.intent)

        self._body.addWidget(field_label("NAME"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("What it does, e.g. Shields to Max")
        self.name.textEdited.connect(self.mark_dirty)
        self._body.addWidget(self.name)

        self._body.addWidget(field_label("ACTION"))
        self.action_seg = Segmented(page.theme, [("single", "Single Key"), ("macro", "Macro")])
        self.action_seg.changed.connect(self._switch_action)
        self._body.addWidget(self.action_seg)

        # Single-key editor
        self.single_box = QWidget()
        sb = QVBoxLayout(self.single_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(10)
        key_row = QHBoxLayout()
        self.key_display = QLabel("—")
        self.key_display.setObjectName("keyChip")
        self.key_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.key_display.setStyleSheet("padding: 9px; font-size: 13px;")
        key_row.addWidget(self.key_display, 1)
        cap = QPushButton("Capture")
        cap.setObjectName("accentSoft")
        cap.clicked.connect(self._capture_key)
        key_row.addWidget(cap)
        sb.addLayout(key_row)
        hold_card = RowsCard()
        self.hold_switch = Switch(page.theme)
        self.hold_switch.toggled.connect(self._hold_toggled)
        hold_card.add_row(self._row("Hold the key", "", self.hold_switch))
        self.hold_dur_row = QWidget()
        hd = QVBoxLayout(self.hold_dur_row)
        hd.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        lab = QLabel("Hold duration")
        lab.setObjectName("rowSub")
        self.hold_ms_label = QLabel("")
        self.hold_ms_label.setObjectName("rowSub")
        top.addWidget(lab, 1)
        top.addWidget(self.hold_ms_label)
        hd.addLayout(top)
        self.hold_ms = QSlider(Qt.Orientation.Horizontal)
        self.hold_ms.setRange(100, 2000)
        self.hold_ms.setSingleStep(50)
        self.hold_ms.valueChanged.connect(self._hold_ms_changed)
        hd.addWidget(self.hold_ms)
        hold_card.add_row(self.hold_dur_row)
        sb.addWidget(hold_card)
        self._body.addWidget(self.single_box)

        # Macro editor
        self.macro_box = QWidget()
        mb = QVBoxLayout(self.macro_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(7)
        self.macro_card = RowsCard()
        mb.addWidget(self.macro_card)
        hint = QLabel("Steps press in order. “ms” is the pause before the "
                      "next step; ↑ reorders.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        mb.addWidget(hint)
        self._body.addWidget(self.macro_box)

        confirm_card = RowsCard()
        self.confirm_switch = Switch(page.theme)
        self.confirm_switch.toggled.connect(self.mark_dirty)
        confirm_card.add_row(self._row("Ask before firing",
                                       "STELLA waits for a spoken “yes”",
                                       self.confirm_switch))
        self._body.addWidget(confirm_card)

        self._body.addWidget(field_label("SPOKEN PHRASES"))
        self.phrase_wrap = QWidget()
        self.phrase_lay = QVBoxLayout(self.phrase_wrap)
        self.phrase_lay.setContentsMargins(0, 0, 0, 0)
        self.phrase_lay.setSpacing(6)
        self._body.addWidget(self.phrase_wrap)
        add_row = QHBoxLayout()
        self.new_phrase = QLineEdit()
        self.new_phrase.setPlaceholderText("Add a phrase…")
        self.new_phrase.returnPressed.connect(self._add_phrase)
        add_row.addWidget(self.new_phrase, 1)
        addb = QPushButton("Add")
        addb.clicked.connect(self._add_phrase)
        add_row.addWidget(addb)
        self._body.addLayout(add_row)

        self._body.addWidget(field_label("SPOKEN REPLY"))
        self.ack = QLineEdit()
        self.ack.setPlaceholderText("What STELLA says back")
        self.ack.textEdited.connect(self.mark_dirty)
        self._body.addWidget(self.ack)

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setObjectName("accent")
        self.save_btn.clicked.connect(self._save)
        self._body.addWidget(self.save_btn)
        self.delete_btn = QPushButton("Delete Command")
        self.delete_btn.setObjectName("destructive")
        self.delete_btn.clicked.connect(self._delete)
        self._body.addWidget(self.delete_btn)
        self._body.addStretch(1)
        self._phrases: list[str] = []

    @staticmethod
    def _row(title: str, sub: str, control: QWidget) -> QWidget:
        from .widgets import setting_row
        return setting_row(title, sub, control)

    # -- state ------------------------------------------------------------
    def load(self, cmd: dict | None) -> None:
        """cmd=None starts a new command."""
        self._is_new = cmd is None
        self._cmd = cmd or {}
        c = self._cmd
        self.title.setText("New Command" if self._is_new else "Edit Command")
        self.intent.setText(c.get("intent", ""))
        self.intent.setReadOnly(not self._is_new)
        self.intent.setStyleSheet("color: gray;" if not self._is_new else "")
        self.name.setText(c.get("description", ""))
        self.key_display.setText(c.get("key") or "—")
        self.hold_switch.setChecked(bool(c.get("hold")))
        ms = int(round(float(c.get("hold_duration") or _HOLD_DEFAULT_MS / 1000) * 1000)) \
            if c.get("hold_duration") is not None else _HOLD_DEFAULT_MS
        self.hold_ms.setValue(max(100, min(2000, ms)))
        self._hold_ms_changed(self.hold_ms.value())
        self.confirm_switch.setChecked(bool(c.get("confirm_required")))
        self._phrases = list(c.get("examples", []))
        self._render_phrases()
        self.ack.setText(c.get("ack", ""))
        self._steps = [dict(s) for s in (c.get("sequence") or [])]
        self.action_seg.set_value("macro" if self._steps else "single")
        self._switch_action(self.action_seg.value())
        self._render_steps()
        self.delete_btn.setVisible(not self._is_new)
        self.save_btn.setText("Create Command" if self._is_new else "Save Changes")
        self._dirty = False
        self._refresh_save()

    def mark_dirty(self, *_a) -> None:
        self._dirty = True
        self._refresh_save()

    def _refresh_save(self) -> None:
        self.save_btn.setEnabled(self._dirty or self._is_new)

    # -- action type ------------------------------------------------------
    def _switch_action(self, value: str) -> None:
        self.single_box.setVisible(value == "single")
        self.macro_box.setVisible(value == "macro")
        if value == "macro" and not self._steps:
            # Seed the first step from the current keybind, like the prototype.
            key = self.key_display.text()
            self._steps = [{"key": "" if key == "—" else key,
                            "hold": self.hold_switch.isChecked(), "taps": 1, "delay": 0.1}]
            self._render_steps()
        self.mark_dirty()

    def _hold_toggled(self, on: bool) -> None:
        self.hold_dur_row.setVisible(on)
        self.mark_dirty()

    def _hold_ms_changed(self, v: int) -> None:
        self.hold_ms_label.setText(f"{v} ms")
        self.mark_dirty()

    def _capture_key(self) -> None:
        from ..command_manager import CaptureDialog
        dlg = CaptureDialog(self)
        if dlg.exec() and dlg.result_key:
            self.key_display.setText(dlg.result_key)
            self.mark_dirty()

    # -- macro steps ------------------------------------------------------
    def _collect_steps(self) -> list[dict]:
        rows = [self.macro_card._lay.itemAt(i).widget()
                for i in range(self.macro_card._lay.count())]
        return [r.data() for r in rows if isinstance(r, _MacroStepRow)]

    def _render_steps(self) -> None:
        self.macro_card.clear()
        for i, step in enumerate(self._steps):
            self.macro_card.add_row(_MacroStepRow(self, i, step))
        add = QPushButton("＋ Add Step")
        add.setObjectName("ghost")
        add.clicked.connect(self._add_step)
        self.macro_card.add_row(add)

    def _add_step(self) -> None:
        self._steps = self._collect_steps()
        self._steps.append({"key": "", "hold": False, "taps": 1, "delay": 0.1})
        self._render_steps()
        self.mark_dirty()

    def remove_step(self, index: int) -> None:
        self._steps = self._collect_steps()
        if 0 <= index < len(self._steps):
            self._steps.pop(index)
        self._render_steps()
        self.mark_dirty()

    def move_step_up(self, index: int) -> None:
        self._steps = self._collect_steps()
        if 1 <= index < len(self._steps):
            self._steps[index - 1], self._steps[index] = self._steps[index], self._steps[index - 1]
        self._render_steps()
        self.mark_dirty()

    # -- phrases ----------------------------------------------------------
    def _render_phrases(self) -> None:
        while self.phrase_lay.count():
            item = self.phrase_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        row: QHBoxLayout | None = None
        width = 0
        for i, p in enumerate(self._phrases):
            if row is None or width > 200:
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                row.addStretch(1)
                self.phrase_lay.addWidget(holder)
                width = 0
            chip = QPushButton(f"“{p}”  ✕")
            chip.setObjectName("phraseChip")
            chip.setToolTip("Remove this phrase")
            chip.clicked.connect(lambda _=False, idx=i: self._remove_phrase(idx))
            row.insertWidget(row.count() - 1, chip)
            width += len(p) * 7 + 30

    def _remove_phrase(self, index: int) -> None:
        if 0 <= index < len(self._phrases):
            self._phrases.pop(index)
            self._render_phrases()
            self.mark_dirty()

    def _add_phrase(self) -> None:
        text = self.new_phrase.text().strip()
        if text:
            self._phrases.append(text)
            self.new_phrase.clear()
            self._render_phrases()
            self.mark_dirty()

    # -- save / delete ------------------------------------------------------
    def data(self) -> dict:
        single = self.action_seg.value() == "single"
        key = self.key_display.text()
        steps = [] if single else [s for s in self._collect_steps() if s["key"]]
        return {
            "intent": self.intent.text().strip(),
            "key": (None if key == "—" else key) if single else None,
            "confirm_required": self.confirm_switch.isChecked(),
            "hold": self.hold_switch.isChecked() if single else False,
            "hold_duration": (self.hold_ms.value() / 1000.0
                              if single and self.hold_switch.isChecked() else None),
            "sequence": steps,
            "description": self.name.text().strip(),
            "examples": list(self._phrases),
            "ack": self.ack.text().strip(),
        }

    def _save(self) -> None:
        d = self.data()
        if not d["intent"]:
            QMessageBox.warning(self, "Missing", "Intent id is required.")
            return
        self.page.save_command(d, is_new=self._is_new)

    def _delete(self) -> None:
        if self._cmd and not self._is_new:
            self.page.delete_command(self._cmd["intent"])


class CommandsPage(QWidget):
    def __init__(self, api, theme: Theme):
        super().__init__()
        self.api = api
        self.theme = theme
        self._commands: list[dict] = []
        self._selected: str | None = None
        self._rows: dict[str, _CommandRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(18)
        head = QHBoxLayout()
        head.setSpacing(14)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        t = QLabel("Commands")
        t.setObjectName("pageTitle")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("pageSub")
        titles.addWidget(t)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search commands or phrases")
        self.search.setFixedWidth(230)
        self.search.textChanged.connect(self._render)
        head.addWidget(self.search)
        new = QPushButton("＋ New Command")
        new.setObjectName("accent")
        new.clicked.connect(self._new)
        head.addWidget(new)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(20)
        self.list_holder = QWidget()
        self.list_lay = QVBoxLayout(self.list_holder)
        self.list_lay.setContentsMargins(0, 0, 8, 0)
        self.list_lay.setSpacing(22)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_holder)
        body.addWidget(scroll, 1)
        right = QVBoxLayout()
        self.editor = _Editor(self)
        self.empty = QFrame()
        self.empty.setObjectName("card")
        self.empty.setFixedWidth(330)
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(24, 40, 24, 40)
        glyph = QLabel("⌘")
        glyph.setStyleSheet("font-size: 26px;")
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg = QLabel("Select a command to edit it")
        msg.setObjectName("rowSub")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(glyph)
        el.addWidget(msg)
        right.addWidget(self.editor)
        right.addWidget(self.empty)
        right.addStretch(1)
        body.addLayout(right)
        outer.addLayout(body, 1)
        self.deselect()

    # -- data --------------------------------------------------------------
    def set_commands(self, commands: list[dict]) -> None:
        self._commands = commands
        self.subtitle.setText(
            f"Everything STELLA can do — {len(commands)} commands. "
            f"Select one to edit its phrases, keybind, and spoken reply.")
        self._render()

    def _render(self, *_a) -> None:
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows = {}
        q = self.search.text().strip().lower()
        cmds = self._commands
        if q:
            cmds = [c for c in cmds
                    if q in c["intent"].lower() or q in c.get("description", "").lower()
                    or any(q in p.lower() for p in c.get("examples", []))]
        groups = group_commands(cmds)
        if not groups:
            empty = QFrame()
            empty.setObjectName("card")
            el = QVBoxLayout(empty)
            el.setContentsMargins(32, 32, 32, 32)
            lbl = QLabel("No commands match your search.")
            lbl.setObjectName("rowSub")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(lbl)
            self.list_lay.addWidget(empty)
        for label, rows in groups:
            holder = QWidget()
            gl = QVBoxLayout(holder)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(7)
            gl.addWidget(section_label(label))
            card = RowsCard()
            for c in rows:
                row = _CommandRow(self, c)
                self._rows[c["intent"]] = row
                card.add_row(row)
            gl.addWidget(card)
            self.list_lay.addWidget(holder)
        self.list_lay.addStretch(1)
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(True)

    # -- selection -----------------------------------------------------------
    def select(self, intent: str) -> None:
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(False)
        self._selected = intent
        if intent in self._rows:
            self._rows[intent].set_selected(True)
        cmd = next((c for c in self._commands if c["intent"] == intent), None)
        self.editor.load(cmd)
        self.editor.setVisible(True)
        self.empty.setVisible(False)

    def deselect(self) -> None:
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(False)
        self._selected = None
        self.editor.setVisible(False)
        self.empty.setVisible(True)

    def _new(self) -> None:
        if self._selected in self._rows:
            self._rows[self._selected].set_selected(False)
        self._selected = None
        self.editor.load(None)
        self.editor.setVisible(True)
        self.empty.setVisible(False)

    # -- CRUD (delegated to the window so status/toasts stay in one place) ---
    def save_command(self, data: dict, is_new: bool) -> None:
        self.window().save_command(data, is_new)

    def delete_command(self, intent: str) -> None:
        if QMessageBox.question(self, "Delete", f"Delete command '{intent}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.window().delete_command(intent)

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.editor.hold_switch.set_theme(theme)
        self.editor.confirm_switch.set_theme(theme)
        self.editor.action_seg.set_theme(theme)
        self._render()
