"""STELLA Command Manager - a PyQt6 GUI to add/edit/delete ship commands.

It is a thin client over the server's /commands CRUD API, so changes take effect
immediately (the server persists keybinds.json and regenerates the LLM prompt).
Run on the same network as the server:

    client\\.venv\\Scripts\\python -m client.command_manager
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .audio_player import AudioPlayer
from .command_sender import CommandSender
from .commands_api import CommandsAPI
from .config import REPO_ROOT, load_client_config
from .macros import macro_to_text, parse_macro_line

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


class CommandDialog(QDialog):
    """Add or edit a single command."""
    def __init__(self, parent=None, command: dict | None = None):
        super().__init__(parent)
        self.editing = command is not None
        self.setWindowTitle("Edit command" if self.editing else "Add command")
        self.setMinimumWidth(420)

        form = QFormLayout()
        self.intent = QLineEdit(command["intent"] if self.editing else "")
        if self.editing:
            self.intent.setReadOnly(True)
            self.intent.setStyleSheet("color: gray;")
        self.intent.setPlaceholderText("e.g. shields_max (letters, digits, underscore)")

        key_row = QHBoxLayout()
        self.key = QLineEdit(command.get("key", "") if self.editing else "")
        self.key.setPlaceholderText("e.g. 0, alt+y, period")
        capture = QPushButton("Capture key...")
        capture.clicked.connect(self._capture)
        key_row.addWidget(self.key)
        key_row.addWidget(capture)

        self.description = QLineEdit(command.get("description", "") if self.editing else "")
        self.description.setPlaceholderText("What it does (shown to the AI)")
        self.confirm = QCheckBox("Require spoken confirmation (dangerous)")
        self.hold = QCheckBox("Hold the key instead of tapping")
        if self.editing:
            self.confirm.setChecked(bool(command.get("confirm_required")))
            self.hold.setChecked(bool(command.get("hold")))
        self.examples = QPlainTextEdit("\n".join(command.get("examples", [])) if self.editing else "")
        self.examples.setPlaceholderText("Optional sample phrases, one per line")
        self.examples.setFixedHeight(80)

        self.macro = QPlainTextEdit(macro_to_text(command.get("sequence", [])) if self.editing else "")
        self.macro.setPlaceholderText(
            "Macro (overrides Key): one step per line. e.g.\n"
            "f7 hold\nf6 hold\nh x3\ntab /0.2")
        self.macro.setFixedHeight(80)

        form.addRow("Intent:", self.intent)
        form.addRow("Key:", self._wrap(key_row))
        form.addRow("Description:", self.description)
        form.addRow("", self.confirm)
        form.addRow("", self.hold)
        form.addRow("Examples:", self.examples)
        form.addRow("Macro:", self.macro)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        w.setLayout(layout)
        return w

    def _capture(self):
        dlg = CaptureDialog(self)
        if dlg.exec() and dlg.result_key:
            self.key.setText(dlg.result_key)

    def _on_ok(self):
        if not self.intent.text().strip():
            QMessageBox.warning(self, "Missing", "Intent name is required.")
            return
        self.accept()

    def data(self) -> dict:
        sequence = [s for s in (parse_macro_line(ln.strip())
                                for ln in self.macro.toPlainText().splitlines() if ln.strip()) if s]
        return {
            "intent": self.intent.text().strip(),
            "key": self.key.text().strip() or None,
            "confirm_required": self.confirm.isChecked(),
            "hold": self.hold.isChecked(),
            "sequence": sequence,
            "description": self.description.text().strip(),
            "examples": [ln.strip() for ln in self.examples.toPlainText().splitlines() if ln.strip()],
        }


class MainWindow(QMainWindow):
    COLS = ["Intent", "Key", "Confirm", "Hold", "Description"]

    def __init__(self, api: CommandsAPI, cfg):
        super().__init__()
        self.api = api
        self.cfg = cfg
        self.sender = CommandSender(cfg.server_url)
        self.player = AudioPlayer(cfg.output_device)
        self.setWindowTitle("STELLA - Command & Voice Manager")
        self.resize(760, 560)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # toolbar buttons
        bar = QHBoxLayout()
        for label, slot in [("Refresh", self.reload), ("Add", self.add),
                            ("Edit", self.edit), ("Delete", self.delete)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        launch = QPushButton("Launch STELLA")
        launch.setStyleSheet("font-weight: bold; color: #39d98a;")
        launch.clicked.connect(self.launch_stella)
        bar.addWidget(launch)
        root.addLayout(bar)

        # table
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(
            len(self.COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit)
        root.addWidget(self.table)

        # test phrase row
        test = QHBoxLayout()
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText("Type a phrase to test classification, e.g. 'shields to max'")
        self.phrase.returnPressed.connect(self.test_phrase)
        tb = QPushButton("Test phrase")
        tb.clicked.connect(self.test_phrase)
        self.test_result = QLabel("")
        test.addWidget(self.phrase, 1)
        test.addWidget(tb)
        root.addLayout(test)
        root.addWidget(self.test_result)

        # voice panel
        voice = QHBoxLayout()
        voice.addWidget(QLabel("Voice:"))
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(220)
        b_set = QPushButton("Set")
        b_set.clicked.connect(self.set_voice)
        b_test = QPushButton("Test")
        b_test.clicked.connect(self.test_voice)
        b_add = QPushButton("Add voice...")
        b_add.clicked.connect(self.add_voice)
        voice.addWidget(self.voice_combo, 1)
        for b in (b_set, b_test, b_add):
            voice.addWidget(b)
        root.addLayout(voice)

        self.statusBar().showMessage("Loading...")
        self.reload()
        self.reload_voices()

    # -- voices -----------------------------------------------------------
    def reload_voices(self):
        try:
            v = self.api.voices()
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"voices error: {e}")
            return
        self.voice_combo.clear()
        self.voice_combo.addItems(v.get("available", []))
        active = v.get("active", "")
        idx = self.voice_combo.findText(active)
        if idx >= 0:
            self.voice_combo.setCurrentIndex(idx)

    def set_voice(self):
        voice = self.voice_combo.currentText()
        if not voice:
            return
        try:
            self.api.set_voice(voice)
            self.statusBar().showMessage(f"voice set: {voice}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Set voice failed", str(e))

    def test_voice(self):
        voice = self.voice_combo.currentText()
        # set it first so the test uses the selected voice
        try:
            if voice:
                self.api.set_voice(voice)
            audio = self.sender.speak(f"STELLA online. This is the {voice} voice.")
            self.player.play_b64(audio, blocking=False)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Test failed", str(e))

    def add_voice(self):
        name, ok = QInputDialog.getText(
            self, "Add voice",
            "Piper voice name (e.g. en_US-amy-medium, en_GB-alba-medium).\n"
            "Browse: https://rhasspy.github.io/piper-samples/")
        if not ok or not name.strip():
            return
        self.statusBar().showMessage(f"downloading {name.strip()} ...")
        QApplication.processEvents()
        try:
            self.api.download_voice(name.strip())
            self.reload_voices()
            self.statusBar().showMessage(f"added voice: {name.strip()}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Add voice failed", str(e))

    # -- launch the overlay -----------------------------------------------
    def launch_stella(self):
        bat = REPO_ROOT / "start_stella.bat"
        if not bat.exists():
            QMessageBox.warning(self, "Not found", f"{bat} not found")
            return
        try:
            os.startfile(str(bat))  # runs the launcher, which self-elevates via UAC
            self.statusBar().showMessage("Launching STELLA... accept the UAC prompt.")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Launch failed", str(e))

    # -- data ops ---------------------------------------------------------
    def reload(self):
        try:
            cmds = self.api.list()
            health = self.api.health()
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"Server error: {e}")
            return
        self.table.setRowCount(0)
        for c in cmds:
            r = self.table.rowCount()
            self.table.insertRow(r)
            key_disp = c.get("key") or (f"macro({len(c['sequence'])})" if c.get("sequence") else "")
            vals = [c["intent"], key_disp, "yes" if c["confirm_required"] else "",
                    "yes" if c.get("hold") else "", c.get("description", "")]
            for col, v in enumerate(vals):
                self.table.setItem(r, col, QTableWidgetItem(str(v)))
        self.statusBar().showMessage(
            f"{len(cmds)} commands  |  {health.get('provider')}:{health.get('model')}  "
            f"llm={'up' if health.get('llm_reachable') else 'down'}")

    def _selected_intent(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).text()

    def add(self):
        dlg = CommandDialog(self)
        if not dlg.exec():
            return
        try:
            self.api.create(dlg.data())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Add failed", str(e))
            return
        self.reload()

    def edit(self):
        intent = self._selected_intent()
        if not intent:
            return
        current = next((c for c in self.api.list() if c["intent"] == intent), None)
        if not current:
            return
        dlg = CommandDialog(self, command=current)
        if not dlg.exec():
            return
        data = dlg.data()
        patch = {k: data[k] for k in ("key", "confirm_required", "hold", "sequence", "description", "examples")}
        try:
            self.api.update(intent, patch)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Update failed", str(e))
            return
        self.reload()

    def delete(self):
        intent = self._selected_intent()
        if not intent:
            return
        if QMessageBox.question(self, "Delete", f"Delete command '{intent}'?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.api.delete(intent)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Delete failed", str(e))
            return
        self.reload()

    def test_phrase(self):
        text = self.phrase.text().strip()
        if not text:
            return
        self.test_result.setText("...")
        try:
            d = self.api.test_phrase(text)
        except Exception as e:  # noqa: BLE001
            self.test_result.setText(f"error: {e}")
            return
        self.test_result.setText(
            f"intent=<b>{d['intent']}</b>  key={d['keybind']}  "
            f"confirm={d['confirm_required']}  STELLA: \"{d['response_text']}\"")


def main():
    cfg = load_client_config()
    api = CommandsAPI(cfg.server_url)
    app = QApplication(sys.argv)
    win = MainWindow(api, cfg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
