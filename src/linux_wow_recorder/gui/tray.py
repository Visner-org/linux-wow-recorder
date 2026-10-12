"""The tray icon and its menu."""

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QDeadlineTimer, QLineF, QLockFile, QRectF, Qt, QThread, QTimer, QUrl
from PySide6.QtDBus import QDBusConnection, QDBusInterface
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSystemTrayIcon, QVBoxLayout)

from .. import APP_NAME, APP_TITLE, config, logs, service, state
from ..clips import pending_recordings
from ..config import ConfigError
from . import desktop, model

POLL_MS = 1000
TRAY_WAIT_MS = 30_000   # at login the app can start a few seconds before the desktop's tray
RED, AMBER, GREY, LIGHT = QColor("#E5484D"), QColor("#E8A33D"), QColor("#7A818C"), QColor("#E6E8EC")


def draw_icon(kind: str) -> QIcon:
    """A ring with a dot: red while recording, amber while cutting, grey otherwise, and a
    warning badge when something needs attention."""
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        ring = LIGHT if kind in (model.RECORDING, model.ATTENTION) else GREY
        pen_w = max(1.5, size / 11)
        p.setPen(QPen(ring, pen_w))
        p.setBrush(Qt.NoBrush)
        m = pen_w / 2 + size * 0.06
        p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
        dot = {model.RECORDING: RED, model.PROCESSING: AMBER, model.ATTENTION: RED}.get(kind, GREY)
        if kind == model.OFF:
            dot = QColor(0, 0, 0, 0)
        p.setPen(Qt.NoPen)
        p.setBrush(dot)
        r = size * 0.24
        p.drawEllipse(QRectF(size / 2 - r, size / 2 - r, 2 * r, 2 * r))
        if kind == model.ATTENTION:
            b = size * 0.42
            p.setBrush(AMBER)
            p.drawEllipse(QRectF(size - b, 0, b, b))
            p.setPen(QPen(QColor("#15171C"), max(1.0, size / 20)))
            x = size - b / 2
            p.drawLine(QLineF(x, b * 0.22, x, b * 0.62))
        p.end()
        icon.addPixmap(pix)
    return icon


ICON_FILE = Path(__file__).with_name("icon.svg")
# Another launch of the app connects here to bring the window up (one app per user).
SERVER_NAME = f"{APP_NAME}-{os.getuid()}"


def app_icon() -> QIcon:
    """The app's own icon for windows and the taskbar (the tray icon shows the state)."""
    icon = QIcon(str(ICON_FILE))
    return icon if not icon.isNull() else draw_icon(model.RECORDING)


class LogWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE}: log")
        self.resize(900, 500)
        self.text = QPlainTextEdit(readOnly=True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(refresh)
        self.load()

    def load(self):
        text = logs.tail(logs.LOG_DIR / "recorder.log", 300)
        self.text.setPlainText(text or f"(empty: the service hasn't logged anything yet)\n\n"
                                       f"Logs are kept in {logs.LOG_DIR}")
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


class Tray:
    def __init__(self, app: QApplication):
        self.app = app
        self.icons = {k: draw_icon(k) for k in (model.IDLE, model.RECORDING, model.PROCESSING,
                                                model.ATTENTION, model.OFF)}
        self.tray = QSystemTrayIcon(self.icons[model.OFF])
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_click)
        self.log_window = None
        self.settings_window = None
        self.clips_window = None
        self.process = None            # a running `process` command
        self.told_about_tray = False   # the "still running in the tray" message, once per run
        self.service_state = ""
        self.status = None
        self.last_end = None           # last_session["end"] already seen, for notifications
        self.last_problem = None

        self.title = self.menu.addAction("")
        self.title.setEnabled(False)
        self.detail_actions: list[QAction] = []
        self.detail_anchor = self.menu.addSeparator()
        self.menu.addAction("Clips...", self.show_clips)
        self.menu.addAction("Open clips folder", lambda: self.open_dir("clips_dir"))
        self.menu.addAction("Open output folder", lambda: self.open_dir("output_dir"))
        self.last_actions: list[QAction] = []
        self.last_anchor = self.menu.addSeparator()
        self.cut_action = self.menu.addAction("Cut waiting recordings now", self.cut_now)
        self.service_action = self.menu.addAction("", self.toggle_service)
        self.menu.addSeparator()
        self.menu.addAction("Settings...", self.show_settings)
        self.menu.addAction("Show log", self.show_log)
        self.menu.addAction("Open log folder", self.open_log_dir)
        self.autostart_action = self.menu.addAction("Show this icon at login", self.toggle_autostart)
        self.autostart_action.setCheckable(True)
        self.menu.addSeparator()
        self.menu.addAction("Close tray icon (recording continues)", app.quit)
        self.menu.addAction("Quit (stop recording and close)", self.quit_all)

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.service_ticks = 0
        self.refresh()
        self.tray.show()

    # ── Updating ──

    def refresh(self):
        self.status = state.read()
        # systemctl is a process start; once every 5 s is plenty, or every tick with no daemon.
        if self.status is None or self.service_ticks % 5 == 0:
            self.service_state = service.state()
        self.service_ticks += 1
        view = model.describe(self.status, self.service_state)

        self.tray.setIcon(self.icons[view.icon])
        self.tray.setToolTip("\n".join([f"{APP_TITLE}: {view.title}", *view.details]))
        self.title.setText(view.title)
        self.title.setIcon(self.icons[view.icon])
        self.detail_actions = self._replace_lines(self.detail_actions, view.details,
                                                  self.detail_anchor)
        self.last_actions = self._replace_lines(self.last_actions,
                                                model.last_session_lines(self.status),
                                                self.last_anchor)
        self.service_action.setText("Stop recorder service" if view.service_running
                                    else "Start recorder service")
        self.service_action.setEnabled(self.service_state != "not installed")
        if self.service_state == "not installed" and self.settings_window is None:
            self.show_settings()   # first run: nothing works until it's set up
        busy = view.recording or (self.status or {}).get("state") == state.PROCESSING
        self.cut_action.setEnabled(not busy and self.process is None and self._has_pending())
        self.autostart_action.setChecked(desktop.autostart_enabled())
        self.notify(view)

    def _replace_lines(self, old: list[QAction], lines: list[str], before: QAction) -> list[QAction]:
        """Read-only menu lines, rebuilt only when their text changes (an open menu would
        flicker otherwise)."""
        if [a.text() for a in old] == lines:
            return old
        for a in old:
            self.menu.removeAction(a)
        new = []
        for line in lines:
            a = QAction(line, self.menu)
            a.setEnabled(False)
            self.menu.insertAction(before, a)
            new.append(a)
        return new

    def notify(self, view: model.View):
        last = (self.status or {}).get("last_session")
        if last and self.last_end is not None and last["end"] != self.last_end:
            n = len(last.get("clips", []))
            self.tray.showMessage(APP_TITLE, f"{n} clip{'s' if n != 1 else ''} ready"
                                  if n else "Session saved; no dungeon runs in it.",
                                  self.icons[model.IDLE])
        if last:
            self.last_end = last["end"]
        elif self.last_end is None:
            self.last_end = 0
        problem = (self.status or {}).get("problem")
        if problem and problem != self.last_problem:
            self.tray.showMessage(APP_TITLE, problem, QSystemTrayIcon.Warning)
        self.last_problem = problem

    # ── Actions ──

    def _config(self):
        try:
            return config.load()
        except ConfigError as e:
            QMessageBox.warning(None, APP_TITLE, str(e))
            return None

    def _has_pending(self) -> bool:
        try:
            return bool(pending_recordings(config.load()))
        except (ConfigError, OSError, TypeError):
            return False

    def open_dir(self, which: str):
        cfg = self._config()
        if cfg and cfg.output_dir:
            folder = getattr(cfg, which)
            folder.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def on_click(self, reason):
        if reason == QSystemTrayIcon.Trigger:   # left click: the window (the clips, once set up)
            self.show_window()

    def cut_now(self):
        self.process = subprocess.Popen([sys.executable, "-m", "linux_wow_recorder", "process"],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.cut_action.setEnabled(False)
        self.tray.showMessage(APP_TITLE, "Cutting the waiting recordings...", self.icons[model.PROCESSING])
        self.process_timer = QTimer()
        self.process_timer.timeout.connect(self._check_process)
        self.process_timer.start(1000)

    def _check_process(self):
        if self.process.poll() is None:
            return
        self.process_timer.stop()
        output = self.process.stdout.read().strip()
        ok = self.process.returncode == 0
        self.process = None
        self.tray.showMessage(APP_TITLE, output.splitlines()[-1] if output else "Done.",
                              QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning)

    def _service_running(self) -> bool:
        return self.service_state in ("active", "activating")

    def _systemctl(self, verb: str) -> bool:
        result = subprocess.run(["systemctl", "--user", verb, service.UNIT_NAME],
                                capture_output=True, text=True)
        if result.returncode != 0:
            QMessageBox.warning(None, APP_TITLE, result.stderr.strip() or "systemctl failed")
        return result.returncode == 0

    def _ok_to_stop(self) -> bool:
        if not (self.status and self.status.get("state") == state.RECORDING):
            return True
        answer = QMessageBox.question(
            None, APP_TITLE, "WoW is being recorded. Stop the service anyway?\n\n"
            "The recording so far is saved and cut after the next session.")
        return answer == QMessageBox.Yes

    def toggle_service(self):
        running = self._service_running()
        if running and not self._ok_to_stop():
            return
        self._systemctl("stop" if running else "start")
        self.service_ticks = 0
        self.refresh()

    def quit_all(self):
        """Stop the service (until the next login, if it's enabled) and close the tray icon."""
        self.service_state = service.state()
        if self._service_running() and not (self._ok_to_stop() and self._systemctl("stop")):
            return
        self.app.quit()

    def show_window(self):
        """What opening the app from the menu shows: the clips, or the settings until it's set up."""
        if self.service_state == "not installed":
            self.show_settings()
        else:
            self.show_clips()

    def closed_to_tray(self):
        if self.told_about_tray or not self.tray.isVisible():
            return
        self.told_about_tray = True
        self.tray.showMessage(APP_TITLE, "Still running in the tray. Right-click the icon for "
                              "the menu, or choose Quit there to stop.", self.icons[model.IDLE])

    def show_clips(self):
        from .browser import ClipBrowser
        if self.clips_window is None:
            self.clips_window = ClipBrowser()
            self.clips_window.closed.connect(self.closed_to_tray)
            self.clips_window.settings_requested.connect(self.show_settings)
        else:
            self.clips_window.reload()
        self.clips_window.show()
        self.clips_window.raise_()
        self.clips_window.activateWindow()

    def show_settings(self):
        from .settings import SettingsWindow
        if self.settings_window is None or not self.settings_window.isVisible():
            self.settings_window = SettingsWindow(on_saved=self._after_save)
            self.settings_window.finished.connect(lambda _: self.closed_to_tray())
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _after_save(self):
        self.service_ticks = 0
        self.refresh()
        if self.clips_window is not None:
            self.clips_window.reload()   # the clips folder may have changed

    def open_log_dir(self):
        logs.LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs.LOG_DIR)))

    def show_log(self):
        if self.log_window is None:
            self.log_window = LogWindow()
        else:
            self.log_window.load()
        self.log_window.show()
        self.log_window.raise_()

    def toggle_autostart(self):
        if desktop.autostart_enabled():
            desktop.remove_autostart()
        else:
            desktop.install_autostart()
        self.autostart_action.setChecked(desktop.autostart_enabled())


