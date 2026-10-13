"""Optional Discord or Fluxer messages through webhooks."""

import json
import logging
import queue
import threading
import time
import urllib.request

from . import APP_TITLE, __version__
from .config import Config

log = logging.getLogger(__name__)


def post(url: str, message: str, label: str = "Webhook") -> bool:
    """Posts one message to one webhook. True if it was accepted."""
    content = f"🎥 **{APP_TITLE}**\n{message}"
    if len(content) > 2000:   # Discord's limit
        content = content[:1990] + "\n…"
    req = urllib.request.Request(
        url, data=json.dumps({"content": content}).encode(), method="POST",
        # Discord refuses urllib's default User-Agent.
        headers={"Content-Type": "application/json",
                 "User-Agent": f"linux-wow-recorder/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        log.warning(f"{label} message failed: {e}")   # not the URL: it's a secret
        return False


class Notifier:
    """background: send() queues the message for a thread of its own and returns at once, so
    the service never waits on a slow webhook (10 s each, up to MAX_WEBHOOKS of them). Messages
    still go out in order."""

    def __init__(self, cfg: Config, background: bool = False):
        self.cfg = cfg
        self._queue: queue.Queue[str] | None = queue.Queue() if background else None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()   # the service sends from more than one thread

    def enabled(self, event: str) -> bool:
        return bool(self.cfg.webhook_urls) and self.cfg.notify.get(event, False)

    def send(self, event: str, message: str, force: bool = False) -> list[int]:
        """Posts message to every webhook if event is turned on (or force).

        Returns the numbers (from 1) of the webhooks that failed; empty when all worked. In the
        background it returns [] at once; failures are logged.
        """
        if not self.cfg.webhook_urls or not (force or self.enabled(event)):
            return []
        if self._queue is None:
            return self._post_all(message)
        self._queue.put(message)
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="webhooks", daemon=True)
                self._thread.start()
        return []

    def _post_all(self, message: str) -> list[int]:
        return [i for i, url in enumerate(self.cfg.webhook_urls, 1)
                if not post(url, message, f"Webhook {i}")]

    def _run(self):
        while True:
            message = self._queue.get()
            try:
                self._post_all(message)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float):
        """Waits up to timeout seconds for queued messages to go out (when the service stops)."""
        deadline = time.monotonic() + timeout
        while self._queue is not None and self._queue.unfinished_tasks \
                and time.monotonic() < deadline:
            time.sleep(0.05)
