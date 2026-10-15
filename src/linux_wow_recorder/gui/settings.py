"""The settings window: everything `setup` asks, and the rest of the config file.

It edits the same dict `setup` does (config.load_raw), saves it with config.save, and restarts
the service when the config file changed, since the service reads it only when it starts. Pages
in a sidebar; with no config yet, the same pieces make a three-step first-run setup instead.
"""

import copy
import shlex
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import APP_TITLE, REPO_URL, __version__, config, logs, service, state, uninstall, updates
from ..cli import find_recorder_command
from ..config import CONFIG_FILE, MAX_WEBHOOKS, NOTIFY_EVENTS, ConfigError
from ..notify import Notifier
from . import desktop, model, theme
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

DEFAULT_CAPTURE = [("portal", "A window or screen you pick (e.g. only WoW)"),
                   ("screen", model.SCREEN_LABEL)]
PAGES = ("Folders and capture", "Sound", "Clips and storage", "Notifications", "Advanced",
         "About")
# Which page each checked field is on, so the sidebar can mark a page with a problem.
FIELD_PAGE = {"combat_log_dir": 0, "output_dir": 0, "stop_below_gb": 2, "other_folders": 2,
              "webhooks": 3, "recorder_command": 4, "after_clips": 4}
# Settings only the desktop app reads: changing them doesn't restart the service.
APP_ONLY = (("clips", "other_folders"), ("updates", "check"),
            *(("clips_window", key) for key in config.CLIPS_WINDOW))
WIZARD = (("Folders", "Where WoW writes its combat log, and where recordings and clips go."),
          ("What to record", "The picture and the sound. Everything else can wait for Settings."),
          ("Try it", "A 5-second recording to check the picture and the sound. Then Finish "
                     "starts the recorder; it runs whenever you're logged in."))
_MARK = {model.OK: "✓", model.INFO: "•", model.WARNING: "⚠", model.ERROR: "✕"}


def _hint(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setProperty("muted", True)
    # Expanding, or a form keeps a wrapped label in a narrow column.
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return label


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.15)
    label.setFont(font)
    return label


def _spin(value, low, high, suffix="", zero_text="") -> QSpinBox:
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(int(value))
    box.setSuffix(suffix)
    if zero_text:
        box.setSpecialValueText(zero_text)   # shown instead of the minimum (0)
    return box


def _section(*parts) -> QWidget:
    """A page's column: headings, forms and widgets, top-aligned."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(8)
    for part in parts:
        if isinstance(part, QWidget):
            v.addWidget(part)
        else:
            v.addLayout(part)
    return w


def _field(widget, *below: QWidget) -> QWidget:
    """A form field with its check and help lines under it, in one cell: as separate form rows,
    a wrapped line gets a gap around it."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    if isinstance(widget, QSpinBox):
        v.addWidget(widget, 0, Qt.AlignLeft)   # a number box stays its own size
    elif isinstance(widget, QWidget):
        v.addWidget(widget)
    else:
        v.addLayout(widget)
    for b in below:
        v.addWidget(b)
    return w


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(8)
    return form


