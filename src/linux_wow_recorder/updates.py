"""Checks GitHub for a newer release, when the user turned it on ([updates] check) or pressed
"Check now". It only tells: downloading and installing is up to the user.

The request is a plain GET of the latest release's details from GitHub's API. It carries no
version, settings, paths or anything else from this computer (GitHub asks for a User-Agent; it's
the app's name). GitHub sees the IP address, as with any web page.
"""

import json
import re
import threading
import urllib.request

from . import APP_NAME, REPO_URL

LATEST_API = "https://api.github.com/repos/Visner-org/linux-wow-recorder/releases/latest"
RELEASES_PAGE = f"{REPO_URL}/releases/latest"
CHECK_EVERY = 24 * 3600   # seconds between automatic checks


def version_tuple(version: str) -> tuple[int, ...] | None:
    """(0, 6, 0) from "0.6.0" or "v0.6.0"; None if it isn't a plain release number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(part) for part in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str) -> bool:
    new, now = version_tuple(latest), version_tuple(current)
    return bool(new and now and new > now)


def latest_version(timeout: float = 15) -> str | None:
    """The newest release's version on GitHub ("0.7.0"), or None if GitHub couldn't be asked
    or answered something unexpected. Drafts and pre-releases aren't "latest" there."""
    request = urllib.request.Request(LATEST_API, headers={
        "User-Agent": APP_NAME, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError):
        return None
    tag = data.get("tag_name") if isinstance(data, dict) else None
    return tag.lstrip("v") if isinstance(tag, str) and version_tuple(tag) else None


class Check:
    """latest_version() in a thread of its own, so a window never waits on the network. done
    turns true when it has an answer (version: None if GitHub couldn't be asked)."""

    def __init__(self):
        self.done = False
        self.version: str | None = None
        threading.Thread(target=self._run, name="update-check", daemon=True).start()

    def _run(self):
        self.version = latest_version()
        self.done = True
