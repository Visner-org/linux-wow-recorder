"""A big red "THIS ONE" over a monitor for a few seconds, so picking DP-1 or DP-2 in the
settings window shows which physical screen that is. Clicks go through it."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

SHOW_MS = 2500
RED = QColor("#e00000")
BORDER = 24


class MonitorFlash(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        font = QFont()
        font.setPointSize(140)
        font.setBold(True)
        self.setFont(font)
        self.timer = QTimer(self, singleShot=True, interval=SHOW_MS, timeout=self.hide)

    def flash(self, monitor: str) -> bool:
        """Shows the text on the monitor Qt calls `monitor` (e.g. "DP-2"). False if there's none."""
        screen = next((s for s in QGuiApplication.screens() if s.name() == monitor), None)
        if screen is None:
            return False
        self.hide()   # a window moves to another output only when it's shown again
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.showFullScreen()
        self.timer.start()
        return True

    def paintEvent(self, event):
        # drawn by hand: a stylesheet border isn't painted on a translucent window
        p = QPainter(self)
        p.setPen(QPen(RED, BORDER))
        p.drawRect(self.rect().adjusted(BORDER // 2, BORDER // 2, -BORDER // 2, -BORDER // 2))
        p.setPen(RED)
        p.drawText(self.rect(), Qt.AlignCenter, "THIS ONE")