def _show_running_instance() -> bool:
    """Asks an app that's already running to show its window. False if none is running."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def _wait_for_tray(app: QApplication):
    """Waits up to TRAY_WAIT_MS for the desktop's tray (a StatusNotifier host) to come up.
    Qt checks for it only once and remembers the answer, so this has to run before
    QSystemTrayIcon is first used."""
    deadline = QDeadlineTimer(TRAY_WAIT_MS)
    while not deadline.hasExpired():
        watcher = QDBusInterface("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                                 "org.kde.StatusNotifierWatcher", QDBusConnection.sessionBus())
        if watcher.isValid() and watcher.property("IsStatusNotifierHostRegistered"):
            return
        app.processEvents()
        QThread.msleep(500)


def run(start_hidden: bool = False) -> int:
    """start_hidden: only the tray icon (at login); otherwise the window opens too."""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    # Matches the .desktop file's name, which is how Wayland taskbars find the icon.
    app.setDesktopFileName(f"{APP_NAME}-gui")
    app.setWindowIcon(app_icon())

    lock = QLockFile(str(state.STATUS_FILE.parent / "gui.lock"))
    state.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not lock.tryLock(100):
        if not start_hidden and not _show_running_instance():
            print("The app is already running.")
        return 0
    QLocalServer.removeServer(SERVER_NAME)   # left over if the last run crashed
    server = QLocalServer()
    server.listen(SERVER_NAME)

    if start_hidden:
        _wait_for_tray(app)
    has_tray = QSystemTrayIcon.isSystemTrayAvailable()
    # With a tray, closing the window leaves the app in the tray; without one, it quits.
    app.setQuitOnLastWindowClosed(not has_tray)
    tray = Tray(app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), tray.show_window()))
    if not has_tray:
        QMessageBox.warning(None, APP_TITLE, "This desktop has no system tray, so the app closes "
                            "with its window (the recorder service keeps running). On GNOME, "
                            "install the \"AppIndicator and KStatusNotifierItem Support\" "
                            "extension.")
    if not start_hidden or not has_tray:
        tray.show_window()
    return app.exec()
