"""The clip browser: the clips list, a player, and a timeline of bosses, deaths and lusts."""

import html
import os
import shlex
import time
from pathlib import Path

from PySide6.QtCore import (QEvent, QFile, QFileSystemWatcher, QLineF, QPointF, QProcess, QRectF,
                            QSettings, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFrame, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QSlider, QSplitter, QStackedWidget, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import APP_TITLE, __version__, config, live, service, state
from ..clips import (read_sidecar, set_keep, sidecar_path, thumbnail_command, thumbnail_path,
                     thumbnail_time)
from ..config import CONFIG_DIR
from ..config import ConfigError
from . import model, theme

COLUMNS = ["Date", "Dungeon", "Level", "Result", "Length", "Deaths", "Kept"]
SESSION_COLUMNS = ["Date", "Dungeons", "Level", "Runs", "Length", "Deaths", "Kept"]
RAID_COLUMNS = ["Date", "Boss", "Pull", "Result", "Length", "Deaths", "Kept"]
PREVIEW_MS = 3000
CLIP_ROLE = Qt.UserRole + 2          # a card's model.ClipInfo
THUMB = QSize(128, 72)               # a card's picture
CARD_HEIGHT = THUMB.height() + 16
SETTINGS_FILE = CONFIG_DIR / "gui.ini"   # the window's own choices (cards or table, ...)
_UNREAD = object()   # not read yet, unlike None (no session ended yet)
PREVIEW_PAUSED = "Recording active, preview paused to save resources"


class Timeline(QWidget):
    """The clip as a scrub bar with lanes: boss pulls as spans above it (kill or wipe), deaths
    and lusts below it, and a small legend. Clicking an event jumps a few seconds before it;
    clicking anywhere else seeks there. Hovering names what's under the pointer."""
    seek = Signal(float)

    PAD = 8           # left and right margin, px
    SNAP = 6          # px: a click this close to an event jumps to it
    BOSS = (4, 18)    # lanes: (top, bottom), px
    TRACK = (24, 30)
    EVENTS = (36, 50)
    LEGEND_Y = 64     # baseline

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(70)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.length = 0.0
        self.position = 0.0
        self.bosses: list[model.Span] = []
        self.lusts: list[model.Span] = []
        self.markers: list[model.Marker] = []   # every event, for snapping and tooltips

    def sizeHint(self) -> QSize:
        return QSize(400, 70)

    def set_clip(self, length: float, details: dict):
        self.length, self.position = length, 0.0
        self.bosses = model.boss_spans(details, length)
        self.lusts = model.lust_spans(details, length)
        self.markers = model.markers(details)
        self.update()

    def set_length(self, length: float, details: dict):
        """The player found the length (a clip without one in its details)."""
        position = self.position
        self.set_clip(length, details)
        self.position = position

    def set_position(self, seconds: float):
        self.position = seconds
        self.update()

    def _x(self, seconds: float) -> float:
        return self.PAD + (self.width() - 2 * self.PAD) * (seconds / self.length if self.length else 0)

    def _seconds(self, x: float) -> float:
        width = max(1, self.width() - 2 * self.PAD)
        return max(0.0, min(self.length, (x - self.PAD) / width * self.length))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        muted = theme.muted(pal)
        gap = 1.0   # each side: 2 px of background between touching spans

        if self.length:
            for s in self.bosses:   # boss lane
                x0, x1 = self._x(s.start) + gap, max(self._x(s.end) - gap, self._x(s.start) + 3)
                rect = QRectF(x0, self.BOSS[0], x1 - x0, self.BOSS[1] - self.BOSS[0])
                if s.kind == "kill":
                    p.setPen(Qt.NoPen)
                    p.setBrush(theme.color("kill", pal))
                else:
                    fill = QColor(muted)
                    fill.setAlpha(110)
                    p.setBrush(fill)
                    pen = QPen(muted, 1, Qt.DashLine if s.kind == "pull" else Qt.SolidLine)
                    p.setPen(pen)
                p.drawRoundedRect(rect, 3, 3)

        # The track: all of it, then the played part, then the playhead.
        track = QRectF(self.PAD, self.TRACK[0], self.width() - 2 * self.PAD, self.TRACK[1] - self.TRACK[0])
        p.setPen(Qt.NoPen)
        p.setBrush(pal.mid())
        p.drawRoundedRect(track, 3, 3)
        if self.length:
            played = QRectF(track.left(), track.top(), self._x(self.position) - self.PAD, track.height())
            p.setBrush(pal.highlight())
            p.drawRoundedRect(played, 3, 3)

            mid = sum(self.EVENTS) / 2
            lust = theme.color("lust", pal)
            for s in self.lusts:    # brackets: a bar the length of the haste, a tall start
                x0, x1 = self._x(s.start), max(self._x(s.end), self._x(s.start) + 4)
                p.setPen(QPen(lust, 3, Qt.SolidLine, Qt.FlatCap))
                p.drawLine(QLineF(x0, mid, x1, mid))
                p.drawLine(QLineF(x0 + 1.5, self.EVENTS[0], x0 + 1.5, self.EVENTS[1]))
            p.setPen(QPen(theme.color("death", pal), 3, Qt.SolidLine, Qt.FlatCap))
            for m in self.markers:
                if m.kind == "death":
                    x = self._x(m.at)
                    p.drawLine(QLineF(x, self.EVENTS[0], x, self.EVENTS[1]))

            # The playhead, ringed with the background so it stands out over any mark.
            x = self._x(self.position)
            p.setPen(QPen(pal.window().color(), 2))
            p.setBrush(pal.highlight())
            p.drawEllipse(QRectF(x - 6, sum(self.TRACK) / 2 - 6, 12, 12))

        self._paint_legend(p, pal, muted)
        p.end()

    def _paint_legend(self, p: QPainter, pal, muted: QColor):
        font = p.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        p.setFont(font)
        metrics = p.fontMetrics()
        items = [("kill", "Kill"), ("wipe", "Wipe"), ("death", "Death"), ("lust", "Lust")]
        widths = [14 + 4 + metrics.horizontalAdvance(text) for _, text in items]
        x = self.width() - self.PAD - sum(widths) - 14 * (len(items) - 1)
        y = self.LEGEND_Y
        for (kind, text), w in zip(items, widths, strict=True):
            swatch = QRectF(x, y - 8, 14, 7)
            p.setPen(Qt.NoPen)
            if kind == "wipe":
                fill = QColor(muted)
                fill.setAlpha(110)
                p.setBrush(fill)
                p.setPen(QPen(muted, 1))
            else:
                p.setBrush(theme.color(kind, pal))
            if kind == "death":
                swatch = QRectF(x + 5.5, y - 10, 3, 11)
            p.drawRoundedRect(swatch, 1.5, 1.5)
            p.setPen(pal.windowText().color())   # text stays text-coloured
            p.drawText(QPointF(x + 18, y), text)
            x += w + 14

    def _near(self, x: float) -> list[model.Marker]:
        return [m for m in self.markers if abs(self._x(m.at) - x) <= self.SNAP]

    def _boss_at(self, x: float, y: float) -> model.Span | None:
        if not self.BOSS[0] - 2 <= y <= self.BOSS[1] + 2:
            return None
        return next((s for s in self.bosses if self._x(s.start) <= x <= self._x(s.end)), None)

    def mousePressEvent(self, event):
        if not self.length or event.button() != Qt.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        near = self._near(x)
        boss = self._boss_at(x, y)
        if near:
            self.seek.emit(model.seek_target(min(near, key=lambda m: abs(self._x(m.at) - x)).at))
        elif boss:
            self.seek.emit(model.seek_target(boss.start))
        else:
            self.seek.emit(self._seconds(x))

    def mouseMoveEvent(self, event):
        if not self.length:
            return
        x, y = event.position().x(), event.position().y()
        names = [f"{model.mmss(m.at)}  {m.text}" for m in self._near(x)]
        boss = self._boss_at(x, y)
        if boss and not names:
            names = [f"{model.mmss(boss.start)}–{model.mmss(boss.end)}  {boss.text}"]
        names += [f"{model.mmss(s.start)}  {s.text}" for s in self.lusts
                  if self._x(s.start) <= x <= self._x(s.end) and self.EVENTS[0] <= y <= self.EVENTS[1]
                  and not any(s.text in n for n in names)]
        self.setToolTip("\n".join(names) if names else model.mmss(self._seconds(x)))


class ClipBrowser(QWidget):
    closed = Signal()   # the window was closed; the app stays in the tray
    settings_requested = Signal()
    def __init__(self, service_state=None):
        """service_state(): the service's state, as service.state() words; the tray passes
        what it already knows from systemd's signals."""
        super().__init__()
        self.service_state = service_state or service.state
        self.setWindowTitle(f"{APP_TITLE} {__version__}: clips")
        self.resize(1400, 860)
        self.clips: list[model.ClipInfo] = []
        self.current: model.ClipInfo | None = None
        self.shown = 0   # clips in the list, after the filters and search
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
        # Keystones (a clip per run) or the full recordings they're cut from.
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for mode, text, tip in ((model.KEYS, "Keystones", "A clip for each dungeon run"),
                                (model.RAIDS, "Raids", "A clip for each raid boss pull"),
                                (model.SESSIONS, "Full sessions",
                                 "The whole recording of each time you played")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setProperty("mode", mode)
            self.mode_group.addButton(b)
            self.mode_buttons[mode] = b
        self.mode_group.buttonClicked.connect(lambda b: self.set_mode(b.property("mode")))
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
        self.view_group = QButtonGroup(self)
        for i, (text, tip) in enumerate((("Cards", "Clips as cards with a picture"),
                                         ("Table", "Clips as a compact table"))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            self.view_group.addButton(b, i)
            filters.addWidget(b)
        self.view_group.idClicked.connect(self.set_view)
        settings = QPushButton("Settings...")
        settings.clicked.connect(self.settings_requested)
        top = QHBoxLayout()
        top.setSpacing(0)
        for b in self.mode_buttons.values():
            top.addWidget(b)
        top.addSpacing(10)
        top.addWidget(self.search, 1)
        top.addSpacing(6)
        top.addWidget(settings)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.DescendingOrder)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.currentItemChanged.connect(lambda item, _: self.select(item.data(0, Qt.UserRole) if item else None))
        self.tree.itemActivated.connect(lambda *_: self.player.play())
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Shift and Ctrl

        self.thumbs: dict[str, QPixmap] = {}
        self.thumb_queue: list[model.ClipInfo] = []
        self.thumb_tried: set[str] = set()
        self.thumb_process: QProcess | None = None
        self.cards = QListWidget()
        self.cards.setItemDelegate(ClipCard(self.thumbnail, self.cards))
        self.cards.setUniformItemSizes(True)
        self.cards.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards.setResizeMode(QListWidget.Adjust)
        self.cards.currentItemChanged.connect(lambda item, _: self.select(item.data(Qt.UserRole) if item else None))
        self.cards.itemActivated.connect(lambda *_: self.player.play())
        self.cards.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for view in (self.cards, self.tree):
            view.itemSelectionChanged.connect(self.show_count)
            QShortcut(QKeySequence.Delete, view, self.delete, context=Qt.WidgetShortcut)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.cards)
        self.list_stack.addWidget(self.tree)
        self.count = QLabel()

        # While WoW is being recorded: its newest frame, to check it records the right thing.
        self.live_box = QGroupBox("Recording now")
        self.live_image = QLabel()
        self.live_image.setAlignment(Qt.AlignCenter)
        self.live_image.setMinimumHeight(120)
        self.live_image.setWordWrap(True)   # for live_message
        self.live_caption = QLabel()
        # While WoW runs with a window picked (or awaited) in the screen-share dialog.
        self.live_switch = QPushButton(model.switch_text(False))
        self.live_switch.clicked.connect(self.toggle_monitor)
        self.live_switch.hide()
        self.live_repick = QPushButton(model.REPICK_TEXT)
        self.live_repick.clicked.connect(self.repick)
        self.live_repick.hide()
        switch_row = QHBoxLayout()
        switch_row.addWidget(self.live_repick)
        switch_row.addWidget(self.live_switch)
        switch_row.addStretch(1)
        live_layout = QVBoxLayout(self.live_box)
        live_layout.addWidget(self.live_image)
        live_layout.addWidget(self.live_caption)
        live_layout.addLayout(switch_row)
        self.live_box.hide()
        self.live_pixmap = QPixmap()
        self.live_message = None   # why WoW isn't being recorded, shown instead of a frame
        self.live_process = None
        self.live_shown_paused = False   # the preview on screen has the paused overlay
        self.last_session_end = _UNREAD   # status.json's; a new one means new clips
        self.live_timer = QTimer(self, interval=PREVIEW_MS)
        self.live_timer.timeout.connect(self.update_live)

        # What still stops the app from working, ticked off; hidden once everything is fine.
        self.checklist = QFrame()
        self.checklist.setObjectName("checklist")
        self.checklist_rows = QVBoxLayout(self.checklist)
        self.checklist_rows.setContentsMargins(12, 10, 12, 10)
        self.checklist_rows.setSpacing(4)
        self.checklist.hide()
        self.checklist_timer = QTimer(self, interval=60_000)
        self.checklist_timer.timeout.connect(self.update_checklist)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.checklist)
        lv.addWidget(self.live_box)
        lv.addLayout(top)
        lv.addLayout(filters)
        lv.addWidget(self.list_stack, 1)
        lv.addWidget(self.count)

        # ── Right: player, timeline, details, actions ──
        self.video = QVideoWidget()
        self.video.setMinimumSize(480, 270)
        self.video.setStyleSheet("background: black")   # no grey between the letterbox bars
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
        self.video_stack = QStackedWidget()
        self.video_stack.addWidget(self.video)
        self.video_stack.addWidget(self.no_video)
        # A clip loaded but not played yet: black with the app's icon, not an empty frame.
        self.poster = Poster()
        self.video_stack.addWidget(self.poster)

        self.play_button = QPushButton(" Play")
        self.play_button.setObjectName("primary")
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setMinimumWidth(96)
        self.play_button.setToolTip("Play or pause (Space)")
        self.play_button.clicked.connect(self.toggle_play)
        self.timeline = Timeline()
        self.timeline.seek.connect(self.seek)
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
        self.result_chip = QLabel()
        self.facts = QLabel()
        self.facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.facts.setWordWrap(True)

        # Events: one row per boss pull, holding what happened during it.
        self.events = QTreeWidget()
        self.events.setHeaderHidden(True)
        self.events.setColumnCount(3)
        self.events.setRootIsDecorated(True)
        self.events.setIndentation(18)
        self.events.setUniformRowHeights(True)
        self.events.setMinimumHeight(150)
        self.events.itemActivated.connect(self.seek_to_event)
        self.events.itemClicked.connect(self.seek_to_event)

        # Actions: Play is the main one (with the player); the rest are an icon row and a menu.
        self.keep_button = self._tool_button("Keep this clip forever, whatever \"Keep clips\" says",
                                             self.toggle_keep)
        self.keep_button.setCheckable(True)
        self.keep_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        open_button = self._tool_button("Open in your video player", self.open_external,
                                        "document-open", QStyle.SP_DialogOpenButton)
        folder_button = self._tool_button("Show in folder", self.show_in_folder,
                                          "folder-open", QStyle.SP_DirOpenIcon)
        self.youtube_button = self._tool_button(
            "Upload to YouTube: copies a description with chapters, opens YouTube's upload page "
            "and shows the clip to drag onto it", self.upload_to_youtube,
            "document-send", QStyle.SP_ArrowUp)
        # Only there when an after_clips command is set (Settings > Advanced).
        self.hook_button = self._tool_button("Run your after_clips command on this clip again",
                                             self.run_hook, "system-run", QStyle.SP_BrowserReload)
        self.trash_button = self._tool_button("Move to trash", self.delete)
        self.trash_button.setIconSize(QSize(20, 20))
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.result_chip, 0, Qt.AlignVCenter)
        header.addSpacing(12)
        for b in (self.keep_button, open_button, folder_button, self.youtube_button,
                  self.hook_button):
            header.addWidget(b, 0, Qt.AlignVCenter)
        header.addSpacing(10)
        header.addWidget(self.trash_button, 0, Qt.AlignVCenter)
        self.action_widgets = [self.keep_button, open_button, folder_button, self.youtube_button,
                               self.hook_button, self.trash_button]

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.video_stack, 3)
        rv.addLayout(controls)
        rv.addLayout(header)
        rv.addWidget(self.facts)
        rv.addWidget(self.events, 2)

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
        self._theming = False
        self.apply_theme()
        self.settings = QSettings(str(SETTINGS_FILE), QSettings.IniFormat)
        view = 1 if self.settings.value("view") == "table" else 0
        self.view_group.button(view).setChecked(True)
        self.list_stack.setCurrentIndex(view)
        mode = self.settings.value("mode")
        self.mode = mode if mode in (model.RAIDS, model.SESSIONS) else model.KEYS
        self.mode_buttons[self.mode].setChecked(True)
        self.show_mode()
        self.reload()

    def _tool_button(self, tip: str, slot, icon_name: str = "", fallback=None) -> QToolButton:
        b = QToolButton()
        b.setAutoRaise(True)
        b.setToolTip(tip)
        if icon_name:
            b.setIcon(QIcon.fromTheme(icon_name, self.style().standardIcon(fallback)))
            b.setIconSize(QSize(20, 20))
        if slot:
            b.clicked.connect(slot)
        return b

    def apply_theme(self):
        """Colours that come from theme.py, set again when the desktop's theme changes."""
        self.setStyleSheet(
            "QPushButton#primary { background: palette(highlight); color: palette(highlighted-text);"
            " border: none; border-radius: 4px; padding: 6px 14px; font-weight: 600; }"
            "QPushButton#primary:hover { background: palette(highlight); }")
        self.facts.setStyleSheet(f"color: {theme.muted().name()};")
        self.no_video.setStyleSheet(f"background: black; color: {theme.muted().name()}; font-size: 15px;")
        self.live_caption.setStyleSheet(f"color: {theme.muted().name()};")
        self.trash_button.setIcon(trash_icon(self.palette()))
        self.checklist.setStyleSheet(
            f"QFrame#checklist {{ border: 1px solid {theme.color('warning').name()};"
            " border-radius: 6px; }")
        if self.current:
            self.show_details(self.current)
        if hasattr(self, "settings"):
            self.update_checklist()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange) \
                and self.live_box.isVisible():
            self.update_live()   # pauses the preview, or brings it up to date at once
        if event.type() == QEvent.PaletteChange and not self._theming:
            self._theming = True   # setStyleSheet sends palette changes of its own
            self.apply_theme()
            self._theming = False

    # ── Live preview ──

    def update_live(self):
        status = state.read() or {}
        end = (status.get("last_session") or {}).get("end")
        if end != self.last_session_end:
            if self.last_session_end is not _UNREAD:
                self.reload_timer.start()   # a session was just cut
            self.last_session_end = end
        path = status.get("recording_file") if status.get("state") == state.RECORDING else None
        view = model.describe(status, "active")
        self.live_switch.setVisible(view.can_switch)
        self.live_switch.setText(model.switch_text(view.monitor_switch))
        self.live_repick.setVisible(view.can_repick)
        message = (status.get("problem") or "Not recording.") if (
            status.get("state") == state.NOT_RECORDING and status.get("session_start")) else None
        if not path:
            self.live_pixmap = QPixmap()
            self.live_message = message
            self.live_box.setVisible(bool(message))
            if message:
                self.live_box.setTitle("Not recording")
                self.live_caption.setText("")
                self.show_live_pixmap()
            return
        self.live_message = None
        self.live_box.setTitle("Recording now")
        self.live_box.show()
        if self.live_pixmap.isNull():
            self.show_live_pixmap()   # room for the first frame, decoded even in the background
        elif self.preview_paused():
            self.show_live_pixmap()
            return   # decoding a frame for nobody to see: wait until the window is looked at
        elif self.live_shown_paused:
            self.show_live_pixmap()   # the last frame without the overlay, until a new one
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
            self.live_caption.setText(f"Updated {time.strftime('%H:%M:%S')}, a few seconds behind live")
            self.show_live_pixmap()   # after the caption: paused, it says so instead
        else:
            self.live_caption.setText("Couldn't read the recording yet")
        proc.deleteLater()

    def toggle_monitor(self):
        """Record monitor instead of the picked window, or the window again, this session."""
        why = state.request_monitor(not (state.read() or {}).get("monitor_switch"))
        if why:
            QMessageBox.warning(self, APP_TITLE, why)
        else:
            self.live_switch.setEnabled(False)   # until the service has switched
            QTimer.singleShot(PREVIEW_MS, lambda: self.live_switch.setEnabled(True))

    def repick(self):
        """Opens the screen-share dialog again, to record another window."""
        if why := state.request_repick():
            QMessageBox.warning(self, APP_TITLE, why)
        else:
            self.live_repick.setEnabled(False)   # until the service has restarted it
            QTimer.singleShot(PREVIEW_MS, lambda: self.live_repick.setEnabled(True))

    def preview_paused(self) -> bool:
        """The live preview waits while the window is minimized or another window has focus."""
        return self.isMinimized() or not self.isActiveWindow()

    def show_live_pixmap(self):
        # the column's width: the box's own is stale while it's hidden, before its first show
        w = max(160, self.live_box.parentWidget().contentsRect().width() - 40)
        paused = self.live_shown_paused = self.preview_paused()
        if self.live_message:
            self.live_image.setMinimumHeight(w * 9 // 16)
            self.live_image.setText(f"<b style='font-size:15pt'>{html.escape(self.live_message, quote=False)}</b>")
            return
        if self.live_pixmap.isNull():
            # a frame's room held from the start, so the first frame doesn't push the page down
            self.live_image.setMinimumHeight(w * 9 // 16)
            self.live_image.setText(PREVIEW_PAUSED if paused else "")
            return
        pixmap = self.live_pixmap.scaledToWidth(w, Qt.SmoothTransformation)
        self.live_image.setMinimumHeight(pixmap.height())
        if paused:
            pixmap = paused_pixmap(pixmap)
        self.live_image.setPixmap(pixmap)
        if paused:
            self.live_caption.setText("Click this window to see the newest frame.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.show_live_pixmap()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_live()
        self.live_timer.start()
        self.update_checklist()
        self.checklist_timer.start()

    # ── Setup checklist ──

    def update_checklist(self):
        steps = model.setup_checklist(self.cfg, self.service_state())
        self.checklist.setVisible(not all(s.ok for s in steps))
        if all(s.ok for s in steps):
            return
        while (item := self.checklist_rows.takeAt(0)) is not None:
            if item.widget():
                item.widget().deleteLater()
        heading = QLabel("Not ready to record yet")
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        self.checklist_rows.addWidget(heading)
        good, bad, muted = theme.color("good"), theme.color("error"), theme.muted()
        for step in steps:
            row = QLabel(f"<span style='color:{(good if step.ok else bad).name()}'>"
                         f"{'✓' if step.ok else '✕'}</span>&nbsp; "
                         + (step.title if step.ok else f"<b>{step.title}</b>"))
            self.checklist_rows.addWidget(row)
            if not step.ok and step.hint:
                hint = QLabel(step.hint)
                hint.setWordWrap(True)
                hint.setStyleSheet(f"color: {muted.name()}; margin-left: 18px;")
                self.checklist_rows.addWidget(hint)
        button = QPushButton("Open settings...")
        button.clicked.connect(self.settings_requested)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 4, 0, 0)
        self.checklist_rows.addWidget(holder)

    # ── The list ──

    def reload(self):
        selected = self.current.path if self.current else None
        try:
            self.cfg = config.load()   # Settings may have moved the output folder
        except ConfigError:
            pass
        # Before the first clip there's no clips folder yet: its parent is watched until it
        # shows up. processed/ gets each full recording once it's cut.
        folders = sorted({str(f) for f in (model.folder_to_watch(self.cfg.clips_dir),
                                           model.folder_to_watch(self.cfg.processed_dir),
                                           *(d for d in self.cfg.other_clip_dirs if d.is_dir()))
                          if f} if self.cfg else set())
        watched = sorted(self.watcher.directories())
        if watched != folders:
            if watched:
                self.watcher.removePaths(watched)
            if folders:
                self.watcher.addPaths(folders)
        if not self.cfg:
            self.clips = []
        elif self.mode == model.SESSIONS:
            status = state.read() or {}
            self.clips = model.load_sessions(self.cfg.processed_dir, self.cfg.sessions_dir,
                                             self.cfg.clips_dir, read_sidecar,
                                             status.get("recording_file"))
        else:
            self.clips = model.load_clips(self.cfg.clips_dir, read_sidecar, self.cfg.other_clip_dirs)
        self.fill_list(select=selected)
        if self.isVisible():
            self.update_checklist()

    def fill_list(self, *_args, select=None):
        select = str(select or (self.current.path if self.current else "")) or None
        key = "all" if self.mode == model.SESSIONS else self.filter_group.checkedButton().property("key")
        shown = [c for c in self.clips if model.in_mode(c, self.mode)
                 and model.matches(c, key, self.search.text().strip())]

        self.cards.blockSignals(True)
        self.cards.clear()
        for c in shown:   # newest first, as load_clips sorts them
            item = QListWidgetItem()
            item.setData(Qt.UserRole, str(c.path))
            item.setData(CLIP_ROLE, c)
            item.setSizeHint(QSize(0, CARD_HEIGHT))
            item.setToolTip(c.path.name)
            self.cards.addItem(item)
        self.cards.blockSignals(False)

        self.tree.blockSignals(True)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for c in shown:
            item = SortItem([
                time.strftime("%Y-%m-%d %H:%M", time.localtime(c.started)),
                c.dungeon,
                f"#{c.pull}" if c.pull else f"+{c.level}" if c.level else "",
                c.result_text,
                c.length,
                "" if c.deaths is None else str(c.deaths),
                "★" if c.keep else "",
            ])
            item.setData(0, Qt.UserRole, str(c.path))
            item.setData(0, Qt.UserRole + 1, c.started)
            item.setData(2, Qt.UserRole + 1, c.pull or c.level or 0)
            item.setData(5, Qt.UserRole + 1, c.deaths or 0)
            self.tree.addTopLevelItem(item)
        self.tree.setSortingEnabled(True)
        for col in (0, 2, 3, 4, 5, 6):
            self.tree.resizeColumnToContents(col)
        self.tree.blockSignals(False)

        paths = [str(c.path) for c in shown]
        self.shown = len(shown)
        self.select(select if select in paths else (paths[0] if paths else None))
        self.show_count()

    def show_count(self):
        total = sum(model.in_mode(c, self.mode) for c in self.clips)
        text = f"{self.shown} of {total} " + ("full sessions" if self.mode == model.SESSIONS else "clips")
        if (n := len(self.selected_clips())) > 1:
            text += f", {n} selected"
        self.count.setText(text)

    def selected_clips(self) -> list[model.ClipInfo]:
        """The clips picked with Shift or Ctrl in the list on show, in its order."""
        view = self.list_stack.currentWidget()
        paths = [i.data(Qt.UserRole) if view is self.cards else i.data(0, Qt.UserRole)
                 for i in view.selectedItems()]
        by_path = {str(c.path): c for c in self.clips}
        return [by_path[p] for p in paths if p in by_path]

    def set_mode(self, mode: str):
        """Keystones (dungeon clips), Raids (boss pull clips) or Full sessions (the recordings)."""
        if mode == self.mode:
            return
        self.mode = mode
        self.settings.setValue("mode", mode)
        self.current = None   # a clip isn't in the sessions list, nor the other way round
        self.show_mode()
        self.reload()

    def show_mode(self):
        sessions, raids = self.mode == model.SESSIONS, self.mode == model.RAIDS
        filters = model.RAID_FILTERS if raids else model.FILTERS
        buttons = self.filter_group.buttons()
        relabel = [b.property("key") for b in buttons] != list(filters)
        for b, (key, label) in zip(buttons, filters.items(), strict=True):
            b.setVisible(not sessions)   # results are per run
            b.setProperty("key", key)
            b.setText(label)
        if relabel:   # Kills isn't Completed: back to All
            buttons[0].setChecked(True)
        self.search.setPlaceholderText("Search bosses and raids" if raids else "Search dungeons")
        self.tree.setHeaderLabels(SESSION_COLUMNS if sessions else RAID_COLUMNS if raids
                                  else COLUMNS)

    def set_view(self, view: int):
        self.list_stack.setCurrentIndex(view)
        self.settings.setValue("view", "table" if view else "cards")
        self.select(self.current.path if self.current else None)   # just the one, as in the other
        self.show_count()

    def select(self, path: str | None):
        """Shows the clip at path (None: no clip) and marks it in both the cards and the table."""
        for i in range(self.cards.count()):
            if self.cards.item(i).data(Qt.UserRole) == path:
                if self.cards.currentRow() != i:   # already: a click, maybe with Shift or Ctrl
                    self.cards.blockSignals(True)
                    self.cards.setCurrentRow(i)
                    self.cards.blockSignals(False)
                break
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).data(0, Qt.UserRole) == path:
                if self.tree.currentItem() is not self.tree.topLevelItem(i):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(self.tree.topLevelItem(i))
                    self.tree.blockSignals(False)
                break
        clip = next((c for c in self.clips if str(c.path) == path), None)
        same_file = clip and self.current and clip.path == self.current.path
        self.current = clip
        for w in self.action_widgets:
            w.setEnabled(clip is not None)
        if not clip:
            self.player.setSource(QUrl())
            sessions = self.mode == model.SESSIONS
            raids = self.mode == model.RAIDS
            if any(model.in_mode(c, self.mode) for c in self.clips):
                empty = "Pick a full session from the list." if sessions else "Pick a clip from the list."
            elif raids:
                empty = "Raid boss clips show up here after your first boss pull."
            elif sessions:
                empty = ("Full recordings show up here once a session has been cut into clips. "
                         "They're kept for as long as Settings > Clips and storage > "
                         "Keep full recordings says.")
            else:
                empty = "Clips show up here after your first dungeon run."
            self.no_video.setText(empty)
            self.video_stack.setCurrentWidget(self.no_video)
            what = "full sessions" if sessions else "raid clips" if raids else "clips"
            listed = any(model.in_mode(c, self.mode) for c in self.clips)
            self.title.setText(f"No {what} yet" if not listed else f"No {what} match")
            self.result_chip.setText("")
            self.result_chip.setStyleSheet("")
            self.facts.setText("")
            self.events.clear()
            self.timeline.set_clip(0, {})
            return
        if not same_file:
            self.video_stack.setCurrentWidget(self.poster)
            self.player.setSource(QUrl.fromLocalFile(str(clip.path)))
        self.show_details(clip)

    # ── Thumbnails ──

    def thumbnail(self, clip: model.ClipInfo) -> QPixmap | None:
        """The card's picture, or None while there isn't one; a missing one is made in the
        background (clips cut before pictures existed)."""
        key = str(clip.path)
        if key in self.thumbs:
            return self.thumbs[key]
        name = clip.details.get("thumbnail")
        beside = clip.details_file or clip.path   # a moved clip's picture stayed with its details
        path = beside.with_name(name) if name else thumbnail_path(clip.path)
        pixmap = QPixmap(str(path)) if path.exists() else QPixmap()
        if pixmap.isNull():
            # Not for a recording still in sessions/ (its picture would stay behind when it
            # moves), nor in another folder: the app writes only in its own.
            if key not in self.thumb_tried and clip.result != "uncut" and not clip.elsewhere:
                self.thumb_tried.add(key)
                self.thumb_queue.append(clip)
                self.next_thumbnail()
            return None
        self.thumbs[key] = pixmap
        return pixmap

    def next_thumbnail(self):
        if self.thumb_process is not None or not self.thumb_queue:
            return
        clip = self.thumb_queue.pop(0)
        cmd = thumbnail_command(clip.path, thumbnail_time(clip.details))
        process = self.thumb_process = QProcess(self)
        process.finished.connect(lambda *_: self.thumbnail_made(process, clip))
        process.errorOccurred.connect(lambda _e: self.thumbnail_made(process, clip))
        process.start(cmd[0], cmd[1:])

    def thumbnail_made(self, process: QProcess, clip: model.ClipInfo):
        if self.thumb_process is not process:
            return   # errorOccurred and finished both came
        self.thumb_process = None
        process.deleteLater()
        out = thumbnail_path(clip.path)
        part = out.with_name(out.name + ".part")
        try:
            if process.exitCode() == 0 and part.exists():
                part.replace(out)
                self.thumbs.pop(str(clip.path), None)
                self.cards.viewport().update()
            else:
                part.unlink(missing_ok=True)
        except OSError:
            pass
        self.next_thumbnail()

    def show_details(self, clip: model.ClipInfo):
        d = clip.details
        level = f" +{clip.level}" if clip.level else ""
        self.title.setText(f"{clip.dungeon}, pull {clip.pull}" if clip.pull else f"{clip.dungeon}{level}")
        self.title.setToolTip(str(clip.path))
        self.result_chip.setText(clip.result_text)
        self.result_chip.setStyleSheet(chip_style(clip.result, self.palette()))
        facts = [time.strftime("%a %Y-%m-%d %H:%M", time.localtime(clip.started))]
        if clip.is_raid:
            facts.append(clip.raid)
            facts.append(f"pull {clip.length}")
        elif not clip.is_session:
            facts.append(f"run {clip.length}")
        elif clip.length:
            facts.append(f"recorded {clip.length}")
        if clip.bosses_killed is not None and not clip.is_raid:
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
        if clip.elsewhere:
            facts.append(f"in {clip.path.parent}")
        self.facts.setText(" · ".join(facts))
        self.events.clear()
        rows = model.session_event_rows(d) if clip.is_session else model.event_rows(d)
        for row in rows:
            item = self._event_tree(row)
            self.events.addTopLevelItem(item)
            self._expand(item, row)
            if clip.is_session:
                item.setExpanded(True)   # a session's runs are open
        if not rows:
            self.events.addTopLevelItem(QTreeWidgetItem(["", (
                "Not cut into clips yet: its runs show here once it is." if clip.result == "uncut"
                else "No dungeon runs in this session." if clip.is_session
                else "No boss, death or lust details for this clip (cut by an older version, "
                     "or advanced combat logging off)."), ""]))
        for col in (0, 2):
            self.events.resizeColumnToContents(col)
        self.events.header().setStretchLastSection(False)
        self.events.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.timeline.set_clip(d.get("clip_length") or self.player.duration() / 1000, d)
        self.keep_button.setChecked(clip.keep)
        self.keep_button.setText("★ Kept" if clip.keep else "☆ Keep")
        # A recording in sessions/ is never deleted anyway, and its details file would stay
        # behind when it's cut and moved.
        # Nor in another folder: retention never deletes anything there.
        self.keep_button.setVisible(clip.result != "uncut" and not clip.elsewhere)
        self.keep_button.setToolTip(
            "Keep this full recording forever, whatever \"Keep full recordings\" says"
            if clip.is_session else "Keep this clip forever, whatever \"Keep clips\" says")
        self.youtube_button.setVisible(not clip.is_session)
        self.hook_button.setVisible(bool(self.cfg and self.cfg.after_clips) and not clip.is_session
                                    and not clip.elsewhere)

    def _event_tree(self, row: model.EventRow) -> QTreeWidgetItem:
        """A row and everything under it."""
        item = self._event_item(row)
        for child in row.children:
            item.addChild(self._event_tree(child))
        return item

    def _expand(self, item: QTreeWidgetItem, row: model.EventRow):
        """Opens the pulls worth a look: wipes and those with deaths. Only once the item is in
        the tree: before, setExpanded doesn't stick."""
        item.setExpanded(row.kind == "wipe" or any(c.kind == "death" for c in row.children))
        for i, child in enumerate(row.children):
            self._expand(item.child(i), child)

    def _event_item(self, row: model.EventRow) -> QTreeWidgetItem:
        item = QTreeWidgetItem([model.mmss(row.at), row.text, row.detail])
        item.setData(0, Qt.UserRole, row.at)
        item.setIcon(1, event_icon(row.kind, self.palette()))
        item.setForeground(0, theme.muted(self.palette()))
        item.setForeground(2, theme.muted(self.palette()))
        if row.children:
            font = item.font(1)
            font.setBold(True)
            item.setFont(1, font)
        return item

    # ── Player ──

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_state(self, s):
        playing = s == QMediaPlayer.PlayingState
        if playing and self.current:
            self.video_stack.setCurrentWidget(self.video)
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing
                                                           else QStyle.SP_MediaPlay))
        self.play_button.setText(" Pause" if playing else " Play")

    def on_position(self, ms: int):
        self.timeline.set_position(ms / 1000)
        self.time_label.setText(f"{model.mmss(ms / 1000)} / {model.mmss(self.player.duration() / 1000)}")

    def on_duration(self, ms: int):
        if ms and self.current and not self.current.details.get("clip_length"):
            self.timeline.set_length(ms / 1000, self.current.details)
        self.on_position(self.player.position())

    def seek_to_event(self, item: QTreeWidgetItem):
        at = item.data(0, Qt.UserRole)
        if at is not None:
            self.seek(model.seek_target(at))   # a few seconds of lead-up
            self.player.play()

    def seek(self, seconds: float):
        self.player.setPosition(int(seconds * 1000))
        if self.current:
            self.video_stack.setCurrentWidget(self.video)   # the frame sought to
        self.timeline.set_position(seconds)

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

    def upload_to_youtube(self):
        """Copies the clip's description, opens YouTube's upload page in the browser and shows
        the clip in the file manager, to drag onto it. The browser does the upload."""
        self.player.pause()
        QGuiApplication.clipboard().setText(model.youtube_description(self.current))
        QDesktopServices.openUrl(QUrl(model.YOUTUBE_UPLOAD_URL))
        self.show_in_folder()
        QMessageBox.information(self, APP_TITLE, "Description copied. Drag the clip onto the "
                                "YouTube page, then paste the description.")

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
        self.hook_button.setToolTip("after_clips is running...")
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
        self.hook_button.setToolTip("Run your after_clips command on this clip again")
        self.hook_button.setEnabled(True)
        if ok:
            QMessageBox.information(self, APP_TITLE, "The after_clips command finished."
                                    + (f"\n\n{out[-1500:]}" if out else ""))
        else:
            QMessageBox.warning(self, APP_TITLE, "The after_clips command failed."
                                + (f"\n\n{out[-1500:]}" if out else f"\n\n{p.errorString()}"))

    def delete(self):
        """Moves the selected clips (or the one shown) and their details to the desktop's trash,
        so a slip can be undone. Deletes them for good only if there's no trash and the user
        says so."""
        clips = self.selected_clips() or ([self.current] if self.current else [])
        if not clips:
            return
        yes_no = QMessageBox.Yes | QMessageBox.No
        kind = "full recording" if self.mode == model.SESSIONS else "clip"
        # Removing a file needs write access to its folder; a read-only share or drive has none.
        writable = [c for c in clips if os.access(c.path.parent, os.W_OK)]
        if not writable:
            what = f"This {kind} is" if len(clips) == 1 else f"These {kind}s are"
            QMessageBox.information(self, APP_TITLE, f"{what} read only and can't be deleted.")
            return
        what = f"this {kind}" if len(writable) == 1 else f"{len(writable)} {kind}s"
        names = "\n".join(c.path.name for c in writable[:10])
        if len(writable) > 10:
            names += f"\n...and {len(writable) - 10} more"
        if len(clips) > len(writable):
            names += f"\n\n{len(clips) - len(writable)} read only, left alone."
        if QMessageBox.question(self, APP_TITLE, f"Move {what} to the trash?\n\n{names}",
                                yes_no, QMessageBox.No) != QMessageBox.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        left = []
        for clip in writable:
            candidates = [clip.path, sidecar_path(clip.path), thumbnail_path(clip.path)]
            if clip.details_file:   # a moved clip's details and picture, left in clips/
                candidates += [clip.details_file, thumbnail_path(clip.details_file)]
            files = [f for f in dict.fromkeys(candidates) if f.exists()]
            left += [f for f in files if not QFile.moveToTrash(str(f))]
        if left and QMessageBox.question(
                self, APP_TITLE, f"There's no trash for this folder. Delete {what} for good?",
                yes_no, QMessageBox.No) == QMessageBox.Yes:
            try:
                for f in left:
                    f.unlink(missing_ok=True)
            except OSError as e:
                QMessageBox.warning(self, APP_TITLE, f"Couldn't delete: {e}")
        self.current = None
        self.reload()

    def closeEvent(self, event):
        self.player.pause()
        self.live_timer.stop()
        super().closeEvent(event)
        self.closed.emit()


