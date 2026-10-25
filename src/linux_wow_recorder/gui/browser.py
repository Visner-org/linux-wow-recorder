"""The clip browser: the clips list, a player, and a timeline of bosses, deaths and lusts."""

import html
import json
import os
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import (QElapsedTimer, QEvent, QFile, QFileSystemWatcher, QLineF, QPointF, QProcess, QRectF,
                            QSettings, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence,
                           QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QFrame, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QProgressDialog, QPushButton, QSlider, QSplitter, QSplitterHandle,
                               QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import shiboken6
from .. import APP_NAME, APP_TITLE, __version__, config, cooldowns, hidechat, highlight, live, service, state
from ..clips import (SHORT_CUT_SECONDS, media_length, read_sidecar, set_keep, sidecar_path,
                     thumbnail_command, thumbnail_path, thumbnail_time, write_sidecar)
from ..config import CONFIG_DIR
from ..config import ConfigError
from . import model, theme
from .hidechat import HideChatDialog

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
    clicking anywhere else seeks there. Hovering names what's under the pointer. While a
    highlight is being made, its start and end are shown on the track and can be dragged."""
    seek = Signal(float)
    marks_changed = Signal(float, float)   # a highlight's start or end was dragged

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
        self.marks: tuple[float, float] | None = None   # the highlight being made
        self.dragging: int | None = None   # 0: its start, 1: its end

    def sizeHint(self) -> QSize:
        return QSize(400, 70)

    def set_clip(self, length: float, details: dict):
        self.length, self.position = length, 0.0
        self.marks = None
        self.bosses = model.boss_spans(details, length)
        self.lusts = model.lust_spans(details, length)
        self.markers = model.markers(details)
        self.update()

    def set_length(self, length: float, details: dict):
        """The player found the length (a clip without one in its details)."""
        position, marks = self.position, self.marks
        self.set_clip(length, details)
        self.position, self.marks = position, marks

    def set_position(self, seconds: float):
        self.position = seconds
        self.update()

    def set_marks(self, marks: tuple[float, float] | None):
        self.marks = marks
        self.update()

    def _mark_at(self, x: float, y: float) -> int | None:
        """The highlight edge under the pointer, if any: 0 its start, 1 its end."""
        if not self.marks or not self.TRACK[0] - 8 <= y <= self.TRACK[1] + 8:
            return None
        near = [(abs(self._x(t) - x), i) for i, t in enumerate(self.marks)
                if abs(self._x(t) - x) <= self.SNAP]
        return min(near)[1] if near else None

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
            if self.marks:   # the highlight: a band over the track, with a handle at each end
                x0, x1 = self._x(self.marks[0]), self._x(self.marks[1])
                band = QColor(theme.color("lust", pal))
                band.setAlpha(90)
                p.setBrush(band)
                p.drawRect(QRectF(x0, self.TRACK[0] - 4, x1 - x0, self.TRACK[1] - self.TRACK[0] + 8))
                p.setBrush(theme.color("lust", pal))
                for x in (x0, x1):
                    p.drawRoundedRect(QRectF(x - 2, self.TRACK[0] - 6, 4, self.TRACK[1] - self.TRACK[0] + 12), 1.5, 1.5)

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
        self.dragging = self._mark_at(x, y)
        if self.dragging is not None:
            return
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
        if self.dragging is not None:
            marks = list(self.marks)
            marks[self.dragging] = self._seconds(x)
            if marks[0] > marks[1]:   # dragged past the other end: they swap
                marks.reverse()
                self.dragging = 1 - self.dragging
            self.set_marks(tuple(marks))
            self.marks_changed.emit(*self.marks)
            return
        self.setCursor(Qt.SizeHorCursor if self._mark_at(x, y) is not None else Qt.PointingHandCursor)
        names = [f"{model.mmss(m.at)}  {m.text}" for m in self._near(x)]
        boss = self._boss_at(x, y)
        if boss and not names:
            names = [f"{model.mmss(boss.start)}–{model.mmss(boss.end)}  {boss.text}"]
        names += [f"{model.mmss(s.start)}  {s.text}" for s in self.lusts
                  if self._x(s.start) <= x <= self._x(s.end) and self.EVENTS[0] <= y <= self.EVENTS[1]
                  and not any(s.text in n for n in names)]
        self.setToolTip("\n".join(names) if names else model.mmss(self._seconds(x)))

    def mouseReleaseEvent(self, event):
        self.dragging = None


class CastStrip(QWidget):
    """The spells cast around where the video is, a lane per player shown: a box per cast with
    the spell's name, scrolling as the video plays, the newest at the playhead line. A hard cast
    has a thin bar back to where it started. Clicking a cast plays from just before it; hovering
    names it."""
    seek = Signal(float)

    BEFORE, AFTER = 12.0, 4.0   # seconds shown before and after the playhead
    WIDER = 5         # major cooldowns only: that many times more, as they're few
    PAD = 8
    ROW = 18          # px per row of boxes; a box that would overlap goes in the lane's next row
    TOP = 3
    LABEL = 84        # px for the players' names, when there's more than one lane
    MAX_BOX = 130     # px: a long name is cut short
    LEAD_IN = 1.0     # seconds played before a clicked cast

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.casts: list[model.CastMark] = []
        self.hues: dict[tuple[str, int], int] = {}
        self.shown: list[str] = []   # the players with a lane, top to bottom
        self.you: str | None = None
        self.major_only = False   # only cooldowns.MAJOR, coloured by kind
        self.position = 0.0
        # While playing, the strip moves on between the player's position updates.
        self.playing = False
        self.since = QElapsedTimer()
        self.since.start()
        self.ticker = QTimer(self, interval=33)
        self.ticker.timeout.connect(self.update)
        self.boxes: list[tuple[QRectF, model.CastMark]] = []   # as last painted
        self.set_shown([])

    def set_casts(self, casts: list[model.CastMark], you: str | None = None):
        self.casts = casts
        self.you = you
        self.hues = model.spell_hues(casts)
        self.position = 0.0
        self.update()

    def set_shown(self, players: list[str]):
        """Whose casts get a lane: two rows for one player, one each for more."""
        self.shown = players
        self.setFixedHeight(self.TOP * 2 + max(1, len(players)) * self._rows() * self.ROW + 2)
        self.update()

    def set_major_only(self, on: bool):
        self.major_only = on
        self.update()

    def _span(self) -> tuple[float, float]:
        """Seconds shown before and after the playhead."""
        k = self.WIDER if self.major_only else 1
        return self.BEFORE * k, self.AFTER * k

    def _rows(self) -> int:
        return 2 if len(self.shown) <= 1 else 1

    def _left(self) -> float:
        return self.PAD + (self.LABEL if len(self.shown) > 1 else 0)

    def set_position(self, seconds: float):
        self.position = seconds
        self.since.restart()
        self.update()

    def set_playing(self, playing: bool):
        self.position = self.now()
        self.since.restart()
        self.playing = playing
        if playing and self.isVisible():
            self.ticker.start()
        else:
            self.ticker.stop()
        self.update()

    def showEvent(self, event):
        if self.playing:
            self.ticker.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.ticker.stop()
        super().hideEvent(event)

    def now(self) -> float:
        """Where the video is: its last reported position, plus the time since while playing
        (at most a second, in case it stalled)."""
        if not self.playing:
            return self.position
        return self.position + min(1.0, self.since.elapsed() / 1000)

    def _scale(self) -> float:
        return max(1, self.width() - self._left() - self.PAD) / sum(self._span())

    def _x(self, seconds: float, now: float) -> float:
        return self._left() + (seconds - now + self._span()[0]) * self._scale()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        dark = theme.is_dark(pal)
        font = p.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        p.setFont(font)
        metrics = p.fontMetrics()
        now = self.now()
        head = self._x(now, now)
        before, after = self._span()
        first, last = now - before - self.MAX_BOX / self._scale(), now + after
        n = self._rows()
        lanes = {who: i for i, who in enumerate(self.shown)}

        # Boxes left to right; each in the first row of its lane free at its start, or else over
        # the end of the row that frees up first (that box is cut short).
        rows: list[list[list]] = [[] for _ in range(len(self.shown) * n)]
        for c in self.casts:
            if c.who not in lanes or not first <= c.at <= last:
                continue
            if self.major_only and not cooldowns.kind(c.spell):
                continue
            x = self._x(c.at, now)
            width = min(self.MAX_BOX, metrics.horizontalAdvance(c.name) + 10)
            mine = rows[lanes[c.who] * n:(lanes[c.who] + 1) * n]
            free = [r for r in mine if not r or r[-1][0] + r[-1][1] + 2 <= x]
            row = free[0] if free else min(mine, key=lambda r: r[-1][0] + r[-1][1])
            if row and row[-1][0] + row[-1][1] + 2 > x:
                row[-1][1] = max(4.0, x - row[-1][0] - 2)
            row.append([x, width, c])

        self.boxes = []
        p.setClipRect(QRectF(self._left(), 0, self.width(), self.height()))
        for i, row in enumerate(rows):
            y = self.TOP + i * self.ROW
            for x, width, c in row:
                rect = QRectF(x, y, width, self.ROW - 2)
                hue = (model.KIND_HUES[cooldowns.kind(c.spell)] if self.major_only
                       else self.hues.get((c.who, c.spell)))
                fill = (QColor.fromHsl(hue, 150, 75 if dark else 205) if hue is not None
                        else pal.mid().color())
                if c.at > now:
                    fill.setAlpha(110)   # still to come
                if c.cast:   # its cast time, as a thin bar leading into it
                    p.setPen(QPen(fill, 3, Qt.SolidLine, Qt.FlatCap))
                    mid = rect.center().y()
                    p.drawLine(QLineF(self._x(c.at - c.cast, now), mid, x, mid))
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(rect, 3, 3)
                if width > 14:
                    p.setPen(pal.windowText().color())
                    p.drawText(rect.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               metrics.elidedText(c.name, Qt.ElideRight, int(width - 8)))
                self.boxes.append((rect, c))
        p.setClipping(False)

        if len(self.shown) > 1:   # whose lane is whose, with a line between lanes
            for i, who in enumerate(self.shown):
                y = self.TOP + i * n * self.ROW
                p.setPen(pal.windowText().color() if who == self.you else theme.muted(pal))
                p.drawText(QRectF(self.PAD, y, self.LABEL - 6, self.ROW - 2),
                           Qt.AlignVCenter | Qt.AlignLeft,
                           metrics.elidedText(model.short_name(who), Qt.ElideRight, self.LABEL - 6))
                if i:
                    line = QColor(theme.muted(pal))
                    line.setAlpha(60)
                    p.setPen(QPen(line, 1))
                    p.drawLine(QLineF(self.PAD, y - 1, self.width() - self.PAD, y - 1))

        # The playhead: where the video is now.
        p.setPen(QPen(pal.highlight().color(), 2))
        p.drawLine(QLineF(head, 0, head, self.height()))
        p.end()

    def _cast_at(self, x: float, y: float) -> model.CastMark | None:
        return next((c for rect, c in reversed(self.boxes) if rect.contains(QPointF(x, y))), None)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x, y = event.position().x(), event.position().y()
        cast = self._cast_at(x, y)
        now = self.now()
        self.seek.emit(max(0.0, cast.at - self.LEAD_IN) if cast
                       else max(0.0, now + (x - self._x(now, now)) / self._scale()))

    def mouseMoveEvent(self, event):
        cast = self._cast_at(event.position().x(), event.position().y())
        self.setToolTip(model.cast_text(cast) if cast else "")


class StayOpenMenu(QMenu):
    """A menu of tick boxes that stays open while they're ticked, so several can be."""

    def mouseReleaseEvent(self, event):
        action = self.activeAction()
        if action and action.isCheckable() and action.isEnabled():
            action.trigger()
            return
        super().mouseReleaseEvent(event)


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
        self.chat_job = None   # the copy with the chat hidden being made
        self.highlight_job = None   # the highlight being saved
        self.frame_process: QProcess | None = None   # the frame the chat box is drawn on
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
        # The result filter: one button that steps through All, Completed, Abandoned (or the
        # raid's All, Kills, Wipes) and says which is on.
        self.filter_key = "all"
        self.filter_button = QPushButton()
        self.filter_button.clicked.connect(self.next_filter)
        filters = QHBoxLayout()
        filters.addWidget(self.filter_button)
        filters.addStretch(1)
        # Cards or table: one button, named for the view it switches to.
        self.view_button = QPushButton()
        self.view_button.clicked.connect(lambda: self.set_view(1 - self.list_stack.currentIndex()))
        filters.addWidget(self.view_button)
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
        self.tree.itemPressed.connect(lambda item: self.seen(item.data(0, Qt.UserRole)))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)   # Shift and Ctrl

        self.thumbs: dict[str, QPixmap] = {}
        self.thumb_queue: list[model.ClipInfo] = []
        self.thumb_tried: set[str] = set()
        self.thumb_process: QProcess | None = None
        # A clip just made (a highlight, a copy with the chat hidden) flashes until it's clicked,
        # to show where it went.
        self.fresh: set[str] = set()
        self.flash_on = False
        self.flash_timer = QTimer(self, interval=500)
        self.flash_timer.timeout.connect(self.flash)
        self.cards = QListWidget()
        self.cards.setItemDelegate(ClipCard(self.thumbnail, self.cards, self.flashing))
        self.cards.setUniformItemSizes(True)
        self.cards.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards.setResizeMode(QListWidget.Adjust)
        self.cards.currentItemChanged.connect(lambda item, _: self.select(item.data(Qt.UserRole) if item else None))
        self.cards.itemActivated.connect(lambda *_: self.player.play())
        self.cards.itemPressed.connect(lambda item: self.seen(item.data(Qt.UserRole)))
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
        self.timeline.marks_changed.connect(lambda *_: self.show_marks())
        self.time_label = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.audio.setVolume(0.8)
        # A speaker before the volume slider (so it doesn't pass for a second timeline): mutes.
        self.mute_button = QToolButton()
        self.mute_button.setAutoRaise(True)
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setFocusPolicy(Qt.NoFocus)   # Space plays, not this
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self.set_muted)
        self.set_muted(False)
        # Full screen: only the video and these controls, the timeline included.
        self.full_button = QToolButton()
        self.full_button.setAutoRaise(True)
        self.full_button.setIconSize(QSize(20, 20))
        self.full_button.setFocusPolicy(Qt.NoFocus)   # Space plays, not this
        self.full_button.clicked.connect(self.toggle_full_screen)
        self.full_screen = False
        self.was_maximized = False
        # The group's casts under the video, for clips that have them: Casts shows or hides
        # them, its menu ticks whose (you, unless other players are ticked).
        self.cast_strip = CastStrip()
        self.cast_strip.seek.connect(self.seek)
        self.casts_menu = StayOpenMenu(self)
        self.casts_button = QToolButton()
        self.casts_button.setText("Casts")
        self.casts_button.setCheckable(True)
        self.casts_button.setAutoRaise(True)
        self.casts_button.setFocusPolicy(Qt.NoFocus)   # Space plays, not this
        self.casts_button.setToolTip("Show the spells cast, under the video (C); the arrow picks "
                                     "whose")
        self.casts_button.setMenu(self.casts_menu)
        self.casts_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.casts_button.toggled.connect(self.show_casts)
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.timeline, 1)
        controls.addWidget(self.time_label)
        controls.addSpacing(6)
        controls.addWidget(self.casts_button)
        controls.addWidget(self.mute_button)
        controls.addWidget(self.volume)
        controls.addWidget(self.full_button)

        # Making a highlight: its start and end (also dragged on the timeline), and Save.
        self.marks_label = QLabel()
        start_here = QPushButton("Start here")
        start_here.setToolTip("Start the highlight where the video is (I)")
        start_here.clicked.connect(lambda: self.set_mark(0))
        end_here = QPushButton("End here")
        end_here.setToolTip("End the highlight where the video is (O)")
        end_here.clicked.connect(lambda: self.set_mark(1))
        self.save_highlight_button = QPushButton("Save highlight")
        self.save_highlight_button.setObjectName("primary")
        self.save_highlight_button.clicked.connect(self.save_highlight)
        cancel = QPushButton("Cancel")
        cancel.setToolTip("Stop making a highlight (Esc)")
        cancel.clicked.connect(self.clear_marks)
        self.highlight_bar = QWidget()
        hb = QHBoxLayout(self.highlight_bar)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.addWidget(self.marks_label, 1)
        for b in (start_here, end_here, self.save_highlight_button, cancel):
            b.setFocusPolicy(Qt.NoFocus)   # Space plays, I and O mark
            hb.addWidget(b)
        self.highlight_bar.hide()
        # The player's keys are shortcuts, not keyPressEvent: a clip list or the events with
        # focus would take them (letters for type-ahead search, Space for selecting). The search
        # box still gets letters, Space and arrows (a text box claims them before shortcuts).
        for key, slot in ((Qt.Key_Space, self.toggle_play), (Qt.Key_F, self.toggle_full_screen),
                          (Qt.Key_Left, lambda: self.skip(-5)), (Qt.Key_Right, lambda: self.skip(5)),
                          (Qt.Key_I, lambda: self.set_mark(0)), (Qt.Key_O, lambda: self.set_mark(1)),
                          (Qt.Key_C, self.casts_button.toggle),
                          (Qt.Key_Escape, self.escape)):
            QShortcut(QKeySequence(key), self, slot)

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
        self.events.setContextMenuPolicy(Qt.CustomContextMenu)
        self.events.customContextMenuRequested.connect(self.event_menu)

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
        self.hide_chat_button = self._tool_button(
            "Hide chat: makes a copy of the clip with a black box over the chat",
            self.hide_chat, "view-hidden", QStyle.SP_DialogNoButton)
        self.highlight_button = self._tool_button(
            "Make a highlight: save part of this clip as a clip of its own (I and O mark it)",
            self.start_highlight, "edit-cut", QStyle.SP_DialogSaveButton)
        # Only there when an after_clips command is set (Settings > Advanced).
        self.hook_button = self._tool_button("Run your after_clips command on this clip again",
                                             self.run_hook, "system-run", QStyle.SP_BrowserReload)
        self.trash_button = self._tool_button("Move to trash", self.delete)
        self.trash_button.setIconSize(QSize(20, 20))
        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.result_chip, 0, Qt.AlignVCenter)
        header.addSpacing(12)
        for b in (self.keep_button, open_button, folder_button, self.highlight_button,
                  self.hide_chat_button, self.youtube_button, self.hook_button):
            header.addWidget(b, 0, Qt.AlignVCenter)
        header.addSpacing(10)
        header.addWidget(self.trash_button, 0, Qt.AlignVCenter)
        self.action_widgets = [self.keep_button, open_button, folder_button, self.highlight_button,
                               self.hide_chat_button, self.youtube_button, self.hook_button,
                               self.trash_button]

        # What full screen hides: the list, and the clip's details and actions.
        self.left = left
        self.details_panel = QWidget()
        dv = QVBoxLayout(self.details_panel)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.addLayout(header)
        dv.addWidget(self.facts)
        dv.addWidget(self.events, 1)

        player = QWidget()
        pv = QVBoxLayout(player)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(self.video_stack, 1)
        pv.addWidget(self.cast_strip)
        pv.addLayout(controls)
        pv.addWidget(self.highlight_bar)
        # Drag the bar under the timeline to give the video more room, or the details.
        right = BarSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.setHandleWidth(10)   # easy to grab, not a 1 px line
        right.addWidget(player)
        right.addWidget(self.details_panel)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)
        right.setSizes([540, 360])
        right.splitterMoved.connect(
            lambda *_: self.settings.setValue("player_sizes", right.sizes()))
        self.player_split = right
        # Double-clicking the video goes full screen and back.
        for w in (self.video, self.poster, *self.video.findChildren(QWidget)):
            w.installEventFilter(self)
        self.show_full_screen_button()

        split = BarSplitter()
        split.setHandleWidth(10)
        split.addWidget(left)
        split.addWidget(right)
        # A bigger or maximized window grows the video, not the list (wider only adds blank card).
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
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
        # Only the sizes: saveState() would also bring back an old handle width.
        sizes = self.settings.value("player_sizes")
        if isinstance(sizes, list) and len(sizes) == 2:
            self.player_split.setSizes([int(n) for n in sizes])
        self.casts_button.setChecked(self.settings.value("show_casts", "true") != "false")
        view = 1 if self.settings.value("view") == "table" else 0
        self.list_stack.setCurrentIndex(view)
        self.show_view_button()
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
            # Tied to the button, so a window closed meanwhile cancels it.
            QTimer.singleShot(PREVIEW_MS, self.live_switch, lambda: self.live_switch.setEnabled(True))

    def repick(self):
        """Opens the screen-share dialog again, to record another window."""
        if why := state.request_repick():
            QMessageBox.warning(self, APP_TITLE, why)
        else:
            self.live_repick.setEnabled(False)   # until the service has restarted it
            QTimer.singleShot(PREVIEW_MS, self.live_repick, lambda: self.live_repick.setEnabled(True))

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
        self.show_parts()
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

    def part_on(self, part: str) -> bool:
        """Whether Settings > Clips window has this part (a config.CLIPS_WINDOW key) on."""
        return not self.cfg or self.cfg.clips_window.get(part, True)

    def show_parts(self):
        """The views Settings > Clips window has on; Keystones alone needs no button."""
        raids, sessions = self.part_on("raids"), self.part_on("full_sessions")
        self.mode_buttons[model.RAIDS].setVisible(raids)
        self.mode_buttons[model.SESSIONS].setVisible(sessions)
        self.mode_buttons[model.KEYS].setVisible(raids or sessions)
        if (self.mode == model.RAIDS and not raids) or (self.mode == model.SESSIONS and not sessions):
            self.mode = model.KEYS
            self.mode_buttons[model.KEYS].setChecked(True)
            self.current = None
            self.show_mode()

    def fill_list(self, *_args, select=None):
        select = str(select or (self.current.path if self.current else "")) or None
        key = "all" if self.mode == model.SESSIONS else self.filter_key
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
                c.dungeon + (" (highlight)" if c.highlight else "")
                + (" (chat hidden)" if c.chat_hidden else ""),
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

    def scroll_to_current_card(self):
        if item := self.cards.currentItem():
            self.cards.scrollToItem(item)

    def show_new(self, path: Path):
        """Shows a clip just made, scrolled to in the list and flashing until it's clicked."""
        self.fresh.add(str(path))
        self.flash_timer.start()
        self.fill_list(select=path)

    def flashing(self, path: str) -> bool:
        """Whether path's card or row is lit up right now (ClipCard asks)."""
        return self.flash_on and path in self.fresh

    def flash(self):
        self.flash_on = not self.flash_on
        if not self.fresh:
            self.flash_on = False
            self.flash_timer.stop()
        lit = QColor(theme.color("lust", self.palette()))
        lit.setAlpha(90)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            on = self.flashing(item.data(0, Qt.UserRole))
            for col in range(self.tree.columnCount()):
                item.setBackground(col, lit if on else Qt.NoBrush)
        self.cards.viewport().update()

    def seen(self, path: str):
        """A clicked clip stops flashing."""
        if path in self.fresh:
            self.fresh.discard(path)
            self.flash()

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
        if self.filter_key not in self.filters():   # Kills isn't Completed: back to All
            self.filter_key = "all"
        self.filter_button.setVisible(not sessions)   # results are per run
        self.show_filter_button()
        self.search.setPlaceholderText("Search bosses and raids" if raids else "Search dungeons")
        self.tree.setHeaderLabels(SESSION_COLUMNS if sessions else RAID_COLUMNS if raids
                                  else COLUMNS)

    def filters(self) -> dict[str, str]:
        return model.RAID_FILTERS if self.mode == model.RAIDS else model.FILTERS

    def next_filter(self):
        keys = list(self.filters())
        self.filter_key = keys[(keys.index(self.filter_key) + 1) % len(keys)]
        self.show_filter_button()
        self.fill_list()

    def show_filter_button(self):
        filters = self.filters()
        keys = list(filters)
        following = filters[keys[(keys.index(self.filter_key) + 1) % len(keys)]]
        self.filter_button.setText(f"Showing: {filters[self.filter_key]}")
        self.filter_button.setToolTip(f"Click for: {following}")

    def set_view(self, view: int):
        self.list_stack.setCurrentIndex(view)
        self.settings.setValue("view", "table" if view else "cards")
        self.show_view_button()
        self.select(self.current.path if self.current else None)   # just the one, as in the other
        self.show_count()

    def show_view_button(self):
        table = self.list_stack.currentIndex() == 1
        self.view_button.setText("Cards" if table else "Table")
        self.view_button.setToolTip("Show the clips as cards with a picture" if table
                                    else "Show the clips as a compact table")

    def select(self, path: str | None):
        """Shows the clip at path (None: no clip) and marks it in both the cards and the table."""
        for i in range(self.cards.count()):
            if self.cards.item(i).data(Qt.UserRole) == path:
                if self.cards.currentRow() != i:   # already: a click, maybe with Shift or Ctrl
                    self.cards.blockSignals(True)
                    self.cards.setCurrentRow(i)
                    self.cards.blockSignals(False)
                # once the list has laid the cards out (it does that later); the item itself may
                # be gone by then (the list refilled), so whichever is current
                QTimer.singleShot(0, self.cards, self.scroll_to_current_card)
                break
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).data(0, Qt.UserRole) == path:
                if self.tree.currentItem() is not self.tree.topLevelItem(i):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(self.tree.topLevelItem(i))
                    self.tree.blockSignals(False)
                self.tree.scrollToItem(self.tree.topLevelItem(i))
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
            self.cast_strip.set_casts([])
            self.fill_casts_menu([], None)
            self.show_casts()
            self.highlight_bar.hide()
            return
        if not same_file:
            self.video_stack.setCurrentWidget(self.poster)
            self.player.setSource(QUrl.fromLocalFile(str(clip.path)))
            self.clear_marks()   # the marks were the other clip's
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
        if self.thumb_process is not process or not shiboken6.isValid(process):
            return   # errorOccurred and finished both came, or the window closed mid-picture
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
        self.result_chip.setStyleSheet(chip_style(clip.status, self.palette()))
        facts = [time.strftime("%a %Y-%m-%d %H:%M", time.localtime(clip.started))]
        if clip.is_raid:
            facts.append(clip.raid)
            facts.append(f"pull {clip.length}")
        elif clip.highlight:
            h = d["highlight"]
            facts.append(f"highlight {clip.length}, from {model.mmss(h['start'])} in the clip")
        elif not clip.is_session:
            facts.append(f"run {clip.length}")
            if clip.time_left:
                facts.append(clip.time_left)
        elif clip.length:
            facts.append(f"recorded {clip.length}")
        if clip.bosses_killed is not None and not clip.is_raid and not clip.highlight:
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
        if d.get("chat_hidden"):
            facts.append("chat hidden")
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
        marks = self.timeline.marks
        self.timeline.set_clip(d.get("clip_length") or self.player.duration() / 1000, d)
        self.timeline.set_marks(marks)   # a reload while making a highlight keeps it
        casts = model.casts(d)
        if casts != self.cast_strip.casts:
            self.cast_strip.set_casts(casts, d.get("player"))
            self.cast_strip.set_position(self.player.position() / 1000)
            self.fill_casts_menu(model.casters(d), d.get("player"))
        self.show_casts()
        self.keep_button.setChecked(clip.keep)
        self.keep_button.setText("★ Kept" if clip.keep else "☆ Keep")
        # A recording in sessions/ is never deleted anyway, and its details file would stay
        # behind when it's cut and moved.
        # Nor in another folder: retention never deletes anything there.
        self.keep_button.setVisible(clip.result != "uncut" and not clip.elsewhere)
        self.keep_button.setToolTip(
            "Keep this full recording forever, whatever \"Keep full recordings\" says"
            if clip.is_session else "Keep this clip forever, whatever \"Keep clips\" says")
        self.youtube_button.setVisible(not clip.is_session and self.part_on("youtube"))
        self.hide_chat_button.setVisible(not clip.is_session and self.part_on("hide_chat"))
        self.highlight_button.setVisible(self.can_highlight())
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
        end = row.children[-1].at if row.children and row.children[-1].kind in ("kill", "wipe") else None
        item.setData(0, Qt.UserRole + 1, (row.kind if not row.children else "pull", end))
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
        self.cast_strip.set_playing(playing)
        if playing and self.current:
            self.video_stack.setCurrentWidget(self.video)
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing
                                                           else QStyle.SP_MediaPlay))
        self.play_button.setText(" Pause" if playing else " Play")

    def on_position(self, ms: int):
        self.timeline.set_position(ms / 1000)
        self.cast_strip.set_position(ms / 1000)
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
        self.cast_strip.set_position(seconds)

    def show_casts(self, *_args):
        """The cast strip, when it's ticked, the clip has casts and someone is ticked in the
        menu; the button only when the clip has casts."""
        has = bool(self.cast_strip.casts)
        self.casts_button.setVisible(has)
        self.cast_strip.setVisible(has and self.casts_button.isChecked()
                                   and bool(self.cast_strip.shown))
        if _args:   # ticked or unticked by the user
            self.settings.setValue("show_casts", "true" if _args[0] else "false")

    def cast_choice(self) -> tuple[bool, set[str]]:
        """Whose casts the user wants: (you, the other players by name), from gui.ini."""
        try:
            others = set(json.loads(self.settings.value("cast_players", "[]")))
        except (TypeError, ValueError):
            others = set()
        return self.settings.value("cast_hide_you", "false") != "true", others

    def fill_casts_menu(self, casters: list[str], you: str | None):
        """A tick per player in the clip: you first, then the others by how much they cast."""
        self.casts_menu.clear()
        show_you, others = self.cast_choice()
        major = self.casts_menu.addAction("Major cooldowns only")
        major.setToolTip("Big damage, healing and defensive spells, coloured red, green and blue")
        major.setCheckable(True)
        major.setChecked(self.settings.value("cast_major_only", "false") == "true")
        major.toggled.connect(self.set_major_only)
        self.casts_menu.addSeparator()
        for name in casters:
            mine = name == you
            action = self.casts_menu.addAction(f"{model.short_name(name)} (you)" if mine
                                               else model.short_name(name))
            action.setCheckable(True)
            action.setChecked(show_you if mine else name in others)
            action.setData(name)
            action.toggled.connect(lambda on, name=name, mine=mine: self.tick_caster(name, mine, on))
        if len(casters) > 1:
            self.casts_menu.addSeparator()
            self.casts_menu.addAction("Only you", lambda: self.tick_casters(only_you=True))
            self.casts_menu.addAction("Everyone", lambda: self.tick_casters(only_you=False))
        self.show_cast_lanes()

    def tick_caster(self, name: str, mine: bool, on: bool):
        show_you, others = self.cast_choice()
        if mine:
            self.settings.setValue("cast_hide_you", "false" if on else "true")
        else:
            others = others | {name} if on else others - {name}
            self.settings.setValue("cast_players", json.dumps(sorted(others)))
        self.show_cast_lanes()

    def tick_casters(self, only_you: bool):
        for action in self.casts_menu.actions():
            if action.isCheckable() and action.data():   # a player's
                action.setChecked(not only_you or action.data() == self.cast_strip.you)

    def show_cast_lanes(self):
        self.cast_strip.set_shown([a.data() for a in self.casts_menu.actions()
                                   if a.data() and a.isChecked()])
        self.cast_strip.set_major_only(self.settings.value("cast_major_only", "false") == "true")
        self.show_casts()

    def set_major_only(self, on: bool):
        self.settings.setValue("cast_major_only", "true" if on else "false")
        self.cast_strip.set_major_only(on)

    def set_muted(self, muted: bool):
        self.audio.setMuted(muted)
        self.volume.setEnabled(not muted)
        self.mute_button.setIcon(QIcon.fromTheme(
            "audio-volume-muted" if muted else "audio-volume-high",
            self.style().standardIcon(QStyle.SP_MediaVolumeMuted if muted else QStyle.SP_MediaVolume)))
        self.mute_button.setToolTip("Unmute" if muted else "Mute")

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonDblClick:
            self.toggle_full_screen()
            return True
        return super().eventFilter(obj, event)

    def toggle_full_screen(self):
        """The video fills the screen, with the play button and timeline under it; again (or
        Esc) goes back to the window as it was."""
        self.full_screen = not self.full_screen
        self.left.setVisible(not self.full_screen)
        self.details_panel.setVisible(not self.full_screen)
        if self.full_screen:
            self.was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self.was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.show_full_screen_button()

    def show_full_screen_button(self):
        if self.full_screen:
            self.full_button.setIcon(QIcon.fromTheme(
                "view-restore", self.style().standardIcon(QStyle.SP_TitleBarNormalButton)))
            self.full_button.setToolTip("Leave full screen (Esc or F, or double-click the video)")
        else:
            self.full_button.setIcon(QIcon.fromTheme(
                "view-fullscreen", self.style().standardIcon(QStyle.SP_TitleBarMaxButton)))
            self.full_button.setToolTip("Full screen (F, or double-click the video)")

    def skip(self, seconds: int):
        if self.player.duration():
            self.player.setPosition(max(0, self.player.position() + seconds * 1000))

    def escape(self):
        """Esc stops making a highlight, or else leaves full screen."""
        if self.timeline.marks:
            self.clear_marks()
        elif self.full_screen:
            self.toggle_full_screen()

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

    def busy(self) -> bool:
        """True while a copy with the chat hidden, a highlight, or an after_clips command is being
        made."""
        return bool(self.chat_job or self.frame_process or self.hook_process or self.highlight_job)

    def hide_chat(self):
        """Asks where the chat is on a frame of the clip, then makes a copy with a black box
        there, in the background. The frame is read in the background too (a slow drive)."""
        clip = self.current
        if clip is None or self.chat_job or self.frame_process or not self.cfg:
            return
        self.player.pause()
        at = (self.player.position() / 1000 if self.player.position() > 0
              else thumbnail_time(clip.details))
        tmp = Path(tempfile.mkdtemp(prefix=f"{APP_NAME}-"))
        frame_file = tmp / "frame.jpg"
        cmd = hidechat.frame_command(clip.path, at, frame_file)
        process = self.frame_process = QProcess(self)
        process.finished.connect(lambda *_: self.hide_chat_frame(process, clip, tmp))
        process.errorOccurred.connect(lambda e: e == QProcess.FailedToStart
                                      and self.hide_chat_frame(process, clip, tmp))
        self.hide_chat_button.setEnabled(False)
        process.start(cmd[0], cmd[1:])
        QTimer.singleShot(60_000, process, process.kill)   # a drive that stopped answering

    def hide_chat_frame(self, process: QProcess, clip: model.ClipInfo, tmp: Path):
        if self.frame_process is not process:
            return   # errorOccurred and finished both came
        self.frame_process = None
        process.deleteLater()
        frame = QPixmap(str(tmp / "frame.jpg"))
        shutil.rmtree(tmp, ignore_errors=True)
        self.hide_chat_button.setEnabled(True)
        if frame.isNull():
            QMessageBox.warning(self, APP_TITLE, "Couldn't read a picture from this clip.")
            return
        dialog = HideChatDialog(frame, hidechat.parse_box(self.settings.value("hide_chat_box")), self)
        if dialog.exec() != HideChatDialog.Accepted:
            return
        self.settings.setValue("hide_chat_box", hidechat.box_text(dialog.box))
        out = hidechat.output_path(clip.path, self.cfg.clips_dir)
        part = out.with_name(out.name + ".part")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't make the copy: {e}")
            return
        length = (clip.details.get("clip_length") or media_length(clip.path)
                  or self.player.duration() / 1000 or 1)
        progress = QProgressDialog("Hiding the chat...", "Cancel", 0, int(length), self)
        progress.setWindowTitle(APP_TITLE)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.canceled.connect(self.cancel_hide_chat)
        self.chat_job = {"clip": clip, "out": out, "part": part, "progress": progress,
                         "tries": hidechat.commands(clip.path, part, dialog.box,
                                                    hidechat.video_bitrate(clip.path)),
                         "process": None, "error": "", "canceled": False}
        self.hide_chat_button.setEnabled(False)
        progress.show()
        self.next_hide_chat_try()

    def next_hide_chat_try(self):
        job = self.chat_job
        cmd = job["tries"].pop(0)
        process = job["process"] = QProcess(self)
        process.readyReadStandardOutput.connect(lambda: self.hide_chat_progress(process))
        process.finished.connect(lambda *_: self.hide_chat_finished(process))
        process.errorOccurred.connect(lambda e: e == QProcess.FailedToStart
                                      and self.hide_chat_finished(process))
        process.start(cmd[0], cmd[1:])

    def hide_chat_progress(self, process: QProcess):
        seconds = hidechat.progress_seconds(bytes(process.readAllStandardOutput()).decode(errors="replace"))
        if seconds is not None and self.chat_job and self.chat_job["process"] is process:
            self.chat_job["progress"].setValue(min(int(seconds), self.chat_job["progress"].maximum()))

    def cancel_hide_chat(self):
        if self.chat_job and self.chat_job["process"]:
            self.chat_job["canceled"] = True
            self.chat_job["process"].kill()

    def hide_chat_finished(self, process: QProcess):
        job = self.chat_job
        if job is None or job["process"] is not process:
            return   # errorOccurred and finished both came
        job["process"] = None
        process.deleteLater()
        ok = (process.exitStatus() == QProcess.NormalExit and process.exitCode() == 0
              and process.error() != QProcess.FailedToStart and job["part"].exists())
        if not ok:
            job["error"] = bytes(process.readAllStandardError()).decode(errors="replace").strip()
            job["part"].unlink(missing_ok=True)
            if job["tries"] and not job["canceled"]:
                self.next_hide_chat_try()   # no such encoder here: the next one
                return
        self.chat_job = None
        job["progress"].close()
        self.hide_chat_button.setEnabled(True)
        if job["canceled"]:
            return
        if not ok:
            QMessageBox.warning(self, APP_TITLE, "Couldn't make the copy."
                                + (f"\n\n{job['error'][-1500:]}" if job["error"] else ""))
            return
        try:
            job["part"].replace(job["out"])
            if job["clip"].details:
                write_sidecar(job["out"], hidechat.copied_details(job["clip"].details))
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't make the copy: {e}")
            return
        self.reload()
        self.show_new(job["out"])   # the copy
        QMessageBox.information(self, APP_TITLE, "Copy made with the chat hidden.")

    # ── Highlights ──

    def can_highlight(self) -> bool:
        clip = self.current
        return bool(clip and self.cfg and not clip.is_session and self.part_on("highlights"))

    def clip_seconds(self) -> float:
        return self.timeline.length or self.player.duration() / 1000

    def start_highlight(self):
        """Opens the highlight bar with 30 s from where the video is; the marks are then moved."""
        if not self.can_highlight():
            return
        if not self.timeline.marks:
            at = self.player.position() / 1000
            self.timeline.set_marks((at, min(self.clip_seconds(), at + 30)))
        self.show_marks()

    def set_mark(self, which: int):
        """The highlight starts (0) or ends (1) where the video is."""
        if not self.can_highlight():
            return
        at = self.player.position() / 1000
        marks = list(self.timeline.marks or (at, min(self.clip_seconds(), at + 30)))
        marks[which] = at
        if marks[0] > marks[1]:   # an end before the start, or the other way: 30 s from it
            marks[1 - which] = max(0.0, at - 30) if which else min(self.clip_seconds(), at + 30)
        self.timeline.set_marks(tuple(marks))
        self.show_marks()

    def event_menu(self, pos):
        item = self.events.itemAt(pos)
        at = item.data(0, Qt.UserRole) if item else None
        if at is None or not self.can_highlight():
            return
        kind, end = item.data(0, Qt.UserRole + 1)
        menu = QMenu(self)
        menu.addAction("Make a highlight of this", lambda: self.highlight_event(kind, at, end))
        menu.exec(self.events.viewport().mapToGlobal(pos))

    def highlight_event(self, kind: str, at: float, end: float | None):
        self.timeline.set_marks(model.highlight_around(kind, at, end, self.clip_seconds()))
        self.show_marks()
        self.seek(self.timeline.marks[0])

    def show_marks(self):
        start, end = self.timeline.marks
        self.marks_label.setText(f"Highlight {model.mmss(start)} – {model.mmss(end)}  ·  "
                                 f"{highlight.length_text(end - start)}")
        self.save_highlight_button.setEnabled(end - start >= highlight.MIN_SECONDS
                                              and self.highlight_job is None)
        self.highlight_bar.show()

    def clear_marks(self):
        self.timeline.set_marks(None)
        self.highlight_bar.hide()

    def save_highlight(self):
        """Saves the marked part as a clip of its own, beside the clip, in the background."""
        clip = self.current
        if not clip or not self.timeline.marks or self.highlight_job or not self.cfg:
            return
        start, end = self.timeline.marks
        out = highlight.output_path(clip.path, self.cfg.clips_dir, start, end)
        if out.exists() and QMessageBox.question(
                self, APP_TITLE, "This highlight is already saved. Save it again?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        part = out.with_name(out.name + ".part")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Couldn't save the highlight: {e}")
            return
        cmd = highlight.command(clip.path, start, end, part)
        process = QProcess(self)
        self.highlight_job = {"clip": clip, "out": out, "part": part, "marks": (start, end),
                              "process": process}
        process.finished.connect(lambda *_: self.highlight_saved(process))
        process.errorOccurred.connect(lambda e: e == QProcess.FailedToStart
                                      and self.highlight_saved(process))
        self.save_highlight_button.setEnabled(False)
        self.save_highlight_button.setText("Saving...")
        process.start(cmd[0], cmd[1:])

    def highlight_saved(self, process: QProcess):
        job = self.highlight_job
        if job is None or job["process"] is not process:
            return   # errorOccurred and finished both came
        self.highlight_job = None
        process.deleteLater()
        self.save_highlight_button.setText("Save highlight")
        ok = (process.exitStatus() == QProcess.NormalExit and process.exitCode() == 0
              and process.error() != QProcess.FailedToStart and job["part"].exists())
        start, end = job["marks"]
        if ok and (media_length(job["part"]) or 0) < end - start - SHORT_CUT_SECONDS:
            ok = False   # ffmpeg took a read error for the end of the clip
        if ok:
            try:
                job["part"].replace(job["out"])
                write_sidecar(job["out"], highlight.details(job["clip"].details, job["clip"].path,
                                                            *job["marks"]))
            except OSError as e:
                job["part"].unlink(missing_ok=True)
                QMessageBox.warning(self, APP_TITLE, f"Couldn't save the highlight: {e}")
                self.show_marks()
                return
            self.clear_marks()
            self.reload()
            self.show_new(job["out"])   # the highlight
            return
        error = bytes(process.readAllStandardError()).decode(errors="replace").strip()
        job["part"].unlink(missing_ok=True)
        QMessageBox.warning(self, APP_TITLE, "Couldn't save the highlight."
                            + (f"\n\n{error[-1500:]}" if error else ""))
        if self.timeline.marks:
            self.show_marks()

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
        if self.full_screen:   # opens as a window next time
            self.toggle_full_screen()
        self.player.pause()
        self.live_timer.stop()
        super().closeEvent(event)
        self.closed.emit()


