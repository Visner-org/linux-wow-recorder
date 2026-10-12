"""Optional Discord or Fluxer messages through webhooks."""

import json
import logging
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
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        log.warning(f"{label} message failed: {e}")   # not the URL: it's a secret
        return False


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def enabled(self, event: str) -> bool:
        return bool(self.cfg.webhook_urls) and self.cfg.notify.get(event, False)

    def send(self, event: str, message: str, force: bool = False) -> list[int]:
        """Posts message to every webhook if event is turned on (or force).

        Returns the numbers (from 1) of the webhooks that failed; empty when all worked.
        """
        if not self.cfg.webhook_urls or not (force or self.enabled(event)):
            return []
        return [i for i, url in enumerate(self.cfg.webhook_urls, 1)
                if not post(url, message, f"Webhook {i}")]
