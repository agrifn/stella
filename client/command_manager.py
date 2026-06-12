"""STELLA Command Manager - the iOS-style GUI for commands, voice, and settings.

Implements the approved Claude Design prototype (design bundle 'iOS UI
Redesign'): a header with health chip, light/dark toggle, and Launch button; a
sidebar with five colored-icon sections (Commands, Test Console, Voice,
Settings, Overlay HUD); and grouped-card pages in client/ui/. Still a thin
client over the server's /commands CRUD API, so edits take effect immediately
(the server persists keybinds.json and the in-process classifier hot-reloads).

    client\\.venv\\Scripts\\python -m client.command_manager
"""
from __future__ import annotations

import json
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                             QMainWindow, QMessageBox, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from .audio_player import AudioPlayer
from .command_sender import CommandSender
from .commands_api import CommandsAPI
from .config import CONFIG_DIR, REPO_ROOT, load_client_config
from .ui.theme import THEMES, Theme, build_qss
from .ui.widgets import AsyncCall, Segmented

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
        lay.addWidget(QLabel("Press the key (or combo) to bind…\nEsc-only cancels."))
        self.setMinimumWidth(280)

    def keyPressEvent(self, e):  # noqa: N802 (Qt signature)
        if e.key() == Qt.Key.Key_Escape and not e.modifiers():
            self.reject()
            return
        name = keyname_from_event(e)
        if name:
            self.result_key = name
            self.accept()


# Sidebar entries: (page key, label, icon glyph, icon background color).
_NAV = [
    ("commands", "Commands", "⌘", "#0A84FF"),
    ("test", "Test Console", "⌖", "#34C759"),
    ("voice", "Voice", "∿", "#AF52DE"),
    ("settings", "Settings", "⚙", "#8E8E93"),
    ("overlay", "Overlay HUD", "▣", "#FF9F0A"),
]


class _NavItem(QWidget):
    def __init__(self, window: "MainWindow", key: str, label: str, glyph: str, color: str):
        super().__init__()
        self._window = window
        self.key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        icon = QLabel(glyph)
        icon.setFixedSize(24, 24)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"background: {color}; color: #fff; border-radius: 6px;"
                           " font-size: 13px;")
        self.label = QLabel(label)
        lay.addWidget(icon)
        lay.addWidget(self.label, 1)
        self.set_active(False)

    def set_active(self, on: bool) -> None:
        t = self._window.theme
        self.setStyleSheet(f"background: {t.accent_soft if on else 'transparent'};"
                           " border-radius: 9px;")
        self.label.setStyleSheet(
            f"color: {t.accent if on else t.text}; font-size: 13px; font-weight: 500;"
            " background: transparent;")

    def mousePressEvent(self, _e) -> None:  # noqa: N802 (Qt signature)
        self._window.go(self.key)


