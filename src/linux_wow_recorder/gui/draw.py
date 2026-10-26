"""Drawing on a paused frame: pen, arrows, boxes, circles and text. The clip browser saves the
picture when the drawing is left; Copy puts it on the clipboard too."""

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QInputDialog, QToolButton, QVBoxLayout,
                               QWidget)

from .. import APP_TITLE

TOOLS = (("pen", "Pen"), ("arrow", "Arrow"), ("box", "Box"), ("circle", "Circle"), ("text", "Text"))
COLORS = ("#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#ffffff")


@dataclass
class Mark:
    """One thing drawn, in the frame's own pixels."""
    tool: str
    color: QColor
    points: list[QPointF] = field(default_factory=list)
    text: str = ""


def line_width(frame: QSize) -> float:
    return max(2.0, frame.height() / 180)


def paint_marks(p: QPainter, marks: list[Mark], frame: QSize):
    """Draws the marks; the painter is set up in the frame's pixels."""
    width = line_width(frame)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(Qt.NoBrush)
    for m in marks:
        p.setPen(QPen(m.color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if m.tool == "pen":
            if len(m.points) == 1:
                p.drawPoint(m.points[0])
            else:
                p.drawPolyline(m.points)
        elif m.tool == "text":
            font = QFont()
            font.setPixelSize(max(12, round(frame.height() / 22)))
            font.setBold(True)
            path = QPainterPath()
            path.addText(m.points[0], font, m.text)
            # dark edges, so it reads on any frame
            p.strokePath(path, QPen(QColor(0, 0, 0, 200), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.fillPath(path, m.color)
        elif len(m.points) == 2:
            a, b = m.points
            if m.tool == "box":
                p.drawRect(QRectF(a, b).normalized())
            elif m.tool == "circle":
                p.drawEllipse(QRectF(a, b).normalized())
            elif m.tool == "arrow":
                p.drawLine(a, b)
                angle = math.atan2(b.y() - a.y(), b.x() - a.x())
                head = width * 5
                for turn in (math.pi * 5 / 6, -math.pi * 5 / 6):
                    p.drawLine(QLineF(b, b + QPointF(math.cos(angle + turn) * head,
                                                     math.sin(angle + turn) * head)))


class DrawCanvas(QWidget):
    """The frame, as big as fits, and what's drawn on it."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.frame = QImage()
        self.marks: list[Mark] = []
        self.drawing: Mark | None = None
        self.tool = "pen"
        self.color = QColor(COLORS[0])
        self.setCursor(Qt.CrossCursor)
        self.setMinimumSize(480, 270)

    def set_frame(self, frame: QImage):
        self.frame = frame
        self.marks = []
        self.drawing = None
        self.update()
        self.changed.emit()

    def picture(self) -> QRectF:
        size = self.frame.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def to_frame(self, pos: QPointF) -> QPointF:
        pic = self.picture()
        scale = self.frame.width() / max(1.0, pic.width())
        return QPointF((pos.x() - pic.x()) * scale, (pos.y() - pic.y()) * scale)

    def image(self) -> QImage:
        """The frame with the marks on it, at the video's size."""
        out = self.frame.convertToFormat(QImage.Format_RGB32)
        p = QPainter(out)
        paint_marks(p, self.marks, out.size())
        p.end()
        return out

    def undo(self):
        if self.marks:
            self.marks.pop()
            self.update()
            self.changed.emit()

    def clear(self):
        self.marks = []
        self.update()
        self.changed.emit()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self.frame.isNull():
            return
        pic = self.picture()
        p.drawImage(pic, self.frame)
        p.setClipRect(pic)
        p.translate(pic.topLeft())
        p.scale(pic.width() / self.frame.width(), pic.height() / self.frame.height())
        paint_marks(p, self.marks + ([self.drawing] if self.drawing else []), self.frame.size())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.frame.isNull():
            return
        at = self.to_frame(event.position())
        if self.tool == "text":
            text, ok = QInputDialog.getText(self, APP_TITLE, "Text:")
            if ok and text.strip():
                self.add(Mark("text", QColor(self.color), [at], text.strip()))
            return
        self.drawing = Mark(self.tool, QColor(self.color), [at] if self.tool == "pen" else [at, at])
        self.update()

    def mouseMoveEvent(self, event):
        if self.drawing is None:
            return
        at = self.to_frame(event.position())
        if self.drawing.tool == "pen":
            self.drawing.points.append(at)
        else:
            self.drawing.points[1] = at
        self.update()

    def mouseReleaseEvent(self, event):
        mark, self.drawing = self.drawing, None
        if mark is None:
            return
        a, b = mark.points[0], mark.points[-1]
        if mark.tool == "pen" or QLineF(a, b).length() >= line_width(self.frame.size()):
            self.add(mark)   # not a stray click with a shape tool
        else:
            self.update()

    def add(self, mark: Mark):
        self.marks.append(mark)
        self.update()
        self.changed.emit()


def swatch(color: str) -> QIcon:
    pix = QPixmap(18, 18)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0, 120), 1))
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(1.5, 1.5, 15, 15))
    p.end()
    return QIcon(pix)


