"""Settings page: how STELLA listens, hears, and connects.

The design's grouped sections (Activation, Mode, Audio, Recognition, Server)
wired to the real settings.json keys. Only controls that map to an existing
config key are shown; nothing is mocked. Edits buffer locally and write on Save
(preserving unknown keys in the file); most apply on the next STELLA launch,
which the footer says in plain language.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QSlider,
                             QVBoxLayout, QWidget)

from .theme import Theme
from .widgets import AsyncCall, RowsCard, Switch, key_chip, section_label, setting_row

_WHISPER_MODELS = ["large-v3-turbo", "distil-large-v3", "medium.en", "small", "base.en"]


class SettingsPage(QWidget):
    def __init__(self, api, settings_path: Path, theme: Theme):
        super().__init__()
        self.api = api
        self.theme = theme
        self._path = settings_path
        self._switches: list[Switch] = []
        self._health_call: AsyncCall | None = None

        try:
            self._data = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        c = self._data.get("client", {})
        clf = self._data.get("classifier", {})

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(30, 26, 30, 40)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        outer.addWidget(scroll)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        t = QLabel("Settings")
        t.setObjectName("pageTitle")
        sub = QLabel("How STELLA listens, hears, and connects.")
        sub.setObjectName("pageSub")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        save = QPushButton("Save Changes")
        save.setObjectName("accent")
        save.clicked.connect(self._save)
        head.addWidget(save, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addSpacing(10)

        def gap() -> None:
            lay.addSpacing(14)

        # ---- ACTIVATION ----
        lay.addWidget(section_label("Activation"))
        act = RowsCard()
        self.ptt = QLineEdit(c.get("ptt_key", "right ctrl"))
        self.ptt.setFixedWidth(150)
        act.add_row(setting_row("Push-to-talk key", "Hold to speak a command", self.ptt))
        self.wake_word = self._switch(bool(c.get("wake_word_enabled", False)))
        act.add_row(setting_row("Wake word “Stella”",
                                "Hands-free — just say her name (needs a wake model)",
                                self.wake_word))
        sens_row = QWidget()
        sl = QVBoxLayout(sens_row)
        sl.setContentsMargins(14, 11, 14, 11)
        top = QHBoxLayout()
        s_lab = QLabel("Wake sensitivity")
        s_lab.setObjectName("rowSub")
        self.sens_val = QLabel("")
        self.sens_val.setObjectName("rowSub")
        top.addWidget(s_lab, 1)
        top.addWidget(self.sens_val)
        sl.addLayout(top)
        self.sens = QSlider(Qt.Orientation.Horizontal)
        self.sens.setRange(5, 95)
        thr = float(c.get("wake_word_threshold", 0.5))
        self.sens.setValue(int(round((1.0 - thr) * 100)))
        self.sens.valueChanged.connect(
            lambda v: self.sens_val.setText(f"{v}%"))
        self.sens_val.setText(f"{self.sens.value()}%")
        sl.addWidget(self.sens)
        caps = QHBoxLayout()
        c1 = QLabel("Fewer false triggers")
        c1.setObjectName("hint")
        c2 = QLabel("Catches “Stella” more often")
        c2.setObjectName("hint")
        caps.addWidget(c1)
        caps.addStretch(1)
        caps.addWidget(c2)
        sl.addLayout(caps)
        act.add_row(sens_row)
        self.follow_up = self._switch(bool(c.get("follow_up_enabled", True)))
        win = float(c.get("follow_up_window", 1.2))
        act.add_row(setting_row("Follow-up listening",
                                f"After a command, keep listening for {win:g}s "
                                f"so you can chain the next one", self.follow_up))
        self.start_asleep = self._switch(bool(c.get("start_asleep", False)))
        act.add_row(setting_row("Start asleep",
                                "Launch dormant; wake with the hotkey", self.start_asleep))
        self.auto_sleep = self._switch(bool(c.get("auto_sleep_enabled", False)))
        act.add_row(setting_row("Auto-sleep when idle",
                                "Stops listening after a few idle minutes", self.auto_sleep))
        lay.addWidget(act)
        gap()

        # ---- MODE ----
        lay.addWidget(section_label("Mode"))
        mode = RowsCard()
        from .widgets import Segmented
        self.default_mode = Segmented(
            theme, [("COMMAND", "Command"), ("CHAT", "Chat")],
            current=str(c.get("default_mode", "COMMAND")).upper())
        mode.add_row(setting_row(
            "Default mode",
            "Command fires keybinds · Chat types your words in-game",
            self.default_mode))
        self.mode_key = QLineEdit(c.get("mode_toggle_key", "ctrl+alt+m"))
        self.mode_key.setFixedWidth(150)
        mode.add_row(setting_row("Mode toggle hotkey", "", self.mode_key))
        self.wake_key = QLineEdit(c.get("wake_key", "ctrl+alt+s"))
        self.wake_key.setFixedWidth(150)
        mode.add_row(setting_row("Wake/sleep hotkey", "", self.wake_key))
        lay.addWidget(mode)
        gap()

        # ---- AUDIO ----
        lay.addWidget(section_label("Audio"))
        audio = RowsCard()
        self.mic = QComboBox()
        self._fill_devices(self.mic, "input", c.get("input_device"))
        audio.add_row(setting_row("Microphone", "", self.mic))
        self.out = QComboBox()
        self._fill_devices(self.out, "output", c.get("output_device"))
        audio.add_row(setting_row("Output device", "", self.out))
        lay.addWidget(audio)
        gap()

        # ---- RECOGNITION ----
        lay.addWidget(section_label("Recognition"))
        rec = RowsCard()
        self.model = QComboBox()
        self.model.addItems(_WHISPER_MODELS)
        cur_model = c.get("whisper_model", "large-v3-turbo")
        if self.model.findText(cur_model) < 0:
            self.model.addItem(cur_model)
        self.model.setCurrentText(cur_model)
        rec.add_row(setting_row("Speech model", "Runs on your GPU", self.model))
        thr_row = QWidget()
        tl = QVBoxLayout(thr_row)
        tl.setContentsMargins(14, 11, 14, 11)
        ttop = QHBoxLayout()
        t_lab = QLabel("Command confidence threshold")
        t_lab.setObjectName("rowSub")
        self.thr_val = QLabel("")
        self.thr_val.setObjectName("rowSub")
        ttop.addWidget(t_lab, 1)
        ttop.addWidget(self.thr_val)
        tl.addLayout(ttop)
        self.thr = QSlider(Qt.Orientation.Horizontal)
        self.thr.setRange(20, 85)
        self.thr.setValue(int(round(float(clf.get("reject_threshold", 0.45)) * 100)))
        self.thr.valueChanged.connect(lambda v: self.thr_val.setText(f"0.{v:02d}"))
        self.thr_val.setText(f"0.{self.thr.value():02d}")
        tl.addWidget(self.thr)
        tcaps = QHBoxLayout()
        tc1 = QLabel("More commands fire")
        tc1.setObjectName("hint")
        tc2 = QLabel("Below this, words go to chat")
        tc2.setObjectName("hint")
        tcaps.addWidget(tc1)
        tcaps.addStretch(1)
        tcaps.addWidget(tc2)
        tl.addLayout(tcaps)
        rec.add_row(thr_row)
        self.nbest = self._switch(bool(c.get("nbest_enabled", True)))
        rec.add_row(setting_row("Second-guess unclear speech",
                                "Re-scores alternate transcripts when unsure", self.nbest))
        self.spec = self._switch(bool(c.get("speculative_stt", True)))
        rec.add_row(setting_row("Head-start transcription",
                                "Starts transcribing while you still hold push-to-talk",
                                self.spec))
        lay.addWidget(rec)
        gap()

        # ---- SERVER ----
        lay.addWidget(section_label("Server"))
        srv = RowsCard()
        self.url = QLineEdit(self._data.get("server_url", "http://127.0.0.1:8420"))
        self.url.setFixedWidth(220)
        srv.add_row(setting_row("Backend URL", "", self.url))
        health_row = QWidget()
        hl = QHBoxLayout(health_row)
        hl.setContentsMargins(14, 11, 14, 11)
        self.health_dot = QLabel("●")
        self.health_text = QLabel("Not checked yet")
        self.health_text.setObjectName("rowTitle")
        hl.addWidget(self.health_dot)
        hl.addWidget(self.health_text, 1)
        check = QPushButton("Check now")
        check.setObjectName("ghost")
        check.clicked.connect(self.check_health)
        hl.addWidget(check)
        srv.add_row(health_row)
        lay.addWidget(srv)

        note = QLabel("Hotkeys use the 'keyboard' library format (e.g. 'right ctrl', "
                      "'ctrl+alt+m'). Most changes apply the next time STELLA launches.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addSpacing(8)
        lay.addWidget(note)
        self._set_health(None)

    def _switch(self, checked: bool) -> Switch:
        s = Switch(self.theme, checked)
        self._switches.append(s)
        return s

    @staticmethod
    def _fill_devices(combo: QComboBox, kind: str, current) -> None:
        combo.addItem("System default", None)
        try:
            import sounddevice as sd
            field = "max_input_channels" if kind == "input" else "max_output_channels"
            for i, d in enumerate(sd.query_devices()):
                if d.get(field, 0) > 0:
                    combo.addItem(f"{i}: {d.get('name', '?')}", i)
        except Exception:  # noqa: BLE001 - device enumeration is best-effort
            pass
        idx = combo.findData(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    # -- health -----------------------------------------------------------
    def check_health(self) -> None:
        if self._health_call is not None:
            return
        self.health_text.setText("Checking…")
        self._health_call = AsyncCall(self, self.api.health)
        self._health_call.done.connect(self._health_done)
        self._health_call.start()

    def _health_done(self, ok: bool, res) -> None:
        self._health_call = None
        self._set_health(res if ok else None,
                         err=None if ok else str(res))

    def _set_health(self, health: dict | None, err: str | None = None) -> None:
        t = self.theme
        if health:
            self.health_dot.setStyleSheet(f"color: {t.green};")
            self.health_text.setText(
                f"Healthy · tts={health.get('tts_engine', '?')}")
        else:
            self.health_dot.setStyleSheet(f"color: {t.red if err else t.text3};")
            self.health_text.setText(err or "Not checked yet")

    # -- save ---------------------------------------------------------------
    def _save(self) -> None:
        c = self._data.setdefault("client", {})
        clf = self._data.setdefault("classifier", {})
        self._data["server_url"] = self.url.text().strip() or "http://127.0.0.1:8420"
        c["ptt_key"] = self.ptt.text().strip() or "right ctrl"
        c["mode_toggle_key"] = self.mode_key.text().strip() or "ctrl+alt+m"
        c["wake_key"] = self.wake_key.text().strip() or "ctrl+alt+s"
        c["default_mode"] = self.default_mode.value()
        c["input_device"] = self.mic.currentData()
        c["output_device"] = self.out.currentData()
        c["whisper_model"] = self.model.currentText()
        c["wake_word_enabled"] = self.wake_word.isChecked()
        c["wake_word_threshold"] = round(1.0 - self.sens.value() / 100.0, 2)
        c["follow_up_enabled"] = self.follow_up.isChecked()
        c["start_asleep"] = self.start_asleep.isChecked()
        c["auto_sleep_enabled"] = self.auto_sleep.isChecked()
        c["nbest_enabled"] = self.nbest.isChecked()
        c["speculative_stt"] = self.spec.isChecked()
        clf["reject_threshold"] = round(self.thr.value() / 100.0, 2)
        try:
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.window().toast("Settings saved — relaunch STELLA to apply")

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        for s in self._switches:
            s.set_theme(theme)
        self.default_mode.set_theme(theme)
