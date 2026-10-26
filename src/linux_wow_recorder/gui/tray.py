"""The tray icon and its menu."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import (SLOT, QDeadlineTimer, QLineF, QLockFile, QObject, QProcess, QRectF,
                            QSettings, Qt, QThread, QTimer, QUrl, Slot)
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QGuiApplication, QIcon, QPainter,
                           QPalette, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QDialog, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSystemTrayIcon, QVBoxLayout)

from .. import APP_NAME, APP_TITLE, __version__, config, logs, service, state, updates
from ..clips import pending_recordings
from ..config import ConfigError
from . import desktop, model

POLL_MS = 1000
RELEASE_TIMER_MS = 15 * 60_000   # how often check_release looks whether a day has passed
# systemctl runs this often when systemd's D-Bus signals can't be used, and once a minute anyway.
SERVICE_POLL_S, SERVICE_RECHECK_S = 5, 60
# Whether recordings are waiting: a folder listing, redone when the status changes or this often.
PENDING_RECHECK_S = 30
TRAY_WAIT_MS = 30_000   # at login the app can start a few seconds before the desktop's tray
RED, AMBER, GREY = QColor("#E5484D"), QColor("#E8A33D"), QColor("#7A818C")


def draw_icon(kind: str, ink: QColor | None = None) -> QIcon:
    """A ring with a dot: red while recording, amber while cutting, grey otherwise, and a
    warning badge when something needs attention. The ring is in the desktop's text colour
    (ink) when it matters, so it shows on light and dark panels alike."""
    ink = ink or QGuiApplication.palette().color(QPalette.WindowText)
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        ring = ink if kind in (model.RECORDING, model.ATTENTION) else GREY
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
        self.setWindowTitle(f"{APP_TITLE} {__version__}: log")
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


def _unit_path(unit: str) -> str:
    """systemd's D-Bus object path for a unit: every character but [A-Za-z0-9] as _xx."""
    return "/org/freedesktop/systemd1/unit/" + "".join(
        c if c.isascii() and c.isalnum() else f"_{ord(c):02x}" for c in unit)