class SettingsWindow(QDialog):
    def __init__(self, on_saved=None):
        super().__init__()
        self.on_saved = on_saved
        self.setWindowTitle(f"{APP_TITLE} {__version__}: settings")
        try:
            self.data = config.load_raw()
        except ConfigError as e:
            QMessageBox.warning(self, APP_TITLE, f"{e}\n\nStarting from the defaults.")
            self.data = config.load_raw(CONFIG_FILE.with_name("missing.toml"))
        self.first_run = not CONFIG_FILE.exists()
        self.wizard = self.first_run
        if self.first_run and (found := find_recorder_command()):
            self.data["recorder"]["command"] = found
        # What's in the file now: saving restarts the service only if this changed.
        self.saved_data = copy.deepcopy(self.data) if not self.first_run else None
        self.service_now = service.state()   # runs systemctl: once here, again after saving
        self.test_process = None
        self.capture_lister: QProcess | None = None
        self.checks: dict[str, QLabel] = {}
        self.insist = False   # an empty needed field is an error only after Next or Save
        # Checks wait for a pause in typing: the folder ones walk the folder.
        self.check_timer = QTimer(self)
        self.check_timer.setSingleShot(True)
        self.check_timer.setInterval(300)
        self.check_timer.timeout.connect(self.update_checks)

        # The pieces, built once; pages or wizard steps arrange them.
        self.parts = {
            "folders": self.folders_part(), "capture": self.capture_part(),
            "test": self.test_part(), "sound": self.sound_part(), "clips": self.clips_part(),
            "storage": self.storage_part(), "notify": self.notify_part(),
            "recorder": self.recorder_part(), "hook": self.hook_part(),
            "service": self.service_part(), "logging": self.logging_part(),
            "updates": self.updates_part(),
        }
        self.problems = QLabel()   # what no field shows: missing programs
        self.problems.setWordWrap(True)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton()
        self.save_button.setDefault(True)

        layout = QVBoxLayout(self)
        if self.wizard:
            self.build_wizard(layout)
        else:
            self.build_pages(layout)
        self.watch_changes()
        self._theming = False
        self.apply_theme()
        self.update_checks()
        self.fit_to_screen()

    # ── Layout ──

    def build_pages(self, layout: QVBoxLayout):
        p = self.parts
        pages = [
            [_heading("Folders"), p["folders"], _heading("What to record"), p["capture"],
             p["test"]],
            [_heading("Sound"), p["sound"]],
            [_heading("Clips"), p["clips"], _heading("Storage"), p["storage"],
             _heading("Clips window"), self.clips_window_part()],
            [_heading("Discord or Fluxer messages"), p["notify"]],
            [_heading("Recorder"), p["recorder"], _heading("After clips"), p["hook"],
             _heading("Background service"), p["service"], _heading("Logs"), p["logging"],
             _heading("Uninstall"), self.uninstall_part()],
            [_heading(APP_TITLE), self.about_part(), _heading("New versions"), p["updates"],
             _heading("License"), self.license_part()],
        ]
        self.sidebar = QListWidget()
        self.sidebar.addItems(PAGES)
        self.sidebar.setFixedWidth(max(200, self.sidebar.sizeHintForColumn(0) + 48))
        self.sidebar.setStyleSheet("QListWidget::item { padding: 7px 8px; }")
        self.stack = QStackedWidget()
        for parts in pages:
            inner = _section(*parts)
            inner.layout().addStretch(1)
            inner.layout().setContentsMargins(16, 8, 16, 8)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(inner)
            self.stack.addWidget(scroll)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        body = QHBoxLayout()
        body.addWidget(self.sidebar)
        body.addWidget(self.stack, 1)

        self.save_button.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.addWidget(_hint(f"Saved to {str(CONFIG_FILE).replace(str(Path.home()), '~', 1)}"), 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)
        layout.addLayout(body, 1)
        layout.addWidget(self.problems)
        layout.addLayout(buttons)

    def build_wizard(self, layout: QVBoxLayout):
        p = self.parts
        steps = [[p["folders"]], [p["capture"], _heading("Sound"), p["sound"]],
                 [p["test"], _heading("New versions"), p["updates"]]]
        self.step_title = _heading("")
        self.step_text = _hint()
        self.stack = QStackedWidget()
        for parts in steps:
            inner = _section(*parts)
            inner.layout().addStretch(1)
            self.stack.addWidget(inner)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(lambda: self.go_to_step(self.stack.currentIndex() - 1))
        self.next_button = QPushButton("Next")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(lambda: self.go_to_step(self.stack.currentIndex() + 1))
        self.save_button.setText("Finish")
        self.save_button.clicked.connect(self.save)
        buttons = QHBoxLayout()
        buttons.addWidget(_hint("You can change all of this, and more, in Settings later."), 1)
        for b in (self.cancel_button, self.back_button, self.next_button, self.save_button):
            buttons.addWidget(b)
        welcome = _heading(f"Set up {APP_TITLE}")
        font = welcome.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        welcome.setFont(font)
        layout.addWidget(welcome)
        layout.addWidget(self.step_title)
        layout.addWidget(self.step_text)
        layout.addSpacing(8)
        layout.addWidget(self.stack, 1)
        layout.addWidget(self.problems)
        layout.addLayout(buttons)
        self.go_to_step(0)

    def go_to_step(self, i: int):
        i = max(0, min(i, self.stack.count() - 1))
        if i > self.stack.currentIndex() and not self.step_ok(self.stack.currentIndex()):
            self.insist = True
            self.update_checks()
            return
        self.stack.setCurrentIndex(i)
        title, text = WIZARD[i]
        self.step_title.setText(f"Step {i + 1} of {len(WIZARD)}: {title}")
        self.step_text.setText(text)
        last = i == self.stack.count() - 1
        self.back_button.setEnabled(i > 0)
        self.next_button.setVisible(not last)
        self.save_button.setVisible(last)
        self.next_button.setDefault(not last)
        self.save_button.setDefault(last)
        self.update_buttons()

    def step_ok(self, i: int) -> bool:
        """The folders step needs both folders before going on."""
        if i != 0:
            return True
        checks = model.field_checks(self.collect())
        return all(checks[k].level != model.ERROR for k in ("combat_log_dir", "output_dir"))

    def fit_to_screen(self):
        width, height = (760, 600) if self.wizard else (980, 680)
        if screen := self.screen():
            free = screen.availableGeometry()
            width, height = min(width, free.width() - 40), min(height, free.height() - 40)
        self.resize(width, height)

    def apply_theme(self):
        """Colours from theme.py, set again when the desktop's theme changes."""
        self.setStyleSheet(f"QLabel[muted=\"true\"] {{ color: {theme.muted().name()}; }}")
        self.update_checks()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange and not getattr(self, "_theming", True):
            self._theming = True   # setStyleSheet sends palette changes of its own
            self.apply_theme()
            self._theming = False

    def watch_changes(self):
        """Any edit re-checks the fields and the Save button's words, after a pause."""
        for part in self.parts.values():
            for w in part.findChildren(QLineEdit):
                w.textChanged.connect(self.check_timer.start)
            for w in part.findChildren(QSpinBox):
                w.valueChanged.connect(self.check_timer.start)
            for w in part.findChildren(QComboBox):
                w.currentIndexChanged.connect(self.check_timer.start)
            for w in part.findChildren(QAbstractButton):
                if w.isCheckable():
                    w.toggled.connect(self.check_timer.start)

    def check_label(self, field: str) -> QLabel:
        label = _hint()
        label.setWordWrap(False)   # one short line; a wrapped one leaves gaps in a form
        self.checks[field] = label
        return label

    # ── The pieces ──

    def folder_row(self, value: str) -> tuple[QWidget, QLineEdit]:
        edit = QLineEdit(value)
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

    def folders_part(self) -> QWidget:
        p = self.data["paths"]
        form = _form()
        row, self.log_dir = self.folder_row(p["combat_log_dir"])
        self.log_dir.setPlaceholderText("…/World of Warcraft/_retail_/Logs")
        self.log_hint = self.check_label("combat_log_dir")
        form.addRow("Combat log folder", _field(row, self.log_hint))
        row, self.out_dir = self.folder_row(p["output_dir"])
        self.out_dir.setPlaceholderText("A folder with room: a few GB per hour of play")
        self.out_hint = self.check_label("output_dir")
        form.addRow("Output folder", _field(row, self.out_hint))
        return _section(form)

    def capture_part(self) -> QWidget:
        r = self.data["recorder"]
        form = _form()
        self.capture = QComboBox()
        self.capture.setEditable(True)   # any monitor name works, listed or not
        # don't widen the window to fit the longest option
        self.capture.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.capture.setMinimumContentsLength(12)
        # What "Record monitor instead" (tray, clips window) switches a session to.
        self.monitor = QComboBox()
        self.monitor.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.monitor.setMinimumContentsLength(12)
        self.monitor.activated.connect(self.flash_switch_monitor)
        # The defaults now; the recorder's own list when it answers (list_capture_options).
        self.fill_capture(DEFAULT_CAPTURE, r["capture"])
        self.list_capture_options()
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
        form.addRow("Record", row)
        self.monitor_hint = _hint("For \"Record monitor instead\" in the tray menu.")
        self.monitor_field = _field(self.monitor, self.monitor_hint)
        form.addRow("Monitor instead", self.monitor_field)
        self.monitor_label = form.labelForField(self.monitor_field)
        self.capture.currentIndexChanged.connect(lambda _: self.show_monitor_row())
        self.capture.editTextChanged.connect(lambda _: self.show_monitor_row())
        self.show_monitor_row()

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
        return _section(form)

    def test_part(self) -> QWidget:
        self.test_button = QPushButton("Save and record a 5-second test")
        self.test_button.clicked.connect(self.test_recorder)
        self.test_result = _hint()
        row = QHBoxLayout()
        row.addWidget(self.test_button)
        row.addStretch(1)
        return _section(row, self.test_result)

    def sound_part(self) -> QWidget:
        r = self.data["recorder"]
        box = QVBoxLayout()
        self.game_sound = QButtonGroup(self)
        current = config.game_sound(r["audio"])
        for i, (key, label) in enumerate(config.GAME_SOUND.items()):
            button = QRadioButton(label)
            button.setObjectName(f"sound_{key}")
            button.setChecked(key == current)
            self.game_sound.addButton(button, i)
            box.addWidget(button)
        self.mic = QCheckBox("Also record my microphone (as a track of its own)")
        self.mic.setChecked(any("default_input" in a for a in r["audio"]))
        box.addSpacing(6)
        box.addWidget(self.mic)
        return _section(box)

    def clips_part(self) -> QWidget:
        c = self.data["clips"]
        form = _form()
        self.min_run = _spin(c["min_run_minutes"], 0, 120, " min")
        form.addRow("Skip runs shorter than",
                    _field(self.min_run, _hint("Completed keys are always clipped, however short.")))
        self.pad_before = _spin(c["pad_before"], 0, 600, " s")
        form.addRow("Extra video before a run", self.pad_before)
        self.pad_after = _spin(c["pad_after"], 0, 600, " s")
        form.addRow("Extra video after a run", self.pad_after)
        self.raids = QComboBox()
        for value, label in config.RAID_CHOICES.items():
            self.raids.addItem(label, value)
        self.raids.setCurrentIndex(max(0, self.raids.findData(c["raids"])))
        form.addRow("Raid boss clips",
                    _field(self.raids, _hint("A clip per boss pull. Pulls under 30 s are skipped.")))
        return _section(form, _heading("Also show clips from"), self.other_folders_part())

    def clips_window_part(self) -> QWidget:
        self.window_parts = {}
        for key, label in config.CLIPS_WINDOW.items():
            box = QCheckBox(label)
            box.setChecked(bool(self.data["clips_window"].get(key, True)))
            box.toggled.connect(self.update_checks)
            self.window_parts[key] = box
        return _section(*self.window_parts.values())

    def other_folders_part(self) -> QWidget:
        """Folders on this computer the clip browser lists clips from, besides clips/."""
        self.other_folders = QListWidget()
        self.other_folders.setObjectName("other_folders")
        self.other_folders.addItems(self.data["clips"]["other_folders"])
        self.other_folders.setMaximumHeight(110)
        add = QPushButton("Add folder...")
        remove = QPushButton("Remove")

        def pick():
            chosen = QFileDialog.getExistingDirectory(self, "A folder with clips", str(Path.home()))
            if chosen and not self.other_folders.findItems(chosen, Qt.MatchExactly):
                self.other_folders.addItem(chosen)
                self.check_timer.start()

        def drop():
            for item in self.other_folders.selectedItems():
                self.other_folders.takeItem(self.other_folders.row(item))
            self.check_timer.start()
        add.clicked.connect(pick)
        remove.clicked.connect(drop)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        return _section(
            _hint("For clips you move somewhere else, e.g. with an After clips command. The clip "
                  "browser lists them too; if a clip's .json stayed in clips/, its deaths, lusts "
                  "and bosses come with it. Nothing in these folders is ever changed or deleted, "
                  "except by Move to trash."),
            self.other_folders, self.check_label("other_folders"), buttons)

    def storage_part(self) -> QWidget:
        s = self.data["storage"]
        form = _form()
        self.session_days = _spin(s["session_retention_days"], 0, 3650, " days", "Forever")
        form.addRow("Keep full recordings", self.session_days)
        self.clip_days = _spin(s["clip_retention_days"], 0, 3650, " days", "Forever")
        form.addRow("Keep clips", _field(self.clip_days, _hint(
            "Clips marked Keep in the clip browser are never deleted.")))
        self.min_free = _spin(s["min_free_gb"], 0, 100000, " GB free")
        form.addRow("Warn below", self.min_free)
        self.stop_below = _spin(s["stop_below_gb"], 0, 100000, " GB free")
        form.addRow("Stop recording below", _field(self.stop_below, self.check_label("stop_below_gb")))
        return _section(form)

    def notify_part(self) -> QWidget:
        d = self.data["discord"]
        intro = _hint("Discord: Server Settings > Integrations > Webhooks > New Webhook > Copy "
                      "Webhook URL. Fluxer: the channel's or community's settings > Webhooks. "
                      f"Up to {MAX_WEBHOOKS}; every message goes to each one. None: no messages.")
        self.webhook_rows = QVBoxLayout()
        self.webhooks: list[QLineEdit] = []
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
        for url in d["webhook_urls"]:
            self.add_webhook(url)
        self.webhook_changed()
        events = QVBoxLayout()
        self.events: dict[str, QCheckBox] = {}
        for key, (default, desc) in NOTIFY_EVENTS.items():
            box = QCheckBox(desc)
            box.setChecked(bool(d["notify"].get(key, default)))
            self.events[key] = box
            events.addWidget(box)
        return _section(intro, self.webhook_rows, self.check_label("webhooks"), row,
                        _heading("Send a message when"), events)

    def recorder_part(self) -> QWidget:
        form = _form()
        self.command = QLineEdit(self.data["recorder"]["command"])
        form.addRow("Recorder command", _field(
            self.command, self.check_label("recorder_command"),
            _hint("gpu-screen-recorder, or the Flatpak's \"flatpak run "
                  "com.dec05eba.gpu_screen_recorder\".")))
        return _section(form)

    def hook_part(self) -> QWidget:
        form = _form()
        self.hook = QLineEdit(self.data["hooks"]["after_clips"])
        self.hook.setPlaceholderText("~/bin/upload-clips.sh")
        form.addRow("Command", _field(
            self.hook, self.check_label("after_clips"),
            _hint("Run after each session with the new clips' paths added as arguments, e.g. a "
                  "script that copies them to a server.")))
        return _section(form)

    def service_part(self) -> QWidget:
        self.service_state = _hint()
        self.service_at_login = QCheckBox("Record at every login (systemd user service)")
        self.service_at_login.setChecked(self.first_run or self.service_now != "not installed")
        self.tray_at_login = QCheckBox("Show the tray icon at login")
        self.tray_at_login.setChecked(self.first_run or desktop.autostart_enabled())
        self.update_service_state()
        return _section(self.service_state, self.service_at_login, self.tray_at_login)

    def logging_part(self) -> QWidget:
        self.detailed = QCheckBox("Detailed logging (for tracking down a problem)")
        self.detailed.setChecked(bool(self.data["logging"]["detailed"]))
        report = QPushButton("Make a report file...")
        report.clicked.connect(self.make_report)
        row = QHBoxLayout()
        row.addWidget(report)
        row.addStretch(1)
        return _section(self.detailed, row,
                        _hint("Logs stay on this computer; nothing is ever sent. The report file "
                              "is for you to share by hand if you want help."))

    def uninstall_part(self) -> QWidget:
        button = QPushButton("Uninstall...")
        button.clicked.connect(self.uninstall)
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch(1)
        return _section(row)

    def uninstall(self):
        """Asks what to remove, then runs `uninstall` in a process of its own (stopping the
        service can take a while: the recording in progress is saved first) and quits."""
        dialog = UninstallDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        args = ["-m", "linux_wow_recorder", "uninstall", "--yes"]
        if dialog.delete_recordings.isChecked():
            args.append("--delete-recordings")
        if dialog.remove_program is None or not dialog.remove_program.isChecked():
            args.append("--keep-program")
        self.setEnabled(False)
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(lambda *_: self.uninstalled(process, dialog.program))
        process.start(sys.executable, args)

    def uninstalled(self, process: QProcess, prog):
        out = bytes(process.readAll()).decode(errors="replace")
        lines = [l for l in out.splitlines() if "[INFO]" not in l and "[WARNING]" not in l]
        if process.exitCode() != 0:
            QMessageBox.warning(self, APP_TITLE, "Uninstalling didn't finish:\n\n"
                                + "\n".join(lines[-8:]))
            self.setEnabled(True)
            return
        text = f"{APP_TITLE} is uninstalled."
        if prog.needs_root:
            text += f"\n\nTo remove the program itself, run:\n{prog.text}"
        QMessageBox.information(self, APP_TITLE, text)
        QApplication.instance().quit()

    def about_part(self) -> QWidget:
        version = QLabel(f"Version {__version__}")
        font = version.font()
        font.setBold(True)
        version.setFont(font)
        version.setTextInteractionFlags(Qt.TextSelectableByMouse)
        links = QLabel(f'<a href="{REPO_URL}">Project page</a> · '
                       f'<a href="{REPO_URL}/releases">Releases</a> · '
                       f'<a href="{REPO_URL}/issues">Report a problem</a>')
        links.setOpenExternalLinks(True)
        from PySide6 import QtCore
        qt = f"PySide6 {getattr(QtCore, '__version__', '?')} (Qt {QtCore.qVersion()})"
        return _section(
            version,
            _hint("Records World of Warcraft on Linux and cuts one clip per dungeon run."),
            links,
            _hint(f"Python {sys.version.split()[0]}, {qt}. A report file "
                  "(Advanced > Make a report file) has these and more, for a problem report."),
            _hint("No telemetry or crash reports. All it sends is your own webhook messages and, "
                  "if you turn it on, a daily check for a new version."))

    def updates_part(self) -> QWidget:
        self.check_updates = QCheckBox("Check for new versions once a day")
        # Setup shows it ticked, so it's a choice made there; an existing config keeps its own.
        self.check_updates.setChecked(True if self.first_run else bool(self.data["updates"]["check"]))
        self.check_now = QPushButton("Check now")
        self.check_now.clicked.connect(self.start_update_check)
        self.update_result = QLabel()
        self.update_result.setOpenExternalLinks(True)
        self.update_check: updates.Check | None = None
        self.update_timer = QTimer(self, interval=300)
        self.update_timer.timeout.connect(self.update_checked)
        row = QHBoxLayout()
        row.addWidget(self.check_now)
        row.addWidget(self.update_result, 1)
        return _section(self.check_updates, row, _hint(
            "Asks GitHub for the newest version number; nothing about you is sent. "
            "Installing it is up to you."))

    def start_update_check(self):
        self.check_now.setEnabled(False)
        self.update_result.setText("Checking...")
        self.update_check = updates.Check()
        self.update_timer.start()

    def update_checked(self):
        if not self.update_check or not self.update_check.done:
            return
        self.update_timer.stop()
        self.check_now.setEnabled(True)
        found, self.update_check = self.update_check.version, None
        if found is None:
            self.update_result.setText("Couldn't reach GitHub.")
        elif updates.is_newer(found, __version__):
            self.update_result.setText(f'Version {found} is out: <a href="{updates.RELEASES_PAGE}">'
                                       'release page</a>')
        else:
            self.update_result.setText("This is the newest version.")

    def license_part(self) -> QWidget:
        intro = _hint(
            "Free software under the GNU General Public License, version 3 or (at your option) "
            "any later version. You may use, share and change it; if you share it, changed or "
            "not, share its source under the same license. It comes with NO WARRANTY, to the "
            "extent the law allows.")
        text = model.license_text()
        if text is None:
            full = QLabel('The full text: <a href="https://www.gnu.org/licenses/gpl-3.0.html">'
                          'gnu.org/licenses/gpl-3.0.html</a>')
            full.setOpenExternalLinks(True)
        else:
            full = QPlainTextEdit(text, readOnly=True)
            full.setObjectName("license")
            full.setMinimumHeight(320)
        return _section(intro, full)

    # ── Capture options ──

    def explain_portal(self):
        QMessageBox.information(self, "A window or screen you pick", PORTAL_INFO)

    def flash_monitor(self, i: int):
        """Big red "THIS ONE" on the monitor just picked, so DP-1/DP-2 aren't guesswork."""
        options = [self.capture.itemData(j) for j in range(self.capture.count())]
        monitor = model.capture_monitor(self.capture.itemData(i), options)
        if monitor:
            self.monitor_flash.flash(monitor)

    def flash_switch_monitor(self, i: int):
        options = [self.monitor.itemData(j) for j in range(self.monitor.count())]
        if monitor := model.capture_monitor(self.monitor.itemData(i) or "screen", options):
            self.monitor_flash.flash(monitor)

    def show_monitor_row(self):
        """The monitor to switch to only matters while a window is picked in the dialog."""
        shown = self.capture_value() == "portal"
        self.monitor_field.setVisible(shown)
        if self.monitor_label:
            self.monitor_label.setVisible(shown)

    def fill_monitor(self, options: list[tuple[str, str]], current: str):
        """The capture options without the portal; "" (the config's default) is "screen"."""
        self.monitor.clear()
        for value, label in options:
            if value != "portal":
                self.monitor.addItem(label, "" if value == "screen" else value)
        i = self.monitor.findData(current)
        if i < 0:
            self.monitor.addItem(current, current)
            i = self.monitor.count() - 1
        self.monitor.setCurrentIndex(i)

    def fill_capture(self, options: list[tuple[str, str]], current: str):
        self.fill_monitor(options, self.monitor.currentData() if self.monitor.count()
                          else self.data["recorder"]["monitor"])
        self.capture.blockSignals(True)
        self.capture.clear()
        for value, label in options:
            self.capture.addItem(label, value)
        i = self.capture.findData(current)
        if i < 0:
            self.capture.addItem(current, current)
            i = self.capture.count() - 1
        self.capture.setCurrentIndex(i)
        # an editable box shows the end of a long label; show its start
        self.capture.lineEdit().setCursorPosition(0)
        self.capture.blockSignals(False)

    def list_capture_options(self):
        """Asks the recorder for its capture options without blocking the window: the
        Flatpak takes a few seconds to start."""
        try:
            cmd = shlex.split(self.data["recorder"]["command"])
        except ValueError:
            return
        if not cmd:
            return
        process = self.capture_lister = QProcess(self)
        process.finished.connect(lambda *_: self.capture_options_listed(process))
        process.start(cmd[0], [*cmd[1:], "--list-capture-options"])
        QTimer.singleShot(10_000, process.kill)   # a no-op once it has finished

    def capture_options_listed(self, process: QProcess):
        try:
            out = bytes(process.readAllStandardOutput()).decode(errors="replace")
        except RuntimeError:
            return   # the window closed before the recorder answered; its process went with it
        self.capture_lister = None
        if options := model.parse_capture_options(out):
            self.fill_capture(options, self.capture_value())

    def capture_value(self) -> str:
        # An item picked from the list has its value as data; typed text is the value itself.
        i = self.capture.currentIndex()
        typed = self.capture.currentText().strip()
        return (self.capture.itemData(i) if i >= 0 and self.capture.itemText(i) == typed
                else typed) or "portal"

    # ── Webhooks ──

    def add_webhook(self, url: str = "") -> QLineEdit:
        edit = QLineEdit(url)
        edit.setPlaceholderText("https://discord.com/api/webhooks/... or a Fluxer webhook URL")
        edit.textChanged.connect(self.check_timer.start)
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
        self.check_timer.start()

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

    # ── Reading the form ──

    def collect(self) -> dict:
        """The config the form shows, as a new dict shaped like config.DEFAULTS."""
        d = copy.deepcopy(self.data)
        d["paths"]["combat_log_dir"] = self.log_dir.text().strip()
        d["paths"]["output_dir"] = self.out_dir.text().strip()
        r = d["recorder"]
        r["command"] = self.command.text().strip()
        r["capture"] = self.capture_value()
        r["monitor"] = self.monitor.currentData() or ""
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
        c["raids"] = self.raids.currentData()
        c["pad_before"] = self.pad_before.value()
        c["pad_after"] = self.pad_after.value()
        c["other_folders"] = [self.other_folders.item(i).text()
                              for i in range(self.other_folders.count())]
        d["discord"]["webhook_urls"] = [e.text().strip() for e in self.webhooks
                                        if e.text().strip()]
        d["discord"]["notify"] = {k: b.isChecked() for k, b in self.events.items()}
        d["hooks"]["after_clips"] = self.hook.text().strip()
        d["logging"]["detailed"] = self.detailed.isChecked()
        d["updates"]["check"] = self.check_updates.isChecked()
        if hasattr(self, "window_parts"):   # not in the first-run wizard
            d["clips_window"] = {k: b.isChecked() for k, b in self.window_parts.items()}
        return d

    def update_checks(self):
        """The line under each checked field, the sidebar's marks, and the Save button."""
        if not hasattr(self, "save_button"):
            return   # still being built
        data = self.collect()
        found = model.field_checks(data)
        for field, label in self.checks.items():
            check = found.get(field)
            label.setVisible(check is not None)
            if check and check.level == model.ERROR and check.text.startswith("Needed") \
                    and not self.insist:
                check = model.Check(model.INFO, check.text)
            if check:
                colour = (theme.color("good") if check.level == model.OK else
                          theme.color("warning") if check.level == model.WARNING else
                          theme.color("error") if check.level == model.ERROR else theme.muted())
                text = theme.muted() if check.level in (model.OK, model.INFO) else colour
                label.setText(f"<span style='color:{colour.name()}'>{_MARK[check.level]}</span>"
                              f"&nbsp;<span style='color:{text.name()}'>{_html(check.text)}</span>")
        if hasattr(self, "sidebar"):
            bad = {FIELD_PAGE[f] for f, c in found.items()
                   if c.level in (model.ERROR, model.WARNING) and f in FIELD_PAGE}
            for i, name in enumerate(PAGES):
                self.sidebar.item(i).setText(f"{name}  ⚠" if i in bad else name)
        missing = model.missing_tools()
        self.problems.setVisible(bool(missing))
        self.problems.setText(f"<span style='color:{theme.color('error').name()}'>✕ Not installed: "
                              f"{', '.join(missing)}. The recorder needs "
                              f"{'them' if len(missing) > 1 else 'it'} to cut clips.</span>")
        self.update_buttons(data)

    def changed(self, data: dict | None = None) -> bool:
        """Whether the form differs from the config file (which the service read)."""
        return self.saved_data is None or (data or self.collect()) != self.saved_data

    def service_changed(self, data: dict | None = None) -> bool:
        """changed(), leaving out the settings only the desktop app reads."""
        if self.saved_data is None:
            return True
        a, b = copy.deepcopy(data or self.collect()), copy.deepcopy(self.saved_data)
        for section, key in APP_ONLY:
            a[section].pop(key, None)
            b[section].pop(key, None)
        return a != b

    def needs_restart(self, data: dict | None = None) -> bool:
        """Saving restarts the service if it's to run and the config changed, or starts it if
        it isn't running. The two login checkboxes alone don't."""
        if not self.service_at_login.isChecked():
            return False
        running = self.service_now in ("active", "activating")
        return self.service_changed(data) or not running

    def update_buttons(self, data: dict | None = None):
        if self.wizard:
            return
        running = self.service_now in ("active", "activating")
        if self.needs_restart(data):
            self.save_button.setText("Save and restart service" if running else "Save and start")
        else:
            self.save_button.setText("Save")

    def update_service_state(self):
        self.service_state.setText(f"The service is {self.service_now}.")

    # ── Buttons ──

    def write(self) -> bool:
        data = self.collect()
        out = data["paths"]["output_dir"]
        if not data["paths"]["combat_log_dir"] or not out:
            self.insist = True
            self.update_checks()
            if not self.wizard:
                self.sidebar.setCurrentRow(0)
            QMessageBox.warning(self, APP_TITLE, "Both folders are needed.")
            return False
        try:
            config.from_dict(data)
            Path(out).expanduser().mkdir(parents=True, exist_ok=True)
            config.save(data)
        except (ConfigError, OSError) as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't save: {e}")
            return False
        self.data, self.first_run = data, False
        return True

    def save(self):
        data = self.collect()
        restart = self.wizard or self.needs_restart(data)
        status = state.read()
        if restart and status and status.get("state") == state.RECORDING and QMessageBox.question(
                self, APP_TITLE, "WoW is being recorded. Saving restarts the service: the "
                "recording so far is saved and cut after the next session, and a new one "
                "starts. Save now?") != QMessageBox.Yes:
            return
        if self.changed(data) and not self.write():
            return
        self.saved_data = copy.deepcopy(self.data)
        try:
            if self.service_at_login.isChecked():
                if restart:
                    service.install()   # (re)writes the unit and restarts it with the new config
            elif self.service_now != "not installed":
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

    def test_recorder(self):
        if self.test_process or not self.write():
            return
        self.saved_data = copy.deepcopy(self.data)
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
        self.service_now = service.state()
        self.update_service_state()
        self.update_checks()

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


