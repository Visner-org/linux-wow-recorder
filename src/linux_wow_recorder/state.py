"""The daemon's live status, as a small JSON file other programs (the tray app, `status`) read.

The file is rewritten whole on every change, so readers never see half of it. It lives in
$XDG_RUNTIME_DIR, which is emptied at logout, so a stale one never outlives the session; a
reader still checks that `pid` is alive, in case the daemon was killed.
"""

import json
import os
import time
from pathlib import Path

from . import APP_NAME
from .config import STATE_DIR

_runtime = os.environ.get("XDG_RUNTIME_DIR")
STATUS_FILE = (Path(_runtime) / APP_NAME if _runtime else STATE_DIR) / "status.json"

# What the daemon is doing.
WAITING = "waiting"         # watching for WoW
RECORDING = "recording"
NOT_RECORDING = "not_recording"   # WoW is running but the recorder failed or the disk is full
PROCESSING = "processing"   # cutting clips after a session
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

    def update(self, **changes):
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