class MainWindow(QMainWindow):
    def __init__(self, api: CommandsAPI, cfg):
        super().__init__()
        self.api = api
        self.cfg = cfg
        self.sender = CommandSender(cfg.server_url, cfg.api_token)
        self.player = AudioPlayer(cfg.output_device)
        self.setWindowTitle("STELLA — Command & Voice Manager")
        self.resize(1180, 760)
        self._settings_path = CONFIG_DIR / "settings.json"
        self.theme: Theme = THEMES.get(self._load_theme_name(), THEMES["dark"])
        self._calls: list[AsyncCall] = []

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- Header ----
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(54)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 0, 18, 0)
        hl.setSpacing(12)
        self.logo = QLabel("✦")
        self.logo.setFixedSize(27, 27)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hl.addWidget(self.logo)
        title = QLabel("STELLA")
        title.setObjectName("logoTitle")
        hl.addWidget(title)
        self.health_chip = QLabel("●  Checking backend…")
        self.health_chip.setObjectName("healthChip")
        hl.addWidget(self.health_chip)
        self.toast_label = QLabel("")
        self.toast_label.setObjectName("rowSub")
        hl.addWidget(self.toast_label, 1, Qt.AlignmentFlag.AlignCenter)
        self.theme_seg = Segmented(self.theme, [("light", "☀"), ("dark", "☾")],
                                   current=self.theme.name)
        self.theme_seg.changed.connect(self.set_theme)
        hl.addWidget(self.theme_seg)
        launch = QPushButton("Launch STELLA")
        launch.setObjectName("accent")
        launch.clicked.connect(self.launch_stella)
        hl.addWidget(launch)
        outer.addWidget(header)

        # ---- Body: sidebar + pages ----
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(206)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(10, 14, 10, 14)
        sl.setSpacing(2)
        self._nav: dict[str, _NavItem] = {}
        for key, label, glyph, color in _NAV:
            item = _NavItem(self, key, label, glyph, color)
            self._nav[key] = item
            sl.addWidget(item)
        sl.addStretch(1)
        foot = QLabel("runs fully local")
        foot.setObjectName("sidebarFootnote")
        foot.setContentsMargins(10, 8, 10, 0)
        sl.addWidget(foot)
        body.addWidget(sidebar)

        from .ui.commands_page import CommandsPage
        from .ui.overlay_page import OverlayPage
        from .ui.settings_page import SettingsPage
        from .ui.test_page import TestPage
        from .ui.voice_page import VoicePage
        self.pages = QStackedWidget()
        self.page_commands = CommandsPage(self.api, self.theme)
        self.page_test = TestPage(self.api, self.theme)
        self.page_voice = VoicePage(self.api, self.sender, self.player, self.theme)
        self.page_settings = SettingsPage(self.api, self._settings_path, self.theme)
        self.page_overlay = OverlayPage(self._settings_path, self.theme)
        self._page_index = {}
        for key, page in [("commands", self.page_commands), ("test", self.page_test),
                          ("voice", self.page_voice), ("settings", self.page_settings),
                          ("overlay", self.page_overlay)]:
            self._page_index[key] = self.pages.addWidget(page)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        self._apply_theme()
        self.go("commands")
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast_label.setText(""))
        self.reload()
        self.reload_voices()
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._poll_health)
        self._health_timer.start(10000)
        self._poll_health()

    # -- navigation / theme -------------------------------------------------
    def go(self, key: str) -> None:
        for k, item in self._nav.items():
            item.set_active(k == key)
        self.pages.setCurrentIndex(self._page_index[key])

    def _load_theme_name(self) -> str:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return data.get("client", {}).get("gui_theme", "dark")
        except (OSError, ValueError):
            return "dark"

    def set_theme(self, name: str) -> None:
        self.theme = THEMES.get(name, THEMES["dark"])
        self._apply_theme()
        # Remember the choice, like the prototype does.
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8")) \
                if self._settings_path.exists() else {}
            data.setdefault("client", {})["gui_theme"] = name
            self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass

    def _apply_theme(self) -> None:
        t = self.theme
        QApplication.instance().setStyleSheet(build_qss(t))
        self.logo.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1,"
            " stop:0 #38B6E8, stop:1 #4A5FE0); color: #fff; border-radius: 8px;"
            " font-size: 13px;")
        self.theme_seg.set_theme(t)
        for item in self._nav.values():
            item.set_active(False)
        current = self.pages.currentIndex() if hasattr(self, "pages") else 0
        for key, idx in getattr(self, "_page_index", {}).items():
            if idx == current:
                self._nav[key].set_active(True)
        for page in (self.page_commands, self.page_test, self.page_voice,
                     self.page_settings, self.page_overlay):
            page.set_theme(t)

    def toast(self, text: str) -> None:
        """Transient header status, the GUI's lightweight feedback channel."""
        self.toast_label.setText(text)
        self._toast_timer.start(4000)

    # -- server data ----------------------------------------------------------
    def _track(self, call: AsyncCall) -> None:
        self._calls.append(call.start())
        call.done.connect(lambda *_: self._calls.remove(call)
                          if call in self._calls else None)

    def reload(self) -> None:
        call = AsyncCall(self, self.api.list)
        call.done.connect(self._loaded_commands)
        self._track(call)

    def _loaded_commands(self, ok: bool, res) -> None:
        if ok:
            self.page_commands.set_commands(res)
        else:
            self.toast(f"Backend error: {res}")

    def reload_voices(self) -> None:
        call = AsyncCall(self, self.api.voices)
        call.done.connect(self._loaded_voices)
        self._track(call)

    def _loaded_voices(self, ok: bool, res) -> None:
        if ok:
            self.page_voice.set_voices(res.get("active", ""), res.get("available", []))

    def _poll_health(self) -> None:
        call = AsyncCall(self, self.api.health)
        call.done.connect(self._health_done)
        self._track(call)

    def _health_done(self, ok: bool, res) -> None:
        t = self.theme
        if ok:
            self.health_chip.setText("●  Backend healthy")
            self.health_chip.setStyleSheet(
                f"background: {t.chip_bg}; border-radius: 12px; padding: 4px 10px;"
                f" font-size: 11px; color: {t.green};")
            self.page_voice.set_engine(res.get("tts_engine", "piper"))
        else:
            self.health_chip.setText("●  Backend offline")
            self.health_chip.setStyleSheet(
                f"background: {t.chip_bg}; border-radius: 12px; padding: 4px 10px;"
                f" font-size: 11px; color: {t.red};")

    # -- CRUD (called by CommandsPage) ----------------------------------------
    def save_command(self, data: dict, is_new: bool) -> None:
        try:
            if is_new:
                self.api.create(data)
            else:
                patch = {k: data[k] for k in ("key", "confirm_required", "hold",
                                              "hold_duration", "sequence",
                                              "description", "examples", "ack")
                         if k in data}
                self.api.update(data["intent"], patch)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.toast(f"Saved '{data['intent']}'")
        self.reload()

    def delete_command(self, intent: str) -> None:
        try:
            self.api.delete(intent)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Delete failed", str(e))
            return
        self.page_commands.deselect()
        self.toast(f"Deleted '{intent}'")
        self.reload()

    # -- launch ----------------------------------------------------------------
    def launch_stella(self) -> None:
        bat = REPO_ROOT / "start_stella.bat"
        if not bat.exists():
            QMessageBox.warning(self, "Not found", f"{bat} not found")
            return
        try:
            os.startfile(str(bat))  # runs the launcher, which self-elevates via UAC
            self.toast("Launching STELLA… accept the UAC prompt")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Launch failed", str(e))


def main():
    cfg = load_client_config()
    api = CommandsAPI(cfg.server_url, cfg.api_token)
    app = QApplication(sys.argv)
    win = MainWindow(api, cfg)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
