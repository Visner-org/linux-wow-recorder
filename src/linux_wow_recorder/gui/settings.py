"""The settings window: everything `setup` asks, and the rest of the config file.

It edits the same dict `setup` does (config.load_raw), saves it with config.save, and restarts
the service so the change takes effect. With no config yet it doubles as the first-run setup.
"""

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .. import APP_TITLE, config, logs, service, state
from ..cli import find_recorder_command
from ..config import CONFIG_FILE, MAX_WEBHOOKS, NOTIFY_EVENTS, ConfigError
from ..notify import Notifier
from . import desktop, model
from .overlay import MonitorFlash


PORTAL_INFO = """\
<p><b>The safest choice: pick the WoW window, and only WoW is recorded.</b></p>
<p>Notifications, Discord messages, your browser and anything else on screen stay out of the
video, even when you alt-tab. Alt-tabbing freezes the video on WoW's last frame instead of
showing your desktop.</p>
<p><b>The catch: it asks every time you start WoW.</b> When recording starts, your desktop's
screen-share dialog asks what to record; pick the World of Warcraft window. A new WoW window
counts as a new window, so on most desktops the dialog comes back every time you launch the
game. Recording starts once you've picked.</p>
<p>If you pick a whole screen in that dialog instead, it's remembered and you aren't asked
again, but everything on that screen is recorded. To stop being asked without the dialog, pick
a monitor (DP-1, HDMI-A-1, ...) in this list.</p>"""


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _spin(value, low, high, suffix="", zero_text="") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(int(value))
    box.setSuffix(suffix)
    if zero_text:
        box.setSpecialValueText(zero_text)   # shown instead of the minimum (0)
    return box


