"""Installs the app as a systemd user service (no root: it lives in ~/.config and stops at
logout). It runs while the desktop app is open, or from every login if the user wants that."""

import os
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from . import APP_NAME, APP_TITLE

UNIT_NAME = f"{APP_NAME}.service"
# systemd reads user units from $XDG_CONFIG_HOME/systemd/user, like the app's own config.
UNIT_FILE = (Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd" / "user"
             / UNIT_NAME)


def _exec_start() -> str:
    exe = shutil.which(APP_NAME)
    args = [exe, "run"] if exe else [sys.executable, "-m", "linux_wow_recorder", "run"]
    return " ".join(f'"{a}"' if " " in a else a for a in args)


def unit_text() -> str:
    return f"""\
[Unit]
Description={APP_TITLE}
After=graphical-session.target

[Service]
Type=simple
ExecStart={_exec_start()}
# Also covers the output folder's drive not being mounted yet at login.
Restart=on-failure
RestartSec=10
# Enough for the recorder to save the file it's writing, and the rest of Daemon.run's stop.
TimeoutStopSec=60
# The recorder needs the display and the session's D-Bus (for Wayland's portal).
PassEnvironment=DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_CURRENT_DESKTOP

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install(at_login: bool = True, restart: bool = True):
    """Writes the unit, starts it at every login or not, and (re)starts it now."""
    UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIT_FILE.write_text(unit_text())
    _systemctl("daemon-reload")
    # restart, not start: if it's already running, it picks up the new config or version.
    for action in ("enable" if at_login else "disable", *(["restart"] if restart else [])):
        result = _systemctl(action, UNIT_NAME)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())


def refresh_unit() -> bool:
    """Points the unit at this copy of the program if the one it starts is gone (the app was
    installed another way since: uv, then a package), so the service doesn't fail to start.
    True if it was rewritten."""
    try:
        text = UNIT_FILE.read_text()
    except OSError:
        return False
    found = re.search(r'^ExecStart=(?:"([^"]+)"|(\S+))', text, re.MULTILINE)
    if not found or Path(found.group(1) or found.group(2)).exists():
        return False
    UNIT_FILE.write_text(unit_text())
    _systemctl("daemon-reload")
    return True


def at_login() -> bool:
    """Whether it starts at every login (enabled), without running systemctl: `systemctl
    enable` links the unit into default.target.wants."""
    return (UNIT_FILE.parent / "default.target.wants" / UNIT_NAME).exists()


def start():
    """Starts it now, for this session only (the desktop app opening)."""
    result = _systemctl("start", UNIT_NAME)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())


def uninstall():
    _systemctl("disable", "--now", UNIT_NAME)
    UNIT_FILE.unlink(missing_ok=True)
    _systemctl("daemon-reload")


def state() -> str:
    """"active", "inactive", "failed", ... or "not installed"."""
    if not UNIT_FILE.exists():
        return "not installed"
    return _systemctl("is-active", UNIT_NAME).stdout.strip() or "unknown"


# ── Updates ──

# How often the service and the desktop app look for a newer version installed under them.
UPDATE_CHECK_SECONDS = 60


def installed_version() -> str | None:
    """The version installed on disk now (its package metadata), which an upgrade changes under
    a running program. None when it can't be told (running from a source folder)."""
    try:
        return metadata.version(APP_NAME)
    except metadata.PackageNotFoundError:
        return None


class UpdateWatch:
    """Notices a different version installed under the running program (an RPM, Arch package, .deb
    or `uv tool upgrade`), so it can restart into it when it's safe. Compared with what was
    installed when it started, not with __version__: a source checkout's metadata can lag
    behind its code. A version counts once two checks in a row see it, so a package that's
    still being installed isn't caught halfway."""

    def __init__(self):
        self.running = installed_version()
        self._seen: str | None = None

    def check(self) -> str | None:
        """The newly installed version, once it's settled; None otherwise."""
        found = installed_version()
        if not found or not self.running or found == self.running:
            self._seen = None
            return None
        if found != self._seen:
            self._seen = found
            return None
        return found
