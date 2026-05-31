"""STELLA overlay app - the real client.

The Qt event loop + overlay run on the main thread; the StellaEngine voice loop
runs on a daemon thread and reports state through a Bridge QObject whose signals
update the overlay (cross-thread, queued). A tray icon provides Quit and a mode
toggle; the mode-toggle hotkey works globally.

    client\\.venv\\Scripts\\python -m client.app          # real (presses keys)
    client\\.venv\\Scripts\\python -m client.app --dry-run # log keys, don't press
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import signal
import sys
import threading

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import load_client_config
from .engine import StellaEngine
from .overlay import Overlay

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stella.app")


class Bridge(QObject):
    """Marshals engine events onto Qt signals delivered on the GUI thread."""
    status = pyqtSignal(str)
    mode = pyqtSignal(str)
    listening = pyqtSignal(bool)
    transcript = pyqtSignal(str)
    response = pyqtSignal(str, str)  # intent, text
    wake_state = pyqtSignal(bool)    # awake / asleep

    def on_event(self, name: str, data: dict):
        if name == "status":
            self.status.emit(data.get("text", ""))
        elif name == "mode":
            self.mode.emit(data.get("mode", ""))
        elif name == "wake_state":
            self.wake_state.emit(bool(data.get("active")))
        elif name == "listening":
            self.listening.emit(bool(data.get("on")))
        elif name == "transcript":
            self.transcript.emit(data.get("text", ""))
        elif name == "response":
            self.response.emit(data.get("intent", ""), data.get("text", ""))
        elif name == "chat_sent":
            self.status.emit(f"sent to chat: {data.get('text','')[:40]}")
        elif name == "await_confirm":
            self.status.emit(f"CONFIRM {data.get('intent','')}? hold PTT + say yes")
        elif name == "confirmed":
            self.status.emit(f"confirmed: {data.get('intent','')}")
        elif name == "cancelled":
            self.status.emit(f"cancelled: {data.get('intent','')}")
        elif name == "executed":
            self.status.emit(f"executed: {data.get('keybind','')}")
        elif name == "error":
            self.status.emit(data.get("text", "error"))


def _make_icon() -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#39d98a"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(4, 4, 24, 24)
    p.end()
    return QIcon(pm)


def _already_running() -> bool:
    """Single-instance guard: a named mutex prevents a second overlay (which would
    install a second PTT hook and double every keystroke)."""
    try:
        k = ctypes.windll.kernel32
        k.CreateMutexW(None, False, "Global\\StellaOverlayApp")
        return k.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="STELLA overlay client")
    ap.add_argument("--dry-run", action="store_true", help="log keybinds instead of pressing")
    args = ap.parse_args(argv)

    if _already_running():
        log.info("STELLA is already running; exiting this instance.")
        return

    cfg = load_client_config()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = Overlay(cfg.overlay_corner, cfg.overlay_opacity, cfg.overlay_margin)
    bridge = Bridge()
    bridge.status.connect(overlay.set_status)
    bridge.mode.connect(overlay.set_mode)
    bridge.listening.connect(overlay.set_listening)
    bridge.transcript.connect(overlay.set_transcript)
    bridge.response.connect(overlay.set_response)
    bridge.wake_state.connect(overlay.set_active)

    state = {"stop": False, "engine": None}

    def worker():
        import keyboard
        try:
            engine = StellaEngine(cfg, on_event=bridge.on_event,
                                  execute_keys=False if args.dry_run else None)
            state["engine"] = engine
            keyboard.add_hotkey(cfg.mode_toggle_key, engine.toggle_mode)
            keyboard.add_hotkey(cfg.wake_key, engine.toggle_wake)  # button to wake/sleep
            engine.warm()
            engine.run(should_stop=lambda: state["stop"])
        except Exception as e:  # noqa: BLE001 - surface fatal startup/loop errors
            log.exception("STELLA worker crashed")
            bridge.status.emit(f"FATAL: {e}")

    threading.Thread(target=worker, daemon=True).start()

    def quit_app():
        state["stop"] = True
        app.quit()

    tray = QSystemTrayIcon(_make_icon())
    tray.setToolTip("STELLA")
    menu = QMenu()
    act_wake = QAction("Wake / Sleep", menu)
    act_wake.triggered.connect(lambda: state["engine"] and state["engine"].toggle_wake())
    act_toggle = QAction("Toggle CHAT/COMMAND", menu)
    act_toggle.triggered.connect(lambda: state["engine"] and state["engine"].toggle_mode())
    act_quit = QAction("Quit STELLA", menu)
    act_quit.triggered.connect(quit_app)
    menu.addAction(act_wake)
    menu.addAction(act_toggle)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.show()

    overlay.show()

    # Let Python handle Ctrl+C and keep the event loop responsive to it.
    signal.signal(signal.SIGINT, lambda *_: quit_app())
    tick = QTimer()
    tick.start(200)
    tick.timeout.connect(lambda: None)

    log.info("STELLA overlay running. Tray icon -> Quit. Mode toggle: %s", cfg.mode_toggle_key)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
