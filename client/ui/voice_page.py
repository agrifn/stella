"""Voice page: the voice STELLA replies with.

Current-voice card with Preview and Export, a library that mixes installed
voices (Use / Active) with a small curated catalog of downloadable Piper voices
(Download wires to /voices/download), and Import for a shared .zip bundle. When
a non-Piper TTS engine is active the page greys out and says why, mirroring the
old manager's behavior.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog,
                             QLabel, QMessageBox, QPushButton, QVBoxLayout,
                             QWidget)

from .theme import Theme
from .widgets import AsyncCall, RowsCard, section_label

# A few good free Piper voices to offer one-click; anything else comes in via
# 'Add by name…' (full catalog at rhasspy.github.io/piper-samples).
_CATALOG = [
    ("en_GB-alba-medium", "en_GB · medium · 63 MB"),
    ("en_GB-jenny_dioco-medium", "en_GB · medium · 63 MB"),
    ("en_US-lessac-medium", "en_US · medium · 63 MB"),
    ("en_US-amy-medium", "en_US · medium · 63 MB"),
    ("en_US-ryan-high", "en_US · high · 115 MB"),
    ("en_US-kristin-medium", "en_US · medium · 61 MB"),
]


def _meta(name: str) -> str:
    parts = name.split("-")
    return f"{parts[0]} · {parts[-1]}" if len(parts) >= 3 else "Piper voice"


class VoicePage(QWidget):
    def __init__(self, api, sender, player, theme: Theme):
        super().__init__()
        self.api = api
        self.sender = sender
        self.player = player
        self.theme = theme
        self._active = ""
        self._available: list[str] = []
        self._busy: AsyncCall | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 26, 30, 26)
        outer.setSpacing(16)
        outer.setAlignment(Qt.AlignmentFlag.AlignTop)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        t = QLabel("Voice")
        t.setObjectName("pageTitle")
        sub = QLabel("The voice STELLA replies with. Download more, or import one "
                     "shared by a friend.")
        sub.setObjectName("pageSub")
        titles.addWidget(t)
        titles.addWidget(sub)
        head.addLayout(titles, 1)
        imp = QPushButton("Import Voice…")
        imp.clicked.connect(self._import)
        head.addWidget(imp)
        addn = QPushButton("Add by name…")
        addn.clicked.connect(self._add_by_name)
        head.addWidget(addn)
        outer.addLayout(head)

        # Current voice card
        self.current_card = QFrame()
        self.current_card.setObjectName("card")
        cl = QHBoxLayout(self.current_card)
        cl.setContentsMargins(18, 18, 18, 18)
        cl.setSpacing(14)
        self.avatar = QLabel("∿")
        self.avatar.setFixedSize(48, 48)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(self.avatar)
        cur = QVBoxLayout()
        cur.setSpacing(2)
        cap = QLabel("CURRENT VOICE")
        cap.setObjectName("fieldLabel")
        self.cur_name = QLabel("—")
        self.cur_name.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.cur_meta = QLabel("")
        self.cur_meta.setObjectName("rowSub")
        cur.addWidget(cap)
        cur.addWidget(self.cur_name)
        cur.addWidget(self.cur_meta)
        cl.addLayout(cur, 1)
        self.preview_btn = QPushButton("▶ Preview")
        self.preview_btn.setObjectName("accent")
        self.preview_btn.clicked.connect(self._preview)
        cl.addWidget(self.preview_btn)
        self.export_btn = QPushButton("Export .zip")
        self.export_btn.clicked.connect(self._export)
        cl.addWidget(self.export_btn)
        outer.addWidget(self.current_card)

        outer.addWidget(section_label("Library"))
        self.library = RowsCard()
        outer.addWidget(self.library)
        self.note = QLabel("Voices are free Piper models and run entirely on this PC. "
                           "Each carries its own license.")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        self._render()

    # -- data ---------------------------------------------------------------
    def set_voices(self, active: str, available: list[str]) -> None:
        self._active = active
        self._available = available
        self._render()

    def set_engine(self, engine: str) -> None:
        """Grey the Piper catalog out when another TTS engine is active."""
        is_piper = "piper" in (engine or "")
        self.setEnabled(True)
        for w in (self.current_card, self.library, self.preview_btn, self.export_btn):
            w.setEnabled(is_piper)
        self.note.setText(
            "Voices are free Piper models and run entirely on this PC. Each carries "
            "its own license." if is_piper else
            f"TTS engine is '{engine}'. This Piper voice list is inactive; that "
            f"engine's voice is set on its own service, not here.")

    def _render(self) -> None:
        self.avatar.setStyleSheet(
            f"background: {self.theme.accent_soft}; color: {self.theme.accent};"
            f" border-radius: 24px; font-size: 22px;")
        self.cur_name.setText(self._active or "—")
        self.cur_meta.setText(_meta(self._active) if self._active else
                              "No voice active yet")
        self.library.clear()
        installed = set(self._available)
        for name in self._available:
            self._add_lib_row(name, _meta(name), "active" if name == self._active else "use")
        for name, meta in _CATALOG:
            if name not in installed:
                self._add_lib_row(name, meta, "download")

    def _add_lib_row(self, name: str, meta: str, kind: str) -> None:
        t = self.theme
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(1)
        n = QLabel(name)
        n.setObjectName("rowTitleStrong")
        m = QLabel(meta)
        m.setObjectName("rowSub")
        text.addWidget(n)
        text.addWidget(m)
        lay.addLayout(text, 1)
        btn = QPushButton({"active": "Active", "use": "Use", "download": "Download"}[kind])
        if kind == "active":
            btn.setEnabled(False)
            btn.setStyleSheet(f"background: {t.accent_soft}; color: {t.accent};"
                              f" border-radius: 13px; padding: 5px 13px;"
                              f" font-size: 12px; font-weight: 600;")
        else:
            btn.setStyleSheet(f"background: {t.chip_bg}; color: {t.text};"
                              f" border-radius: 13px; padding: 5px 13px;"
                              f" font-size: 12px; font-weight: 600;")
            if kind == "use":
                btn.clicked.connect(lambda _=False, v=name: self._use(v))
            else:
                btn.clicked.connect(lambda _=False, v=name, b=btn: self._download(v, b))
        lay.addWidget(btn)
        self.library.add_row(row)

    # -- actions --------------------------------------------------------------
    def _use(self, name: str) -> None:
        try:
            self.api.set_voice(name)
            self._active = name
            self._render()
            self.window().toast(f"Voice set: {name}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Set voice failed", str(e))

    def _download(self, name: str, btn: QPushButton) -> None:
        if self._busy is not None:
            return
        btn.setText("Downloading…")
        btn.setEnabled(False)
        self._busy = AsyncCall(self, self.api.download_voice, name)
        self._busy.done.connect(lambda ok, res: self._downloaded(name, ok, res))
        self._busy.start()

    def _downloaded(self, name: str, ok: bool, res) -> None:
        self._busy = None
        if ok:
            if name not in self._available:
                self._available.append(name)
            self.window().toast(f"Added voice: {name}")
        else:
            QMessageBox.critical(self, "Download failed", str(res))
        self._render()

    def _add_by_name(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Add voice",
            "Piper voice name (e.g. en_US-amy-medium).\n"
            "Browse: https://rhasspy.github.io/piper-samples/")
        if ok and name.strip():
            fake_btn = QPushButton()
            self._download(name.strip(), fake_btn)

    def _preview(self) -> None:
        if self._busy is not None or not self._active:
            return
        self.preview_btn.setText("…")
        self._busy = AsyncCall(self, self.sender.speak,
                               f"STELLA online. This is the {self._active} voice.")
        self._busy.done.connect(self._previewed)
        self._busy.start()

    def _previewed(self, ok: bool, res) -> None:
        self._busy = None
        self.preview_btn.setText("▶ Preview")
        if ok and res:
            try:
                self.player.play_b64(res, blocking=False)
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self, "Playback failed", str(e))
        elif not ok:
            QMessageBox.critical(self, "Preview failed", str(res))

    def _export(self) -> None:
        if not self._active:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export voice bundle", f"{self._active}-stella-voice.zip",
            "Voice bundle (*.zip)")
        if not dest:
            return
        try:
            self.api.export_voice(self._active, dest)
            self.window().toast(f"Exported {self._active}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(e))

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import voice bundle", "", "Voice bundle (*.zip)")
        if not path:
            return
        try:
            res = self.api.import_voice(path)
            self.window().toast(f"Imported voice (active: {res.get('active', '?')})")
            self.window().reload_voices()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Import failed", str(e))

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self._render()