class ServiceWatcher(QObject):
    """The service's state (service.state()'s words), kept up to date from systemd's D-Bus
    signals rather than by running systemctl. Without the bus, systemctl every SERVICE_POLL_S."""

    def __init__(self):
        super().__init__()
        self.state = service.state()
        self.checked = time.monotonic()
        self.live = self._subscribe()

    def _subscribe(self) -> bool:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        manager = QDBusInterface("org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                                 "org.freedesktop.systemd1.Manager", bus)
        # systemd sends unit PropertiesChanged signals only to clients that subscribed.
        if manager.call("Subscribe").type() == QDBusMessage.ErrorMessage:
            return False
        return bus.connect("org.freedesktop.systemd1", _unit_path(service.UNIT_NAME),
                           "org.freedesktop.DBus.Properties", "PropertiesChanged", self,
                           SLOT("_changed(QString,QVariantMap,QStringList)"))

    @Slot(str, "QVariantMap", "QStringList")
    def _changed(self, interface: str, changed: dict, invalidated: list):
        active = changed.get("ActiveState")
        if interface == "org.freedesktop.systemd1.Unit" and isinstance(active, str):
            self.state = active if service.UNIT_FILE.exists() else "not installed"

    def current(self, force: bool = False) -> str:
        """The state; runs systemctl only when forced, without D-Bus, or once in a while."""
        age = time.monotonic() - self.checked
        if force or age >= (SERVICE_RECHECK_S if self.live else SERVICE_POLL_S):
            self.state, self.checked = service.state(), time.monotonic()
        elif not service.UNIT_FILE.exists():
            self.state = "not installed"   # a stat, so an uninstall shows at once
        return self.state


class Tray:
    def __init__(self, app: QApplication, restart=None):
        """restart(): replaces the app with a newly installed version (run() makes it)."""
        self.app = app
        self.restart = restart
        self.ink = None
        self.draw_icons()
        self.tray = QSystemTrayIcon(self.icons[model.OFF])
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_click)
        self.log_window = None
        self.settings_window = None
        self.clips_window = None
        self.process = None            # a running `process` command
        self.told_about_tray = False   # the "still running in the tray" message, once per run
        self.service = ServiceWatcher()
        self.service_state = self.service.state
        self.status = None
        self.pending = False           # recordings waiting in sessions/
        self.pending_key = None        # the status it was worked out for
        self.pending_checked = 0.0
        self.last_end = None           # last_session["end"] already seen, for notifications
        self.last_problem = None
        # A newer release on GitHub ([updates] check): when it was last asked, what it said,
        # and which version a popup was already shown for, kept across restarts.
        self.gui_settings = QSettings(str(config.CONFIG_DIR / "gui.ini"), QSettings.IniFormat)
        self.release_check: updates.Check | None = None
        self.release_enabled = False   # [updates] check, as last read

        # What's going on (read-only lines), then what to do, then the ways out.
        self.title = self.menu.addAction("")
        self.title.setEnabled(False)
        self.detail_actions: list[QAction] = []
        self.detail_anchor = self.menu.addSeparator()
        self.last_actions: list[QAction] = []
        self.last_anchor = self.menu.addSeparator()
        clips = self.menu.addAction("Clips...", self.show_clips)
        font = clips.font()
        font.setBold(True)
        clips.setFont(font)
        self.pause_action = self.menu.addAction("Pause recording this session", self.toggle_pause)
        self.switch_action = self.menu.addAction(model.switch_text(False), self.toggle_monitor)
        self.repick_action = self.menu.addAction(model.REPICK_TEXT, self.repick)
        self.cut_action = self.menu.addAction("Cut waiting recordings now", self.cut_now)
        self.in_session = False
        self.release_action = self.menu.addAction("", lambda: QDesktopServices.openUrl(
            QUrl(updates.RELEASES_PAGE)))
        self.release_action.setVisible(False)
        self.menu.addAction("Settings...", self.show_settings)
        folders = self.menu.addMenu("Folders and logs")
        folders.addAction("Open clips folder", lambda: self.open_dir("clips_dir"))
        folders.addAction("Open output folder", lambda: self.open_dir("output_dir"))
        folders.addSeparator()
        folders.addAction("Show log", self.show_log)
        folders.addAction("Open log folder", self.open_log_dir)
        self.menu.addSeparator()
        self.service_action = self.menu.addAction("", self.toggle_service)
        self.autostart_action = self.menu.addAction("Show this icon at login", self.toggle_autostart)
        self.autostart_action.setCheckable(True)
        self.menu.addSeparator()
        # Only when the service starts at login: otherwise it runs while the app is open.
        self.hide_action = self.menu.addAction("Hide tray icon (keeps recording)", app.quit)
        self.menu.addAction("Quit and stop recording", self.quit_all)
        self.start_with_app()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        # A newer version installed under the app: it restarts into it while no window is open.
        self.updates = service.UpdateWatch()
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.check_update)
        self.update_timer.start(service.UPDATE_CHECK_SECONDS * 1000)
        self.release_timer = QTimer()
        self.release_timer.timeout.connect(self.check_release)
        self.release_timer.start(RELEASE_TIMER_MS)
        cfg = self._config(quiet=True)
        self.release_enabled = bool(cfg and cfg.check_updates)   # the menu line before a check
        QTimer.singleShot(60_000, self.check_release)   # not while the desktop is still starting
        self.refresh()
        self.tray.show()

    # ── Updating ──

    def refresh(self, force: bool = False):
        """Every POLL_MS; force (after an action) also rechecks systemd and the waiting
        recordings right away."""
        self.status = state.read()
        self.service_state = self.service.current(force)
        view = model.describe(self.status, self.service_state)
        if QGuiApplication.palette().color(QPalette.WindowText) != self.ink:
            self.draw_icons()   # the desktop's theme changed

        self.tray.setIcon(self.icons[view.icon])
        self.tray.setToolTip("\n".join([f"{APP_TITLE}: {view.title}", *view.details]))
        self.title.setText(view.title)
        self.title.setIcon(self.icons[view.icon])
        self.detail_actions = self._replace_lines(self.detail_actions, view.details,
                                                  self.detail_anchor)
        self.last_actions = self._replace_lines(self.last_actions,
                                                model.last_session_lines(self.status),
                                                self.last_anchor)
        self.service_action.setText("Stop the recorder service" if view.service_running
                                    else "Start the recorder service")
        self.pause_action.setVisible(view.recording or view.paused)
        self.pause_action.setText("Resume recording" if view.paused
                                  else "Pause recording this session")
        self.switch_action.setVisible(view.can_switch)
        self.repick_action.setVisible(view.can_repick)
        self.switch_action.setText(model.switch_text(view.monitor_switch))
        self.service_action.setEnabled(self.service_state != "not installed")
        if not self.set_up() and self.settings_window is None:
            self.show_settings()   # first run: nothing works until it's set up
        # During a session it cuts the clips so far (the service does it); otherwise it cuts the
        # recordings waiting in sessions/.
        status = self.status or {}
        self.in_session = view.service_running and bool(status.get("session_start"))
        if self.in_session:
            waiting = bool(status.get("cut_waiting"))
            self.cut_action.setText("Clips get cut when this run ends" if waiting
                                    else "Cut the clips so far")
            self.cut_action.setEnabled(not waiting)
        else:
            self.cut_action.setText("Cut waiting recordings now")
            busy = view.recording or status.get("state") == state.PROCESSING
            self.cut_action.setEnabled(not busy and self.process is None and self._has_pending(force))
        self.autostart_action.setChecked(desktop.autostart_enabled())
        self.hide_action.setVisible(service.at_login())
        self.notify(view)
        self.show_release()

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
                                  if n else "Session saved; no runs or raid pulls in it.",
                                  self.icons[model.IDLE])
        if last:
            self.last_end = last["end"]
        elif self.last_end is None:
            self.last_end = 0
        problem = (self.status or {}).get("problem")
        if problem and problem != self.last_problem:
            self.tray.showMessage(APP_TITLE, problem, QSystemTrayIcon.Warning)
        self.last_problem = problem

    def check_update(self):
        """Restarts into a newly installed version, but only while none of the app's windows
        are open and nothing runs in the background (a cut, a copy with the chat hidden, an
        after_clips command), so nobody's clip or settings vanish under them."""
        found = self.updates.check()
        if not found or self.restart is None:
            return
        windows = (self.clips_window, self.settings_window, self.log_window)
        if self.process is not None or any(
                w is not None and (w.isVisible() or getattr(w, "busy", lambda: False)())
                for w in windows):
            return   # checked again in a minute
        logging.getLogger(APP_NAME).info(f"Version {found} was installed; restarting into it.")
        self.restart()

    def check_release(self):
        """Asks GitHub for the newest version, if that's turned on and a day has passed."""
        cfg = self._config(quiet=True)
        self.release_enabled = bool(cfg and cfg.check_updates)
        if not self.release_enabled or self.release_check is not None:
            return
        checked = float(self.gui_settings.value("updates/checked", 0) or 0)
        if time.time() - checked >= updates.CHECK_EVERY:
            self.release_check = updates.Check()

    def show_release(self):
        """The answer to check_release: a menu line while a newer version is out, and a popup
        once per version."""
        check = self.release_check
        if check is not None and check.done:
            self.release_check = None
            # Not reachable (offline, say): asked again in an hour, not a day.
            self.gui_settings.setValue("updates/checked", time.time() if check.version else
                                       time.time() - updates.CHECK_EVERY + 3600)
            if check.version:
                self.gui_settings.setValue("updates/latest", check.version)
        latest = str(self.gui_settings.value("updates/latest", "") or "")
        newer = self.release_enabled and updates.is_newer(latest, __version__)
        self.release_action.setVisible(newer)
        if not newer:
            return
        self.release_action.setText(f"Version {latest} is out...")
        if self.gui_settings.value("updates/told", "") != latest:
            self.gui_settings.setValue("updates/told", latest)
            self.tray.showMessage(APP_TITLE, f"Version {latest} is out.", self.icons[model.IDLE])

    def draw_icons(self):
        self.ink = QGuiApplication.palette().color(QPalette.WindowText)
        self.icons = {k: draw_icon(k, self.ink) for k in (model.IDLE, model.RECORDING,
                                                          model.PROCESSING, model.ATTENTION,
                                                          model.OFF)}

    # ── Actions ──

    def toggle_pause(self):
        """Asks the service to stop recording until WoW closes, or to record again."""
        paused = (self.status or {}).get("state") == state.PAUSED
        why = state.request_pause(not paused)
        if why:
            QMessageBox.warning(None, APP_TITLE, why)
            return
        self.tray.showMessage(APP_TITLE, "Recording again." if paused else
                              "Paused until WoW closes. What was recorded so far is kept.",
                              self.icons[model.IDLE])

    def toggle_monitor(self):
        """Asks the service to record the monitor instead of the picked window, or the window
        again, for this session."""
        why = state.request_monitor(not (self.status or {}).get("monitor_switch"))
        if why:
            QMessageBox.warning(None, APP_TITLE, why)

    def repick(self):
        """Asks the service to open the screen-share dialog again."""
        if why := state.request_repick():
            QMessageBox.warning(None, APP_TITLE, why)

    def _config(self, quiet: bool = False):
        """The config, or None; a broken one is said in a message box unless quiet."""
        try:
            return config.load()
        except ConfigError as e:
            if not quiet:
                QMessageBox.warning(None, APP_TITLE, str(e))
            return None

    def _has_pending(self, force: bool = False) -> bool:
        """Whether recordings wait in sessions/. Loading the config and listing the folder
        every second is wasted work, so it's redone only when the service's state or last
        session changes, or every PENDING_RECHECK_S."""
        status = self.status or {}
        key = (status.get("state"), (status.get("last_session") or {}).get("end"))
        if force or key != self.pending_key or \
                time.monotonic() - self.pending_checked >= PENDING_RECHECK_S:
            self.pending_key, self.pending_checked = key, time.monotonic()
            try:
                self.pending = bool(pending_recordings(config.load()))
            except (ConfigError, OSError, TypeError):
                self.pending = False
        return self.pending

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
        if self.in_session:
            if why := state.request_cut():
                QMessageBox.warning(None, APP_TITLE, why)
            else:
                self.tray.showMessage(APP_TITLE, "Cutting the clips so far; recording goes on.",
                                      self.icons[model.PROCESSING])
            return
        # QProcess keeps the output in memory as it comes: a pipe read only at the end could
        # fill up with log lines and stall the command.
        process = self.process = QProcess()
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(lambda *_: self._process_done(process))
        process.errorOccurred.connect(lambda e: e == QProcess.FailedToStart
                                      and self._process_done(process))
        process.start(sys.executable, ["-m", "linux_wow_recorder", "process"])
        self.cut_action.setEnabled(False)
        self.tray.showMessage(APP_TITLE, "Cutting the waiting recordings...", self.icons[model.PROCESSING])

    def _process_done(self, process: QProcess):
        if self.process is not process:
            return   # errorOccurred and finished both came
        self.process = None
        output = bytes(process.readAll()).decode(errors="replace")
        lines = [l for l in output.splitlines() if l.strip() and not logs.is_log_line(l)]
        ok = (process.exitStatus() == QProcess.NormalExit and process.exitCode() == 0
              and process.error() != QProcess.FailedToStart)
        process.deleteLater()
        self.tray.showMessage(APP_TITLE, lines[-1] if lines else "Done." if ok else "Cutting failed.",
                              QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning)
        self.refresh(force=True)

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

    def start_with_app(self):
        """Opening the app starts the recorder (unless it's not set up yet, or already
        running): it records while the app is open."""
        if service.refresh_unit():
            logging.getLogger(APP_NAME).info("The service ran a copy of the app that's gone; "
                                             "it runs this one now.")
        if not config.CONFIG_FILE.exists():
            return
        if self.service.current(force=True) in ("inactive", "failed"):
            try:
                service.start()
            except RuntimeError as e:
                QMessageBox.warning(None, APP_TITLE, f"The recorder didn't start: {e}")

    def stop_with_app(self):
        """The app closing (without a tray, its window) stops the recorder, unless it starts at
        login. Without waiting: systemd saves the recording."""
        if not service.at_login() and self.service.current(force=True) in ("active", "activating"):
            subprocess.run(["systemctl", "--user", "stop", "--no-block", service.UNIT_NAME],
                           capture_output=True)

    def toggle_service(self):
        running = self._service_running()
        if running and not self._ok_to_stop():
            return
        self._systemctl("stop" if running else "start")
        self.refresh(force=True)

    def quit_all(self):
        """Stop the service (until the next login, if it's enabled) and close the tray icon."""
        self.service_state = self.service.current(force=True)
        if self._service_running() and not (self._ok_to_stop() and self._systemctl("stop")):
            return
        self.app.quit()

    def set_up(self) -> bool:
        """Settings saved and the service installed; either can be missing after a reinstall."""
        return self.service_state != "not installed" and config.CONFIG_FILE.exists()

    def show_window(self):
        """What opening the app from the menu shows: the clips, or the settings until it's set up."""
        if not self.set_up():
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
            self.clips_window = ClipBrowser(service_state=lambda: self.service_state)
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
        self.check_release()   # turned on (or off) just now
        self.refresh(force=True)
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

    def restart():
        """Replaces this process with the newly installed version, in the tray. The lock and the
        local server go first: the new one has the same process ID and would think it's
        already running."""
        server.close()
        lock.unlock()
        logging.shutdown()
        os.execv(sys.executable, [sys.executable, "-m", "linux_wow_recorder.gui", "--tray"])

    tray = Tray(app, restart if has_tray else None)
    app.aboutToQuit.connect(tray.stop_with_app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), tray.show_window()))
    if not has_tray:
        QMessageBox.warning(None, APP_TITLE, "This desktop has no system tray, so the app closes "
                            "with its window. On GNOME, "
                            "install the \"AppIndicator and KStatusNotifierItem Support\" "
                            "extension.")
    if not start_hidden or not has_tray:
        tray.show_window()
    return app.exec()
