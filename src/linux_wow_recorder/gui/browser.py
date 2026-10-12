"""The clip browser: the clips list, a player, and a timeline of bosses, deaths and lusts."""

import os
import shlex
import time

from PySide6.QtCore import QFileSystemWatcher, QLineF, QProcess, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QButtonGroup, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSlider, QSplitter, QStackedWidget, QStyle, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import APP_TITLE, config, live, state
from ..clips import read_sidecar, set_keep, sidecar_path
from ..config import ConfigError
from . import model

MARKER_COLORS = {"kill": "#2E9BD6", "wipe": "#8A919C", "pull": "#8A919C", "death": "#E5484D",
                 "lust": "#E8A33D", "start": "#8A919C"}
COLUMNS = ["Date", "Dungeon", "Level", "Result", "Length", "Deaths", "Kept"]
PREVIEW_MS = 3000


class Timeline(QWidget):
    """The clip's length as a bar, the played part filled, with a tick per marker. Clicking
    seeks there; hovering a tick names it."""
    seek = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(34)
        self.setMouseTracking(True)
        self.length = 0.0
        self.position = 0.0
        self.markers: list[model.Marker] = []

    def sizeHint(self) -> QSize:
        return QSize(400, 34)

    def set_clip(self, length: float, markers: list[model.Marker]):
        self.length, self.markers, self.position = length, markers, 0.0
        self.update()

    def set_position(self, seconds: float):
        self.position = seconds
        self.update()

    def _x(self, seconds: float) -> float:
        return 8 + (self.width() - 16) * (seconds / self.length if self.length else 0)

    def _seconds(self, x: float) -> float:
        return max(0.0, min(self.length, (x - 8) / max(1, self.width() - 16) * self.length))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        mid = self.height() / 2
        bar = QRectF(8, mid - 3, self.width() - 16, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(self.palette().mid())
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(self.palette().highlight())
        p.drawRoundedRect(QRectF(8, mid - 3, self._x(self.position) - 8, 6), 3, 3)
        for m in self.markers:
            x = self._x(m.at)
            p.setPen(QPen(QColor(MARKER_COLORS[m.kind]), 3 if m.kind in ("kill", "lust") else 2))
            tall = m.kind in ("kill", "wipe", "lust")
            p.drawLine(QLineF(x, mid - (13 if tall else 8), x, mid + (13 if tall else 8)))
        p.end()

    def mousePressEvent(self, event):
        if self.length:
            # Snap to a marker within 6 px, so the ticks are easy to hit.
            x = event.position().x()
            near = [m for m in self.markers if abs(self._x(m.at) - x) <= 6]
            self.seek.emit(near[0].at if near else self._seconds(x))

    def mouseMoveEvent(self, event):
        x = event.position().x()
        near = [m.text for m in self.markers if abs(self._x(m.at) - x) <= 6]
        self.setToolTip(" · ".join(near) if near else model.mmss(self._seconds(x)))


class ClipBrowser(QWidget):
    closed = Signal()   # the window was closed; the app stays in the tray
    settings_requested = Signal()
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE}: clips")
        self.resize(1400, 860)
        self.clips: list[model.ClipInfo] = []
        self.current: model.ClipInfo | None = None
        self.hook_process = None
        try:
            self.cfg = config.load()
        except ConfigError as e:
            self.cfg = None
            QMessageBox.warning(self, APP_TITLE, str(e))

        # ── Left: filters and the list ──
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search dungeons")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill_list)
        self.filter_group = QButtonGroup(self)
        filters = QHBoxLayout()
        for key, label in model.FILTERS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("key", key)
            b.setChecked(key == "all")
            self.filter_group.addButton(b)
            filters.addWidget(b)
        self.filter_group.buttonClicked.connect(lambda _: self.fill_list())
        filters.addStretch(1)
        settings = QPushButton("Settings...")
        settings.clicked.connect(self.settings_requested)
        filters.addWidget(settings)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.DescendingOrder)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.currentItemChanged.connect(self.on_select)
        self.tree.itemActivated.connect(lambda *_: self.player.play())
        self.count = QLabel()

        # While WoW is being recorded: its newest frame, to check it records the right thing.
        self.live_box = QGroupBox("Recording now")
        self.live_image = QLabel()
        self.live_image.setAlignment(Qt.AlignCenter)
        self.live_image.setMinimumHeight(120)
        self.live_caption = QLabel()
        self.live_caption.setStyleSheet("color: palette(placeholder-text);")
        live_layout = QVBoxLayout(self.live_box)
        live_layout.addWidget(self.live_image)
        live_layout.addWidget(self.live_caption)
        self.live_box.hide()
        self.live_pixmap = QPixmap()
        self.live_process = None
        self.live_timer = QTimer(self, interval=PREVIEW_MS)
        self.live_timer.timeout.connect(self.update_live)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.live_box)
        lv.addWidget(self.search)
        lv.addLayout(filters)
        lv.addWidget(self.tree, 1)
        lv.addWidget(self.count)

        # ── Right: player, timeline, details, actions ──
        self.video = QVideoWidget()
        self.video.setMinimumSize(480, 270)
        self.audio = QAudioOutput()
        self.player = QMediaPlayer()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_state)
        self.player.errorOccurred.connect(lambda _e, msg: self.time_label.setText(msg))
        # With no clip loaded, QVideoWidget still paints letterbox bars for a video that isn't
        # there, so a plain panel stands in for it.
        self.no_video = QLabel()
        self.no_video.setAlignment(Qt.AlignCenter)
        self.no_video.setWordWrap(True)
        self.no_video.setStyleSheet("background: black; color: #8A919C; font-size: 15px;")
        self.video_stack = QStackedWidget()
        self.video_stack.addWidget(self.video)
        self.video_stack.addWidget(self.no_video)

        self.play_button = QPushButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setFixedWidth(44)
        self.play_button.clicked.connect(self.toggle_play)
        self.timeline = Timeline()
        self.timeline.seek.connect(lambda s: self.player.setPosition(int(s * 1000)))
        self.time_label = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.timeline, 1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.volume)

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.title.setWordWrap(True)
        self.facts = QLabel()
        self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.facts.setWordWrap(True)
        self.events = QListWidget()
        self.events.setMaximumHeight(170)
        self.events.itemActivated.connect(self.seek_to_event)
        self.events.itemClicked.connect(self.seek_to_event)

        self.keep_button = QPushButton("Keep forever")
        self.keep_button.setCheckable(True)
        self.keep_button.clicked.connect(self.toggle_keep)
        buttons = QHBoxLayout()
        for text, slot in (("Open in video player", self.open_external),
                           ("Show in folder", self.show_in_folder)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            buttons.addWidget(b)
        buttons.addWidget(self.keep_button)
        self.hook_button = QPushButton("Run after_clips again")
        self.hook_button.clicked.connect(self.run_hook)
        buttons.addWidget(self.hook_button)
        buttons.addStretch(1)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete)
        buttons.addWidget(delete)
        self.action_widgets = [*(buttons.itemAt(i).widget() for i in range(buttons.count())
                                 if buttons.itemAt(i).widget())]

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.video_stack, 1)
        rv.addLayout(controls)
        rv.addWidget(self.title)
        rv.addWidget(self.facts)
        rv.addWidget(self.events)
        rv.addLayout(buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([560, 840])
        layout = QVBoxLayout(self)
        layout.addWidget(split)

        # New and deleted clips show up by themselves.
        self.watcher = QFileSystemWatcher(self)
        self.reload_timer = QTimer(self, singleShot=True, interval=1500)
        self.reload_timer.timeout.connect(self.reload)
        self.watcher.directoryChanged.connect(lambda _: self.reload_timer.start())
        self.reload()

    # ── Live preview ──

    def update_live(self):
        status = state.read() or {}
        path = status.get("recording_file") if status.get("state") == state.RECORDING else None
        if not path:
            self.live_box.hide()
            self.live_pixmap = QPixmap()
            return
        self.live_box.show()
        if self.live_process is not None:
            return   # the last frame is still being decoded
        data = live.preview_input(path)
        if data is None:
            self.live_caption.setText("Waiting for the first seconds of video...")
            return
        self.live_process = QProcess(self)
        self.live_process.finished.connect(self.live_frame_ready)
        cmd = live.ffmpeg_command()
        self.live_process.start(cmd[0], cmd[1:])
        self.live_process.write(data)
        self.live_process.closeWriteChannel()

    def live_frame_ready(self, *_args):
        proc, self.live_process = self.live_process, None
        pixmap = QPixmap()
        if pixmap.loadFromData(bytes(proc.readAllStandardOutput())):
            self.live_pixmap = pixmap
            self.show_live_pixmap()
            self.live_caption.setText(f"Updated {time.strftime('%H:%M:%S')}, a few seconds behind live")
        else:
            self.live_caption.setText("Couldn't read the recording yet")
        proc.deleteLater()

    def show_live_pixmap(self):
        if not self.live_pixmap.isNull():
            w = max(160, self.live_box.contentsRect().width() - 20)
            self.live_image.setPixmap(self.live_pixmap.scaledToWidth(w, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.show_live_pixmap()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_live()
        self.live_timer.start()

    # ── The list ──

    def reload(self):
        selected = self.current.path if self.current else None
        try:
            self.cfg = config.load()   # Settings may have moved the output folder
        except ConfigError:
            pass
        if self.cfg and self.cfg.clips_dir.is_dir() and str(self.cfg.clips_dir) not in self.watcher.directories():
            for d in self.watcher.directories():
                self.watcher.removePath(d)
            self.watcher.addPath(str(self.cfg.clips_dir))
        self.clips = model.load_clips(self.cfg.clips_dir, read_sidecar) if self.cfg else []
        self.fill_list(select=selected)

    def fill_list(self, *_args, select=None):
        select = select or (self.current.path if self.current else None)
        key = self.filter_group.checkedButton().property("key")
        shown = [c for c in self.clips if model.matches(c, key, self.search.text().strip())]
        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        chosen = None
        for c in shown:
            item = SortItem([
                time.strftime("%Y-%m-%d %H:%M", time.localtime(c.started)),
                c.dungeon,
                f"+{c.level}" if c.level else "",
                c.label.split()[-1] if c.label else "",
                c.length,
                "" if c.deaths is None else str(c.deaths),
                "★" if c.keep else "",
            ])
            item.setData(0, Qt.UserRole, str(c.path))
            item.setData(0, Qt.UserRole + 1, c.started)
            item.setData(2, Qt.UserRole + 1, c.level or 0)
            item.setData(5, Qt.UserRole + 1, c.deaths or 0)
            self.tree.addTopLevelItem(item)
            if select and c.path == select:
                chosen = item
        self.tree.setSortingEnabled(True)
        for col in (0, 2, 3, 4, 5, 6):
            self.tree.resizeColumnToContents(col)
        if chosen is None and self.tree.topLevelItemCount():
            chosen = self.tree.topLevelItem(0)
        if chosen is not None:
            self.tree.setCurrentItem(chosen)
        self.tree.blockSignals(False)
        self.count.setText(f"{len(shown)} of {len(self.clips)} clips")
        self.on_select(chosen, None)

    def on_select(self, item, _previous):
        path = item.data(0, Qt.UserRole) if item else None
        clip = next((c for c in self.clips if str(c.path) == path), None)
        same_file = clip and self.current and clip.path == self.current.path
        self.current = clip
        for w in self.action_widgets:
            w.setEnabled(clip is not None)
        if not clip:
            self.player.setSource(QUrl())
            self.no_video.setText("Clips show up here after your first dungeon run." if not self.clips
                                  else "Pick a clip from the list.")
            self.video_stack.setCurrentWidget(self.no_video)
            self.title.setText("No clips yet" if not self.clips else "No clips match")
            self.facts.setText("")
            self.events.clear()
            self.timeline.set_clip(0, [])
            return
        self.video_stack.setCurrentWidget(self.video)
        if not same_file:
            self.player.setSource(QUrl.fromLocalFile(str(clip.path)))
        self.show_details(clip)

    def show_details(self, clip: model.ClipInfo):
        d = clip.details
        level = f" +{clip.level}" if clip.level else ""
        result = clip.label.split()[-1] if clip.label else ""
        self.title.setText(f"{clip.dungeon}{level} · {result}")
        facts = [time.strftime("%a %Y-%m-%d %H:%M", time.localtime(clip.started)),
                 f"run {clip.length}"]
        if clip.bosses_killed is not None:
            bosses = {e["name"] for e in d.get("encounters", [])}
            facts.append(f"{clip.bosses_killed}/{len(bosses)} bosses killed" if bosses
                         else "no bosses")
        if clip.deaths is not None:
            facts.append(f"{clip.deaths} death{'s' if clip.deaths != 1 else ''}")
        if d.get("lusts"):
            facts.append(f"{len(d['lusts'])} lust{'s' if len(d['lusts']) != 1 else ''}")
        try:
            size = clip.path.stat().st_size
            facts.append(f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB")
        except OSError:
            pass
        self.facts.setText(" · ".join(facts) + f"\n{clip.path}")
        self.events.clear()
        found = model.markers(d)
        for m in found:
            item = QListWidgetItem(f"{model.mmss(m.at)}   {m.text}")
            item.setData(Qt.UserRole, m.at)
            item.setForeground(QColor(MARKER_COLORS[m.kind]) if m.kind in ("death", "lust", "kill")
                               else self.palette().text().color())
            self.events.addItem(item)
        if not found:
            self.events.addItem("No boss, death or lust details for this clip "
                                "(cut by an older version, or advanced combat logging off).")
        self.timeline.set_clip(d.get("clip_length") or self.player.duration() / 1000, found)
        self.keep_button.setChecked(clip.keep)
        self.keep_button.setText("★ Kept forever" if clip.keep else "Keep forever")
        self.hook_button.setEnabled(bool(self.cfg and self.cfg.after_clips))

    # ── Player ──

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_state(self, s):
        icon = QStyle.SP_MediaPause if s == QMediaPlayer.PlayingState else QStyle.SP_MediaPlay
        self.play_button.setIcon(self.style().standardIcon(icon))

    def on_position(self, ms: int):
        self.timeline.set_position(ms / 1000)
        self.time_label.setText(f"{model.mmss(ms / 1000)} / {model.mmss(self.player.duration() / 1000)}")

    def on_duration(self, ms: int):
        if ms and self.current and not self.current.details.get("clip_length"):
            self.timeline.set_clip(ms / 1000, self.timeline.markers)
        self.on_position(self.player.position())

    def seek_to_event(self, item: QListWidgetItem):
        at = item.data(Qt.UserRole)
        if at is not None:
            self.player.setPosition(int(max(0, at - 3) * 1000))   # a few seconds of lead-up
            self.player.play()

    def keyPressEvent(self, event):
        k = event.key()
        if k == Qt.Key_Space:
            self.toggle_play()
        elif k in (Qt.Key_Left, Qt.Key_Right) and self.player.duration():
            step = -5000 if k == Qt.Key_Left else 5000
            self.player.setPosition(max(0, self.player.position() + step))
        else:
            super().keyPressEvent(event)

    # ── Actions ──

    def open_external(self):
        self.player.pause()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current.path)))

    def show_in_folder(self):
        # The file manager's "show this file" over D-Bus where there is one; the folder otherwise.
        ok = QProcess.startDetached("dbus-send", [
            "--session", "--dest=org.freedesktop.FileManager1", "--type=method_call",
            "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{QUrl.fromLocalFile(str(self.current.path)).toString()}", "string:"])
        if not ok:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current.path.parent)))

    def toggle_keep(self):
        clip = self.current
        try:
            set_keep(clip.path, not clip.keep)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't save: {e}")
        self.reload()

    def run_hook(self):
        if not self.cfg or not self.cfg.after_clips or self.hook_process:
            return
        cmd = shlex.split(self.cfg.after_clips)
        cmd[0] = os.path.expanduser(cmd[0])
        self.hook_button.setEnabled(False)
        self.hook_button.setText("Running...")
        self.hook_process = QProcess(self)
        self.hook_process.setProcessChannelMode(QProcess.MergedChannels)
        self.hook_process.finished.connect(self.hook_finished)
        self.hook_process.errorOccurred.connect(lambda _e: self.hook_finished())
        self.hook_process.start(cmd[0], [*cmd[1:], str(self.current.path)])

    def hook_finished(self, *_args):
        p = self.hook_process
        if p is None:
            return
        self.hook_process = None
        out = bytes(p.readAll()).decode(errors="replace").strip()
        ok = p.exitStatus() == QProcess.NormalExit and p.exitCode() == 0 and p.error() != QProcess.FailedToStart
        self.hook_button.setText("Run after_clips again")
        self.hook_button.setEnabled(True)
        if ok:
            QMessageBox.information(self, APP_TITLE, "The after_clips command finished."
                                    + (f"\n\n{out[-1500:]}" if out else ""))
        else:
            QMessageBox.warning(self, APP_TITLE, "The after_clips command failed."
                                + (f"\n\n{out[-1500:]}" if out else f"\n\n{p.errorString()}"))

    def delete(self):
        clip = self.current
        if QMessageBox.question(self, APP_TITLE, f"Delete this clip for good?\n\n{clip.path.name}"
                                ) != QMessageBox.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        try:
            clip.path.unlink()
            sidecar_path(clip.path).unlink(missing_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't delete: {e}")
        self.current = None
        self.reload()

    def closeEvent(self, event):
        self.player.pause()
        self.live_timer.stop()
        super().closeEvent(event)
        self.closed.emit()


class SortItem(QTreeWidgetItem):
    """Sorts by the number stored in UserRole + 1 where there is one (dates, levels, deaths)."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn()
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col) < other.text(col)
