"""The .desktop files that list the app in the menu and start it in the tray at login.

An RPM install puts the menu entry and icon in /usr/share; otherwise (uv, pipx) they're written
to ~/.local/share the first time autostart is turned on.
"""

import os
import shutil
import sys
from pathlib import Path

from .. import APP_NAME, APP_TITLE

_config = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
_data = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser()
DESKTOP_NAME = f"{APP_NAME}-gui.desktop"   # the app's desktop file name in tray.run must match
AUTOSTART_FILE = _config / "autostart" / DESKTOP_NAME
MENU_FILE = _data / "applications" / DESKTOP_NAME
ICON_FILE = _data / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_NAME}.svg"
SYSTEM_MENU_FILE = Path("/usr/share/applications") / DESKTOP_NAME


def _exec(*extra: str) -> str:
    exe = shutil.which(f"{APP_NAME}-gui")
    args = ([exe] if exe else [sys.executable, "-m", "linux_wow_recorder.gui"]) + list(extra)
    return " ".join(f'"{a}"' if " " in a else a for a in args)


def desktop_entry(autostart: bool) -> str:
    # At login only the tray icon; from the menu the window opens.
    extra = "X-GNOME-Autostart-enabled=true\n" if autostart else ""
    return f"""\
[Desktop Entry]
Type=Application
Name={APP_TITLE}
Comment=Records World of Warcraft and cuts one clip per dungeon run
Exec={_exec("--tray") if autostart else _exec()}
Icon={APP_NAME}
Categories=AudioVideo;Recorder;Game;
StartupWMClass={APP_NAME}
StartupNotify=false
{extra}"""


def install_autostart() -> Path:
    """Starts the app in the tray at login, and lists it in the app menu so it can be reopened."""
    AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTOSTART_FILE.write_text(desktop_entry(autostart=True))
    install_menu_entry()
    return AUTOSTART_FILE


def install_menu_entry():
    """Lists the app in the app menu, unless a package already does (the RPM, Arch)."""
    if not SYSTEM_MENU_FILE.exists():
        MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
        MENU_FILE.write_text(desktop_entry(autostart=False))
        ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).with_name("icon.svg"), ICON_FILE)


def remove_autostart():
    AUTOSTART_FILE.unlink(missing_ok=True)


def autostart_enabled() -> bool:
    return AUTOSTART_FILE.exists()
