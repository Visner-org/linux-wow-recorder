"""Installs the app as a systemd user service, so it runs whenever the user is logged in."""

import shutil
import subprocess
import sys
from pathlib import Path

from . import APP_NAME, APP_TITLE

UNIT_NAME = f"{APP_NAME}.service"
UNIT_FILE = Path("~/.config/systemd/user").expanduser() / UNIT_NAME


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
# Enough for the recorder to save the file it's writing.
TimeoutStopSec=45
# The recorder needs the display and the session's D-Bus (for Wayland's portal).
PassEnvironment=DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_CURRENT_DESKTOP

[Install]
WantedBy=default.target
"""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install():
    UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNIT_FILE.write_text(unit_text())
    _systemctl("daemon-reload")
    # restart, not start: if it's already running, it picks up the new config or version.
    for action in ("enable", "restart"):
        result = _systemctl(action, UNIT_NAME)
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