class ClipCard(QStyledItemDelegate):
    """A clip as a card: its picture, the dungeon and level, the result, and what happened."""

    def __init__(self, thumbnail, parent):
        super().__init__(parent)
        self.thumbnail = thumbnail   # ClipBrowser.thumbnail

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, CARD_HEIGHT)   # as wide as the list

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index):
        clip: model.ClipInfo = index.data(CLIP_ROLE)
        widget = option.widget
        style = widget.style() if widget else None
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        if style:   # the selection and hover background, as the theme draws it
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, opt, p, widget)
        if clip is None:
            return
        pal = option.palette
        selected = bool(option.state & QStyle.State_Selected)
        text = pal.highlightedText().color() if selected else pal.text().color()
        muted = text if selected else theme.muted(pal)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(8, 8, -8, -8)

        # The picture, cropped to fill; a plain panel while there's none.
        pic = QRectF(r.left(), r.top(), THUMB.width(), THUMB.height())
        pixmap = self.thumbnail(clip)
        path = QPainterPath()
        path.addRoundedRect(pic, 4, 4)
        p.setClipPath(path)
        if pixmap:
            scaled = pixmap.scaled(THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(pic.topLeft(), scaled,
                         QRectF((scaled.width() - THUMB.width()) / 2,
                                (scaled.height() - THUMB.height()) / 2, THUMB.width(), THUMB.height()))
        else:
            p.fillRect(pic, pal.mid())
        p.setClipping(False)
        if clip.level or clip.pull:   # "+12", or a pull's "#3", on the picture's corner
            font = QFont(option.font)
            font.setBold(True)
            p.setFont(font)
            label = f"#{clip.pull}" if clip.pull else f"+{clip.level}"
            w = p.fontMetrics().horizontalAdvance(label) + 10
            badge = QRectF(pic.left() + 4, pic.top() + 4, w, p.fontMetrics().height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(badge, 3, 3)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignCenter, label)

        x = pic.right() + 12
        width = r.right() - x
        # Line 1: the dungeon, and the star when kept.
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.08)
        p.setFont(font)
        fm = p.fontMetrics()
        star = "★" if clip.keep else ""
        star_w = fm.horizontalAdvance(star) + 6 if star else 0
        p.setPen(text)
        p.drawText(QRectF(x, r.top(), width - star_w, fm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(clip.dungeon, Qt.ElideRight, int(width - star_w)))
        if star:
            p.drawText(QRectF(r.right() - star_w, r.top(), star_w, fm.height()), Qt.AlignRight, star)

        # Line 2: the result chip and the run's length.
        p.setFont(option.font)
        fm = p.fontMetrics()
        y = r.top() + fm.height() + 8
        chip_w = fm.horizontalAdvance(clip.result_text) + 16
        chip = QRectF(x, y, chip_w, fm.height() + 2)
        edge = result_color(clip.result, pal)
        p.setPen(QPen(text if selected else edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), chip.height() / 2, chip.height() / 2)
        p.setPen(text)
        p.drawText(chip, Qt.AlignCenter, clip.result_text)
        p.drawText(QRectF(chip.right() + 10, y, width, chip.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   clip.length)

        # Line 3: when, deaths and lusts, with the timeline's marks.
        y = chip.bottom() + 6
        p.setPen(muted)
        when = time.strftime("%a %d %b %H:%M", time.localtime(clip.started))
        p.drawText(QRectF(x, y, width, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, when)
        cx = x + fm.horizontalAdvance(when) + 14
        lusts = len(clip.details.get("lusts") or [])
        # Counts only where they're known: not for clips without details, or recordings not cut yet.
        for kind, n in (("death", clip.deaths), ("lust", lusts if clip.deaths is not None else None)):
            if n is None:
                continue
            icon = event_icon(kind, pal)
            icon.paint(p, int(cx), int(y + (fm.height() - 14) / 2), 14, 14)
            p.setPen(muted)
            p.drawText(QRectF(cx + 17, y, 40, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, str(n))
            cx += 17 + fm.horizontalAdvance(str(n)) + 12
        p.restore()


def trash_icon(palette) -> QIcon:
    """A trash can in the theme's error red: deleting stands out from the other actions."""
    icon = QIcon()
    red = theme.color("error", palette)
    for size in (16, 20, 24, 32, 40):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        u = size / 16
        pen = QPen(red, max(1.2, 1.3 * u), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(QLineF(2.5 * u, 4 * u, 13.5 * u, 4 * u))              # the lid
        p.drawLine(QLineF(6.5 * u, 2 * u, 9.5 * u, 2 * u))               # its handle
        body = QPainterPath()
        body.moveTo(4 * u, 4 * u)
        body.lineTo(4.8 * u, 14 * u)
        body.lineTo(11.2 * u, 14 * u)
        body.lineTo(12 * u, 4 * u)
        p.drawPath(body)
        for x in (6.6, 8, 9.4):                                           # the ribs
            p.drawLine(QLineF(x * u, 6.5 * u, x * u, 11.5 * u))
        p.end()
        icon.addPixmap(pix)
    return icon


class Poster(QWidget):
    """Black, with the app's icon in the middle: the player before a clip is played."""

    def __init__(self):
        super().__init__()
        self.icon = QIcon(str(Path(__file__).with_name("icon.svg")))

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        side = min(self.width(), self.height()) // 4
        if side > 0 and not self.icon.isNull():
            self.icon.paint(p, (self.width() - side) // 2, (self.height() - side) // 2, side, side)
        p.end()


def paused_pixmap(pixmap: QPixmap) -> QPixmap:
    """The last preview frame, dimmed, with PREVIEW_PAUSED over it."""
    out = QPixmap(pixmap)
    p = QPainter(out)
    p.fillRect(out.rect(), QColor(0, 0, 0, 150))
    font = p.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.1)
    p.setFont(font)
    p.setPen(QColor("white"))   # on the dimmed frame, whatever the theme
    p.drawText(out.rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap,
               PREVIEW_PAUSED)
    p.end()
    return out


def event_icon(kind: str, palette) -> QIcon:
    """A small mark beside an event, in the timeline's colour for its kind."""
    pix = QPixmap(14, 14)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    muted = theme.muted(palette)
    if kind == "kill":
        p.setBrush(theme.color("kill", palette))
        p.drawRoundedRect(QRectF(1, 4, 12, 6), 2, 2)
    elif kind in ("wipe", "pull"):
        fill = QColor(muted)
        fill.setAlpha(110)
        p.setBrush(fill)
        p.setPen(QPen(muted, 1, Qt.DashLine if kind == "pull" else Qt.SolidLine))
        p.drawRoundedRect(QRectF(1.5, 4.5, 11, 5), 2, 2)
    elif kind == "death":
        p.setBrush(theme.color("death", palette))
        p.drawRoundedRect(QRectF(5.5, 1, 3, 12), 1, 1)
    elif kind == "lust":
        p.setPen(QPen(theme.color("lust", palette), 3, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QLineF(3, 2, 3, 12))
        p.drawLine(QLineF(3, 7, 13, 7))
    else:   # the run's start
        p.setBrush(muted)
        p.drawEllipse(QRectF(4, 4, 6, 6))
    p.end()
    return QIcon(pix)


def result_color(result: str, palette) -> QColor:
    """Completed keys and raid kills good, abandoned keys and wipes a warning, the rest muted."""
    return (theme.color("good", palette) if result in ("completed", "kill") else
            theme.color("warning", palette) if result in ("abandoned", "wipe") else theme.muted(palette))


def chip_style(result: str, palette) -> str:
    """The result as a chip: text in the text colour, outlined in the result's status colour
    (completed: good; abandoned: warning; no key: neutral)."""
    edge = result_color(result, palette)
    return (f"QLabel {{ border: 1px solid {edge.name()}; border-radius: 9px; padding: 1px 9px;"
            f" color: palette(window-text); }}")


class SortItem(QTreeWidgetItem):
    """Sorts by the number stored in UserRole + 1 where there is one (dates, levels, deaths)."""

    def __lt__(self, other):
        col = self.treeWidget().sortColumn()
        a, b = self.data(col, Qt.UserRole + 1), other.data(col, Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        return self.text(col) < other.text(col)