class ClipCard(QStyledItemDelegate):
    """A clip as a card: its picture, the dungeon and level, the result, and what happened."""

    def __init__(self, thumbnail, parent, flashing=lambda _path: False):
        super().__init__(parent)
        self.thumbnail = thumbnail   # ClipBrowser.thumbnail
        self.flashing = flashing     # ClipBrowser.flashing

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
        edge = result_color(clip.status, pal)
        p.setPen(QPen(text if selected else edge, 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), chip.height() / 2, chip.height() / 2)
        p.setPen(text)
        p.drawText(chip, Qt.AlignCenter, clip.result_text)
        p.drawText(QRectF(chip.right() + 10, y, width, chip.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   clip.length)
        cx = chip.right() + 10 + fm.horizontalAdvance(clip.length) + 14
        # A highlight, or the copy made with Hide chat, beside its clip
        for shown, icon_name, label in ((clip.highlight, "edit-cut", "Highlight"),
                                        (clip.chat_hidden, "view-hidden", "Chat hidden")):
            if not shown:
                continue
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                icon.paint(p, int(cx), int(y + (chip.height() - 16) / 2), 16, 16)
                cx += 20
            p.setPen(muted)
            p.drawText(QRectF(cx, y, max(0.0, r.right() - cx), chip.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, label)
            cx += fm.horizontalAdvance(label) + 14

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
        if self.flashing(str(clip.path)):   # just made: an outline that blinks until clicked
            p.setPen(QPen(theme.color("lust", pal), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(option.rect).adjusted(2, 2, -2, -2), 6, 6)
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


class BarSplitter(QSplitter):
    """A splitter whose handle shows: a dark line across it, the highlight colour on hover."""

    def createHandle(self):
        return BarHandle(self.orientation(), self)


class BarHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WA_Hover)   # repaints on enter and leave

    def paintEvent(self, event):
        p = QPainter(self)
        bar = self.rect().adjusted(0, 3, 0, -3) if self.orientation() == Qt.Vertical \
            else self.rect().adjusted(3, 0, -3, 0)
        p.fillRect(bar, self.palette().highlight() if self.underMouse() else QColor("black"))
        # Five grip dots in the middle (the style's own would paint over the line).
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#bbbbbb"))
        c = QRectF(self.rect()).center()
        for i in range(-2, 3):
            d = QPointF(i * 6, 0) if self.orientation() == Qt.Vertical else QPointF(0, i * 6)
            p.drawEllipse(c + d, 1.5, 1.5)


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
            theme.color("warning", palette) if result in ("abandoned", "wipe", "overtime")
            else theme.muted(palette))


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
