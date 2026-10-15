"""The "Hide chat" dialog: a frame of the clip with a box to drag over the chat."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from .. import APP_TITLE, hidechat

HANDLE = 14   # px: the corner to drag to resize


class BoxPicker(QWidget):
    """The frame, with the box over it. Drag the box to move it, its corner to resize it, or
    anywhere else to draw a new one."""
    changed = Signal()

    def __init__(self, frame: QPixmap, box: hidechat.Box):
        super().__init__()
        self.frame = frame
        self.box = hidechat.clamp(box)
        self.drag = None   # ("move" | "resize" | "draw", where it started, the box then)
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)

    def picture(self) -> QRectF:
        """Where the frame is drawn: as big as fits, centred."""
        size = self.frame.size().scaled(self.size(), Qt.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def box_rect(self) -> QRectF:
        pic, (x, y, w, h) = self.picture(), self.box
        return QRectF(pic.x() + x * pic.width(), pic.y() + y * pic.height(),
                      w * pic.width(), h * pic.height())

    def _fraction(self, pos: QPointF) -> tuple[float, float]:
        pic = self.picture()
        return ((pos.x() - pic.x()) / max(1.0, pic.width()),
                (pos.y() - pic.y()) / max(1.0, pic.height()))

    def _corner(self, pos: QPointF) -> bool:
        r = self.box_rect()
        return abs(pos.x() - r.right()) <= HANDLE and abs(pos.y() - r.bottom()) <= HANDLE

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        p.drawPixmap(self.picture().toRect(), self.frame)
        r = self.box_rect()
        p.fillRect(r, QColor(0, 0, 0, 200))   # see-through here, solid in the copy
        p.setPen(QPen(QColor("#e00000"), 2))
        p.drawRect(r)
        p.fillRect(QRectF(r.right() - HANDLE / 2, r.bottom() - HANDLE / 2, HANDLE, HANDLE),
                   QColor("#e00000"))

    def mousePressEvent(self, event):
        pos = event.position()
        if self._corner(pos):
            kind = "resize"
        elif self.box_rect().contains(pos):
            kind = "move"
        else:
            kind = "draw"
        self.drag = (kind, self._fraction(pos), self.box)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self.drag is None:
            self.setCursor(Qt.SizeFDiagCursor if self._corner(pos)
                           else Qt.SizeAllCursor if self.box_rect().contains(pos)
                           else Qt.CrossCursor)
            return
        kind, (sx, sy), (x, y, w, h) = self.drag
        fx, fy = self._fraction(pos)
        if kind == "move":
            x = min(max(0.0, x + fx - sx), 1 - w)
            y = min(max(0.0, y + fy - sy), 1 - h)
        elif kind == "resize":
            w, h = fx - x, fy - y
        else:
            x, y, w, h = min(sx, fx), min(sy, fy), abs(fx - sx), abs(fy - sy)
        self.box = hidechat.clamp((x, y, w, h))
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event):
        self.drag = None


class HideChatDialog(QDialog):
    def __init__(self, frame: QPixmap, box: hidechat.Box, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE}: hide chat")
        self.picker = BoxPicker(frame, box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Make copy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drag a box over your chat. A copy of the clip is made with "
                                "it blacked out."))
        layout.addWidget(self.picker, 1)
        layout.addWidget(buttons)
        self.resize(1100, 700)

    @property
    def box(self) -> hidechat.Box:
        return self.picker.box
