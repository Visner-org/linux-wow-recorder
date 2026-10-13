"""Colours that read on light and dark desktop themes.

Each has a light and a dark value, picked by the window colour of the current palette. The
timeline's kill, death and lust colours were checked as a set for colour-blind separation and
contrast against Breeze's light (#eff0f1) and dark (#202326) windows; the text colours are at
least 4.5:1 against both. Anything neutral (wipes, ticks, hints) uses the palette's own roles.
"""

from PySide6.QtGui import QColor, QGuiApplication, QPalette

_COLORS = {             # (light, dark)
    "kill": ("#2a78d6", "#3987e5"),
    "death": ("#e34948", "#e33b5c"),
    "lust": ("#eda100", "#c98500"),
    "good": ("#176f30", "#4cc26b"),       # text and chip outline: completed, all fine
    "warning": ("#8a5300", "#f0b44c"),    # text: something to look at
    "error": ("#c4312f", "#ff7b72"),      # text: something that stops recording
}


def is_dark(palette: QPalette | None = None) -> bool:
    palette = palette or QGuiApplication.palette()
    return palette.color(QPalette.Window).lightness() < 128


def color(name: str, palette: QPalette | None = None) -> QColor:
    light, dark = _COLORS[name]
    return QColor(dark if is_dark(palette) else light)


def muted(palette: QPalette | None = None) -> QColor:
    """Secondary text and neutral marks (wipes, the run start)."""
    return (palette or QGuiApplication.palette()).color(QPalette.PlaceholderText)


def css(name: str) -> str:
    """For a style sheet: "color: #8a5300;"."""
    return f"color: {color(name).name()};"