class DrawPage(QWidget):
    """The canvas with its tools above it. Done (or Esc, or playing) goes back to the video."""
    done = Signal()

    def __init__(self):
        super().__init__()
        self.canvas = DrawCanvas()
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 4)
        self.tools = QButtonGroup(self)
        for i, (tool, name) in enumerate(TOOLS):
            b = self._button(name, f"{name} ({i + 1})")
            b.setCheckable(True)
            b.setChecked(tool == self.canvas.tool)
            b.clicked.connect(lambda _=False, tool=tool: setattr(self.canvas, "tool", tool))
            QShortcut(QKeySequence(str(i + 1)), self, b.click)
            self.tools.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(10)
        self.colors = QButtonGroup(self)
        for color in COLORS:
            b = self._button("", "Colour")
            b.setIcon(swatch(color))
            b.setCheckable(True)
            b.setChecked(QColor(color) == self.canvas.color)
            b.clicked.connect(lambda _=False, color=color: setattr(self.canvas, "color", QColor(color)))
            self.colors.addButton(b)
            bar.addWidget(b)
        bar.addSpacing(10)
        self.undo_button = self._button("Undo", "Undo (Ctrl+Z)")
        self.undo_button.clicked.connect(self.canvas.undo)
        QShortcut(QKeySequence.Undo, self, self.canvas.undo)
        self.clear_button = self._button("Clear", "Rub everything out")
        self.clear_button.clicked.connect(self.canvas.clear)
        bar.addWidget(self.undo_button)
        bar.addWidget(self.clear_button)
        bar.addStretch(1)
        self.copy_button = self._button("Copy", "Copy the picture, to paste it somewhere (Ctrl+C)")
        self.copy_button.clicked.connect(self.copy)
        QShortcut(QKeySequence.Copy, self, self.copy)
        done_button = self._button("Done", "Save the picture beside the clip and go back to "
                                           "the video (Esc)")
        done_button.clicked.connect(self.done)
        for b in (self.copy_button, done_button):
            bar.addWidget(b)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.canvas, 1)
        self.canvas.changed.connect(self.show_buttons)
        self.show_buttons()

    def _button(self, text: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setIconSize(QSize(18, 18))
        b.setFocusPolicy(Qt.NoFocus)   # Space plays, not this
        return b

    def show_buttons(self):
        self.undo_button.setEnabled(bool(self.canvas.marks))
        self.clear_button.setEnabled(bool(self.canvas.marks))

    def start(self, frame: QImage):
        self.canvas.set_frame(frame)

    def copy(self):
        if self.canvas.frame.isNull():
            return
        QGuiApplication.clipboard().setImage(self.canvas.image())
        self.copy_button.setText("Copied")
        QTimer.singleShot(1500, self.copy_button, lambda: self.copy_button.setText("Copy"))