class UninstallDialog(QDialog):
    """What uninstalling removes, with the two choices: the recordings, and the program."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(f"Uninstall {APP_TITLE}")
        try:
            cfg = config.load()
        except (ConfigError, OSError):
            cfg = None
        self.program = uninstall.program()
        folders = uninstall.recording_folders(cfg)
        layout = QVBoxLayout(self)
        intro = QLabel(f"This stops {APP_TITLE} and removes its service, login and menu "
                       "entries, settings and logs.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if (state.read() or {}).get("state") == state.RECORDING:
            layout.addWidget(QLabel("WoW is being recorded: the recording so far is saved first."))
        self.delete_recordings = QCheckBox(
            f"Also delete my recordings and clips ({uninstall.size_gb(folders):.1f} GB in "
            f"{cfg.output_dir})" if folders else "Also delete my recordings and clips")
        self.delete_recordings.setEnabled(bool(folders))
        layout.addWidget(self.delete_recordings)
        self.remove_program = None
        if self.program.needs_root:
            then = QLabel(f"Then remove the program with: <code>{_html(self.program.text)}</code>")
            then.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(then)
        elif self.program.kind != "other":
            self.remove_program = QCheckBox(f"Also remove the program ({self.program.text})")
            self.remove_program.setChecked(True)
            layout.addWidget(self.remove_program)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel, go = QPushButton("Cancel"), QPushButton("Uninstall")
        cancel.setDefault(True)   # an Enter by mistake doesn't uninstall
        cancel.clicked.connect(self.reject)
        go.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(go)
        layout.addSpacing(8)
        layout.addLayout(buttons)


def _html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
