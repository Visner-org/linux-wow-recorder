"""The daemon's live status, as a small JSON file other programs (the tray app, `status`) read.

The file is rewritten whole on every change, so readers never see half of it. It lives in
$XDG_RUNTIME_DIR, which is emptied at logout, so a stale one never outlives the session; a
reader still checks that `pid` is alive, in case the daemon was killed.
"""

import json
import os
import threading
import time
from pathlib import Path

from . import APP_NAME
from .config import STATE_DIR

_runtime = os.environ.get("XDG_RUNTIME_DIR")
STATUS_FILE = (Path(_runtime) / APP_NAME if _runtime else STATE_DIR) / "status.json"
# Requests to the service from the desktop app or the CLI, beside the status file. The service
# reads it every poll and acts; nothing else does anything about it.
CONTROL_FILE = STATUS_FILE.with_name("control.json")

# What the daemon is doing.
WAITING = "waiting"         # watching for WoW
RECORDING = "recording"
NOT_RECORDING = "not_recording"   # WoW is running but the recorder failed or the disk is full
PROCESSING = "processing"   # cutting clips after a session
PAUSED = "paused"           # WoW is running; the user paused recording for this session
STOPPED = "stopped"         # the daemon exited


class Status:
    """The status file's contents, written on every change through update()."""

    def __init__(self, path: Path = STATUS_FILE):
        self.path = path
        self.data = {
            "pid": os.getpid(),
            "state": WAITING,
            "flavor": None,             # "Retail", "Classic", ... while WoW runs
            "session_start": None,      # Unix time WoW was found
            "recording_file": None,
            "free_gb": None,
            "problem": None,            # a short sentence while something is wrong
            "last_session": None,       # {"end", "clips": [paths], "problems": [sentences]}
            "updated": None,
        }
        self._lock = threading.Lock()   # the service updates it from more than one thread

    def update(self, **changes):
        with self._lock:
            self.data.update(changes)
            self.data["updated"] = time.time()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(self.data, indent=2))
                tmp.replace(self.path)
            except OSError:
                pass   # the status file is a convenience; recording must go on without it


def read(path: Path = STATUS_FILE) -> dict | None:
    """The daemon's status, or None if no daemon is running."""
    try:
        data = json.loads(path.read_text())
        os.kill(int(data["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data if data.get("state") != STOPPED else None


def request_pause(pause: bool, path: Path | None = None, status_path: Path | None = None) -> str | None:
    """Asks the service to stop recording for the rest of this WoW session (pause) or to record
    again. It applies to the session running now only: the next time WoW starts, it records.
    Returns why it can't, or None."""
    path, status = path or CONTROL_FILE, read(status_path or STATUS_FILE)
    if status is None:
        return "The recorder service isn't running."
    if not status.get("session_start"):
        return "WoW isn't running."
    data = {"pause": pause, "session": status["session_start"], "asked": time.time()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError as e:
        return f"Couldn't ask the service: {e}"
    return None


def pause_requested(session_start: float, path: Path | None = None) -> bool:
    """Whether recording is paused for the session that started at session_start."""
    try:
        data = json.loads((path or CONTROL_FILE).read_text())
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("pause") and data.get("session") == session_start)


def clear_requests(path: Path | None = None):
    (path or CONTROL_FILE).unlink(missing_ok=True)