class SettingsWindow(QDialog):
    def __init__(self, on_saved=None):
        super().__init__()
        self.on_saved = on_saved
        self.setWindowTitle(f"{APP_TITLE}: settings")
        try:
            self.data = config.load_raw()
        except ConfigError as e:
            QMessageBox.warning(self, APP_TITLE, f"{e}\n\nStarting from the defaults.")
            self.data = config.load_raw(CONFIG_FILE.with_name("missing.toml"))
        self.first_run = not CONFIG_FILE.exists()
        if self.first_run and (found := find_recorder_command()):
            self.data["recorder"]["command"] = found
        self.test_process = None

        grid = QGridLayout()
        grid.setSpacing(14)
        grid.addWidget(self.folders_box(), 0, 0)
        grid.addWidget(self.capture_box(), 0, 1)
        grid.addWidget(self.clips_box(), 1, 0)
        grid.addWidget(self.service_box(), 1, 1)
        grid.addWidget(self.discord_box(), 2, 0, 1, 2)
        grid.addWidget(self.hook_box(), 3, 0, 1, 2)
        grid.setRowStretch(4, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        header = _hint(f"Saved to {CONFIG_FILE}" + (
            ". Nothing is set up yet: fill in the two folders, then Save." if self.first_run else ""))
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #d97706;")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(
            "Save and restart service" if service.state() in ("active", "activating")
            else "Save and start")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.problems)
        layout.addWidget(buttons)
        self.update_hints()
        self.fit_to_content(inner, scroll)

    def fit_to_content(self, inner: QWidget, scroll: QScrollArea):
        """Open wide enough that nothing is cut off (fonts and themes vary), within the screen."""
        width = (inner.sizeHint().width() + scroll.verticalScrollBar().sizeHint().width()
                 + 2 * scroll.frameWidth() + self.layout().contentsMargins().left()
                 + self.layout().contentsMargins().right())
        width, height = max(width, 1000), 820
        if screen := self.screen():
            free = screen.availableGeometry()
            width, height = min(width, free.width() - 40), min(height, free.height() - 40)
        self.resize(width, height)

    # ── Sections ──

    def folder_row(self, value: str, create: bool) -> tuple[QWidget, QLineEdit]:
        edit = QLineEdit(value)
        edit.textChanged.connect(self.update_hints)
        browse = QPushButton("Browse...")

        def pick():
            start = edit.text() or str(CONFIG_FILE.home())
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
            if chosen:
                edit.setText(chosen)
        browse.clicked.connect(pick)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        h.addWidget(browse)
        return row, edit

    def folders_box(self) -> QGroupBox:
        p = self.data["paths"]
        box = QGroupBox("Folders")
        form = QVBoxLayout(box)
        form.addWidget(_label("Combat log folder: the Logs folder next to WoW's _retail_ "
                              "(or _classic_) folder"))
        row, self.log_dir = self.folder_row(p["combat_log_dir"], create=False)
        form.addWidget(row)
        self.log_hint = _hint()
        form.addWidget(self.log_hint)
        form.addSpacing(8)
        form.addWidget(_label("Output folder: recordings and clips (a few GB per hour)"))
        row, self.out_dir = self.folder_row(p["output_dir"], create=True)
        form.addWidget(row)
        self.out_hint = _hint()
        form.addWidget(self.out_hint)
        form.addStretch(1)
        return box

    def capture_box(self) -> QGroupBox:
        r = self.data["recorder"]
        box = QGroupBox("Capture")
        form = QFormLayout(box)
        self.command = QLineEdit(r["command"])
        form.addRow("Recorder command", self.command)

        self.capture = QComboBox()
        self.capture.setEditable(True)   # any monitor name works, listed or not
        # don't widen the window to fit the longest option
        self.capture.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.capture.setMinimumContentsLength(12)
        for value, label in self.capture_options():
            self.capture.addItem(label, value)
        i = self.capture.findData(r["capture"])
        if i < 0:
            self.capture.addItem(r["capture"], r["capture"])
            i = self.capture.count() - 1
        self.capture.setCurrentIndex(i)
        # an editable box shows the end of a long label; show its start
        self.capture.lineEdit().setCursorPosition(0)
        self.capture.currentIndexChanged.connect(lambda _: self.capture.lineEdit().setCursorPosition(0))
        self.monitor_flash = MonitorFlash()
        self.capture.activated.connect(self.flash_monitor)   # picked by hand, not set in code
        self.capture.activated.connect(
            lambda i: self.capture.itemData(i) == "portal" and self.explain_portal())
        about = QPushButton("?")
        about.setFixedWidth(32)
        about.setToolTip("About \"A window or screen you pick\"")
        about.clicked.connect(self.explain_portal)
        row = QHBoxLayout()
        row.addWidget(self.capture, 1)
        row.addWidget(about)
        form.addRow("What to record", row)

        self.fps = QComboBox()
        for fps in (30, 60, 120, 144):
            self.fps.addItem(f"{fps} fps", fps)
        i = self.fps.findData(int(r["fps"]))
        if i < 0:
            self.fps.addItem(f"{r['fps']} fps", int(r["fps"]))
            i = self.fps.count() - 1
        self.fps.setCurrentIndex(i)
        form.addRow("Frame rate", self.fps)

        self.quality = QComboBox()
        for value, label in model.QUALITIES.items():
            self.quality.addItem(label, value)
        self.quality.setCurrentIndex(max(0, self.quality.findData(r["quality"])))
        form.addRow("Quality", self.quality)

        sound = QVBoxLayout()
        self.game_sound = QButtonGroup(self)
        current = config.game_sound(r["audio"])
        for i, (key, label) in enumerate(config.GAME_SOUND.items()):
            button = QRadioButton(label)
            button.setObjectName(f"sound_{key}")
            button.setChecked(key == current)
            self.game_sound.addButton(button, i)
            sound.addWidget(button)
        self.mic = QCheckBox("Also record my microphone")
        self.mic.setChecked(any("default_input" in a for a in r["audio"]))
        sound.addWidget(self.mic)
        form.addRow("Sound", sound)

        self.test_button = QPushButton("Save and record a 5-second test")
        self.test_button.clicked.connect(self.test_recorder)
        self.test_result = _hint()
        form.addRow("", self.test_button)
        form.addRow("", self.test_result)
        return box

    def explain_portal(self):
        QMessageBox.information(self, "A window or screen you pick", PORTAL_INFO)

    def flash_monitor(self, i: int):
        """Big red "THIS ONE" on the monitor just picked, so DP-1/DP-2 aren't guesswork."""
        options = [self.capture.itemData(j) for j in range(self.capture.count())]
        monitor = model.capture_monitor(self.capture.itemData(i), options)
        if monitor:
            self.monitor_flash.flash(monitor)

    def capture_options(self) -> list[tuple[str, str]]:
        default = [("portal", "A window or screen you pick (e.g. only WoW)"), ("screen", model.SCREEN_LABEL)]
        try:
            cmd = shlex.split(self.data["recorder"]["command"]) + ["--list-capture-options"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return default
        return model.parse_capture_options(out) or default

    def clips_box(self) -> QGroupBox:
        c, s = self.data["clips"], self.data["storage"]
        box = QGroupBox("Clips and cleanup")
        form = QFormLayout(box)
        self.session_days = _spin(s["session_retention_days"], 0, 3650, " days", "Forever")
        form.addRow("Keep full recordings", self.session_days)
        self.clip_days = _spin(s["clip_retention_days"], 0, 3650, " days", "Forever")
        form.addRow("Keep clips", self.clip_days)
        self.min_free = _spin(s["min_free_gb"], 0, 100000, " GB free")
        form.addRow("Warn below", self.min_free)
        self.stop_below = _spin(s["stop_below_gb"], 0, 100000, " GB free")
        form.addRow("Stop recording below", self.stop_below)
        self.min_run = _spin(c["min_run_minutes"], 0, 120, " min")
        form.addRow("Skip runs shorter than", self.min_run)
        form.addRow("", _hint("Completed keys are always clipped, however short."))
        self.pad_before = _spin(c["pad_before"], 0, 600, " s")
        form.addRow("Extra video before a run", self.pad_before)
        self.pad_after = _spin(c["pad_after"], 0, 600, " s")
        form.addRow("Extra video after a run", self.pad_after)
        return box

    def service_box(self) -> QGroupBox:
        box = QGroupBox("Background service")
        form = QVBoxLayout(box)
        self.service_state = QLabel()
        form.addWidget(self.service_state)
        self.service_at_login = QCheckBox("Record at every login (systemd user service)")
        self.service_at_login.setChecked(self.first_run or service.state() != "not installed")
        form.addWidget(self.service_at_login)
        self.tray_at_login = QCheckBox("Show the tray icon at login")
        self.tray_at_login.setChecked(self.first_run or desktop.autostart_enabled())
        form.addWidget(self.tray_at_login)
        form.addWidget(_hint("Saving restarts the service. If WoW is being recorded, the "
                             "recording so far is saved and cut after the next session."))
        self.detailed = QCheckBox("Detailed logging (for tracking down a problem)")
        self.detailed.setChecked(bool(self.data["logging"]["detailed"]))
        form.addWidget(self.detailed)
        report = QPushButton("Make a report file...")
        report.clicked.connect(self.make_report)
        form.addWidget(report)
        form.addWidget(_hint("Logs stay on this computer; nothing is ever sent. The report file "
                             "is for you to share by hand if you want help."))
        form.addStretch(1)
        return box

    def make_report(self):
        start = str(logs.default_report_folder())
        folder = QFileDialog.getExistingDirectory(self, "Where to save the report file", start)
        if not folder:
            return
        try:
            path = logs.write_report(Path(folder))
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't write the report file: {e}")
            return
        QMessageBox.information(
            self, APP_TITLE,
            f"Saved {path.name} in {folder}.\n\nNothing has been sent anywhere. Read it before "
            "you share it: webhook links, your home folder, user name, computer name and IP "
            "addresses have been replaced.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def discord_box(self) -> QGroupBox:
        d = self.data["discord"]
        box = QGroupBox("Discord or Fluxer messages (optional)")
        layout = QVBoxLayout(box)
        layout.addWidget(_hint("Discord: Server Settings > Integrations > Webhooks > New Webhook "
                               "> Copy Webhook URL. Fluxer: the channel's or community's "
                               f"settings > Webhooks. Up to {MAX_WEBHOOKS}; every message goes "
                               "to each one. None = no messages."))
        self.webhook_rows = QVBoxLayout()
        self.webhooks: list[QLineEdit] = []
        layout.addLayout(self.webhook_rows)
        row = QHBoxLayout()
        self.add_webhook_button = QPushButton("Add webhook")
        self.add_webhook_button.clicked.connect(lambda: self.add_webhook().setFocus())
        self.show_webhooks = QCheckBox("Show URLs")
        self.show_webhooks.toggled.connect(self.webhook_echo)
        test = QPushButton("Send a test message")
        test.clicked.connect(self.test_discord)
        self.webhook_count = QLabel()
        row.addWidget(self.add_webhook_button)
        row.addWidget(self.webhook_count)
        row.addStretch(1)
        row.addWidget(self.show_webhooks)
        row.addWidget(test)
        layout.addLayout(row)
        for url in d["webhook_urls"]:
            self.add_webhook(url)
        self.webhook_changed()
        checks = QGridLayout()
        self.events: dict[str, QCheckBox] = {}
        for i, (key, (default, desc)) in enumerate(NOTIFY_EVENTS.items()):
            box_ = QCheckBox(desc)
            box_.setChecked(bool(d["notify"].get(key, default)))
            self.events[key] = box_
            checks.addWidget(box_, i // 2, i % 2)
        layout.addLayout(checks)
        return box

    def add_webhook(self, url: str = "") -> QLineEdit:
        edit = QLineEdit(url)
        edit.setPlaceholderText("https://discord.com/api/webhooks/... or a Fluxer webhook URL")
        delete = QPushButton("Delete")
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(QLabel(), 0)   # its number, set by webhook_changed
        line.addWidget(edit, 1)
        line.addWidget(delete)
        delete.clicked.connect(lambda: self.delete_webhook(row, edit))
        self.webhook_rows.addWidget(row)
        self.webhooks.append(edit)
        self.webhook_changed()
        return edit

    def delete_webhook(self, row: QWidget, edit: QLineEdit):
        self.webhooks.remove(edit)
        self.webhook_rows.removeWidget(row)
        row.deleteLater()
        self.webhook_changed()

    def webhook_changed(self):
        """Renumbers the rows, hides or shows the URLs, and stops "Add" at the limit."""
        for i, edit in enumerate(self.webhooks, 1):
            edit.parentWidget().layout().itemAt(0).widget().setText(f"{i}.")
        self.webhook_echo(self.show_webhooks.isChecked())
        n = len(self.webhooks)
        self.add_webhook_button.setEnabled(n < MAX_WEBHOOKS)
        self.webhook_count.setText(f"{n} of {MAX_WEBHOOKS}" if n else "")

    def webhook_echo(self, show: bool):
        for edit in self.webhooks:   # anyone with a URL can post
            edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)

    def hook_box(self) -> QGroupBox:
        box = QGroupBox("After clips (optional)")
        layout = QVBoxLayout(box)
        layout.addWidget(_hint("A command run after each session with the new clips' paths "
                               "added as arguments, e.g. a script that copies them to a server."))
        self.hook = QLineEdit(self.data["hooks"]["after_clips"])
        self.hook.setPlaceholderText("~/bin/upload-clips.sh")
        layout.addWidget(self.hook)
        return box

    # ── Reading the form ──

    def collect(self) -> dict:
        d = self.data
        d["paths"]["combat_log_dir"] = self.log_dir.text().strip()
        d["paths"]["output_dir"] = self.out_dir.text().strip()
        r = d["recorder"]
        r["command"] = self.command.text().strip()
        # An item picked from the list has its value as data; typed text is the value itself.
        i = self.capture.currentIndex()
        typed = self.capture.currentText().strip()
        r["capture"] = (self.capture.itemData(i) if i >= 0 and self.capture.itemText(i) == typed
                        else typed) or "portal"
        r["fps"] = self.fps.currentData()
        r["quality"] = self.quality.currentData()
        game = list(config.GAME_SOUND)[max(0, self.game_sound.checkedId())]
        r["audio"] = config.audio_tracks(game, self.mic.isChecked())
        s, c = d["storage"], d["clips"]
        s["session_retention_days"] = self.session_days.value()
        s["clip_retention_days"] = self.clip_days.value()
        s["min_free_gb"] = self.min_free.value()
        s["stop_below_gb"] = self.stop_below.value()
        c["min_run_minutes"] = self.min_run.value()
        c["pad_before"] = self.pad_before.value()
        c["pad_after"] = self.pad_after.value()
        d["discord"]["webhook_urls"] = [e.text().strip() for e in self.webhooks
                                        if e.text().strip()]
        d["discord"]["notify"] = {k: b.isChecked() for k, b in self.events.items()}
        d["hooks"]["after_clips"] = self.hook.text().strip()
        d["logging"]["detailed"] = self.detailed.isChecked()
        return d

    def update_hints(self):
        self.log_hint.setText(model.combat_log_hint(self.log_dir.text().strip()))
        out = self.out_dir.text().strip()
        try:
            cfg = config.from_dict(self.collect())
        except ConfigError:
            cfg = None
        try:
            free = shutil.disk_usage(Path(out).expanduser()).free / 1e9 if out else None
        except OSError:
            free = None
        self.out_hint.setText(f"{free:.0f} GB free" if free is not None else
                              "Will be created when you save" if out else "")
        self.service_state.setText(f"Service: {service.state()}")
        found = config.problems(cfg) if cfg else []
        # The output folder is created on save, so it isn't a problem yet.
        found = [p for p in found if not (p.startswith("output_dir") and "doesn't exist" in p)]
        self.problems.setText("\n".join(f"⚠ {p}" for p in found))

    # ── Buttons ──

    def write(self) -> bool:
        data = self.collect()
        out = data["paths"]["output_dir"]
        if not data["paths"]["combat_log_dir"] or not out:
            QMessageBox.warning(self, APP_TITLE, "Both folders are needed.")
            return False
        try:
            config.from_dict(data)
            Path(out).expanduser().mkdir(parents=True, exist_ok=True)
            config.save(data)
        except (ConfigError, OSError) as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't save: {e}")
            return False
        self.first_run = False
        return True

    def save(self):
        status = state.read()
        if status and status.get("state") == state.RECORDING and QMessageBox.question(
                self, APP_TITLE, "WoW is being recorded. Saving restarts the service: the "
                "recording so far is saved and cut after the next session, and a new one "
                "starts. Save now?") != QMessageBox.Yes:
            return
        if not self.write():
            return
        try:
            if self.service_at_login.isChecked():
                service.install()   # (re)writes the unit and restarts it with the new config
            elif service.state() != "not installed":
                service.uninstall()
        except RuntimeError as e:
            QMessageBox.warning(self, APP_TITLE, f"Saved, but the service didn't start: {e}")
        if self.tray_at_login.isChecked():
            desktop.install_autostart()
        else:
            desktop.remove_autostart()
        if self.on_saved:
            self.on_saved()
        self.accept()

    def test_recorder(self):
        if self.test_process or not self.write():
            return
        self.test_button.setEnabled(False)
        self.test_result.setText("Recording 5 seconds...")
        self.test_process = QProcess(self)
        self.test_process.setProcessChannelMode(QProcess.MergedChannels)
        self.test_process.finished.connect(self.test_finished)
        self.test_process.start(sys.executable, ["-m", "linux_wow_recorder", "test-recorder"])

    def test_finished(self):
        out = bytes(self.test_process.readAll()).decode(errors="replace").strip()
        lines = [l for l in out.splitlines() if "[INFO]" not in l]   # the recorder's log lines
        self.test_result.setText("\n".join(lines[-6:]) or "Done.")
        self.test_process = None
        self.test_button.setEnabled(True)
        self.update_hints()

    def test_discord(self):
        try:
            cfg = config.from_dict(self.collect())
        except ConfigError as e:
            QMessageBox.warning(self, APP_TITLE, str(e))
            return
        if not cfg.webhook_urls:
            QMessageBox.information(self, APP_TITLE, "Add a webhook URL first.")
            return
        failed = Notifier(cfg).send("", "Test message from the settings window.", force=True)
        if not failed:
            QMessageBox.information(self, APP_TITLE, f"Sent to {len(cfg.webhook_urls)} webhook(s).")
        else:
            QMessageBox.warning(self, APP_TITLE, f"Failed for webhook "
                                f"{', '.join(map(str, failed))}. Check the URL.")
