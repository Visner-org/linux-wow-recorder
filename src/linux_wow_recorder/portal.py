"""Checks the desktop's screen-share service, which portal capture records through.

xdg-desktop-portal picks its desktop backend once, when it starts. Started before the desktop
session is up (logging out and back in, without a restart), it can come up with no ScreenCast,
and gpu-screen-recorder then exits right away. Restarting it once the desktop is up fixes that.
"""

import logging
import subprocess
import time

log = logging.getLogger(__name__)

PORTAL_UNIT = "xdg-desktop-portal.service"
# How long restart() waits for ScreenCast to show up again.
RESTART_WAIT = 10


def screencast_ok() -> bool | None:
    """True if the screen-share service offers ScreenCast, False if it doesn't, None if it
    couldn't be asked (no busctl)."""
    try:
        result = subprocess.run(
            ["busctl", "--user", "get-property", "org.freedesktop.portal.Desktop",
             "/org/freedesktop/portal/desktop", "org.freedesktop.portal.ScreenCast", "version"],
            capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return False
    if result.returncode != 0:
        log.debug(f"ScreenCast check: {result.stderr.strip()}")
    return result.returncode == 0


def restart() -> bool:
    """Restarts the screen-share service. True if ScreenCast works afterwards."""
    try:
        subprocess.run(["systemctl", "--user", "restart", PORTAL_UNIT],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"Couldn't restart {PORTAL_UNIT}: {e}")
        return False
    deadline = time.time() + RESTART_WAIT
    while True:
        if screencast_ok():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)
