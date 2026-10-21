"""Log files, kept on this computer only.

Rules (see docs/development.md, "Logging"):
- Nothing in this module, or anything that reads these files, uses the network. Logs never leave
  the computer; webhook messages never carry log text, error output or file paths.
- No uploading and no crash reporting. `write_report` makes a plain text file in a folder the user
  picks; sharing it is up to them.
- Lines are cleaned before they're written: webhook URLs, the home folder, the computer's name,
  the user name and IP addresses are replaced. gpu-screen-recorder's own output (sessions/) is
  saved as it is and cleaned when it goes into a report.
- Size is capped: each log rotates at 1 MB (4 old copies), 10 session logs are kept, and files
  untouched for 30 days are deleted.
"""

import faulthandler
import getpass
import logging
import logging.handlers
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, APP_TITLE, __version__
from .config import CONFIG_FILE, STATE_DIR

LOG_DIR = STATE_DIR / "logs"
SESSIONS_DIR = LOG_DIR / "sessions"
CRASH_LOG = LOG_DIR / "crash.log"
MAX_BYTES = 1_000_000
BACKUPS = 4
KEEP_SESSIONS = 10
MAX_AGE_DAYS = 30
FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

_WEBHOOK = re.compile(r"https?://[^\s\"'<>]*/api/(?:v\d+/)?webhooks/[^\s\"'<>]+", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
# Not part of a longer dotted number or name (a version like 1.2.3.4.5, libfoo-1.2.3.4).
_IPV4 = re.compile(r"(?<![\w.-])(?!127\.0\.0\.1\b)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}"
                   r"(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\w-]|\.\d)")
_LOG_LINE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[[A-Z]+\] ")   # FORMAT's
_crash_file = None   # kept open for faulthandler


def _identity() -> list[tuple[re.Pattern, str]]:
    """Patterns for what identifies this computer and user, longest first."""
    found = []
    home = str(Path.home())
    if len(home) > 1:
        found.append((re.compile(re.escape(home) + r"(?=/|\b|$)"), "~"))
    for value, label in ((socket.gethostname(), "<computer>"), (_user(), "<user>")):
        if value and len(value) >= 3 and value.lower() not in ("localhost", "root", "user"):
            found.append((re.compile(r"(?<![\w.-])" + re.escape(value) + r"(?![\w-])",
                                     re.IGNORECASE), label))
    return found


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


_IDENTITY = _identity()


def clean(text: str, webhooks: list[str] = ()) -> str:
    """text with webhook URLs, the home folder, the computer's name, the user name and IP
    addresses replaced."""
    for i, url in enumerate(webhooks, 1):
        if url:
            text = text.replace(url, f"webhook {i} (...{url.rstrip('/')[-4:]})")
    text = _WEBHOOK.sub("<webhook URL>", text)
    for pattern, label in _IDENTITY:
        text = pattern.sub(label, text)
    return _IPV4.sub("<IP>", text)


class _CleanFilter(logging.Filter):
    def __init__(self, webhooks: list[str]):
        super().__init__()
        self.webhooks = list(webhooks)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = clean(record.getMessage(), self.webhooks)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = clean(logging.Formatter().formatException(record.exc_info),
                                    self.webhooks)
        return True


def setup(name: str, detailed: bool = False, webhooks: list[str] = (),
          to_stderr: bool = True) -> Path:
    """Sends logging to LOG_DIR/<name>.log (and stderr, which the service's journal keeps; also
    local). Catches uncaught errors in any thread, and hard crashes into crash.log."""
    global _crash_file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    prune()
    path = LOG_DIR / f"{name}.log"
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if detailed else logging.INFO)
    cleaner = _CleanFilter(webhooks)
    formatter = logging.Formatter(FORMAT, DATEFMT)
    handlers = [logging.handlers.RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                                     encoding="utf-8")]
    if to_stderr:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.addFilter(cleaner)
        h.setFormatter(formatter)
        root.addHandler(h)

    def uncaught(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            return sys.__excepthook__(exc_type, exc, tb)
        logging.getLogger(APP_NAME).critical("Unexpected error", exc_info=(exc_type, exc, tb))

    sys.excepthook = uncaught
    threading.excepthook = lambda a: uncaught(a.exc_type, a.exc_value, a.exc_traceback)
    if _crash_file is None:
        try:
            _crash_file = open(CRASH_LOG, "a", encoding="utf-8")
            faulthandler.enable(_crash_file)
        except OSError:
            pass
    logging.getLogger(APP_NAME).debug(f"{APP_TITLE} {__version__}, logging to {path}")
    return path


def prune(now: float | None = None):
    """Deletes log files untouched for MAX_AGE_DAYS, and session logs beyond KEEP_SESSIONS."""
    now = now or time.time()
    for f in LOG_DIR.rglob("*.log*") if LOG_DIR.exists() else []:
        try:
            if now - f.stat().st_mtime > MAX_AGE_DAYS * 86400:
                f.unlink()
        except OSError:
            pass
    sessions = sorted(SESSIONS_DIR.glob("*.log")) if SESSIONS_DIR.exists() else []
    for f in sessions[:-KEEP_SESSIONS]:
        f.unlink(missing_ok=True)


def new_session_log() -> Path:
    """A new file for gpu-screen-recorder's output: sessions/<date time>.log."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{time.strftime('%Y-%m-%d %H-%M-%S')}.log"
    n = 2
    while path.exists():
        path = SESSIONS_DIR / f"{time.strftime('%Y-%m-%d %H-%M-%S')} ({n}).log"
        n += 1
    path.touch()
    sessions = sorted(SESSIONS_DIR.glob("*.log"))
    for f in sessions[:-KEEP_SESSIONS]:
        f.unlink(missing_ok=True)
    return path


def is_log_line(line: str) -> bool:
    """True for a line a log handler wrote (FORMAT), not one a command printed."""
    return bool(_LOG_LINE.match(line))


def tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


# ── Report file ──

def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"({e.__class__.__name__})"


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return platform.system()


def system_summary() -> str:
    """"Nobara Linux 44 (KDE Plasma Desktop Edition), kernel 7.2.9, KDE (wayland)", for the first
    line of each log."""
    parts = [_os_name(), f"kernel {platform.release()}"]
    desktop, session = os.environ.get("XDG_CURRENT_DESKTOP"), os.environ.get("XDG_SESSION_TYPE")
    if desktop:
        parts.append(desktop + (f" ({session})" if session else ""))
    return ", ".join(parts)


def _system(cfg) -> list[str]:
    recorder = cfg.recorder_command if cfg else ["gpu-screen-recorder"]
    lines = [
        f"System     : {_os_name()}, kernel {platform.release()}",
        f"Desktop    : {os.environ.get('XDG_CURRENT_DESKTOP', '?')} "
        f"({os.environ.get('XDG_SESSION_TYPE', '?')})",
        f"Python     : {platform.python_version()}",
    ]
    try:
        from PySide6 import QtCore
        lines.append(f"PySide6    : {getattr(QtCore, '__version__', '?')} (Qt {QtCore.qVersion()})")
    except ImportError:
        lines.append("PySide6    : not installed (no desktop app)")
    if recorder and shutil.which(recorder[0]):
        lines.append(f"Recorder   : {_run([*recorder, '--version'])}")
        info = _run([*recorder, "--info"])
        lines += ["Recorder --info:", *("  " + l for l in info.splitlines()[:30])]
    else:
        lines.append(f"Recorder   : not found ({' '.join(recorder)})")
    for tool in ("ffmpeg", "ffprobe"):
        first = _run([tool, "-version"]).splitlines()[:1] if shutil.which(tool) else ["not found"]
        lines.append(f"{tool:<11}: {first[0] if first else '?'}")
    return lines


def _config_text(path: Path, webhooks: list[str]) -> str:
    """The config file without anything secret or personal: webhook URLs and the after_clips
    command (it can name servers) are replaced."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return "(no config file)"
    out = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key == "webhook_urls":
            out.append(f"webhook_urls = <{len(webhooks)} removed>")
        elif key == "after_clips":
            set_ = bool(line.split("=", 1)[1].strip().strip('"'))
            out.append(f"after_clips = <{'set, removed' if set_ else 'not set'}>")
        else:
            out.append(_URL.sub("<link removed>", line))
    return clean("\n".join(out), webhooks)


def write_report(folder: Path, config_path: Path | None = None) -> Path:
    """Writes linux-wow-recorder-report-<date>.txt into folder and returns its path. Nothing is
    sent anywhere."""
    from . import config, state
    config_path = config_path or CONFIG_FILE
    try:
        cfg = config.load(config_path)
    except Exception:
        cfg = None
    webhooks = cfg.webhook_urls if cfg else []
    status = state.read() or {}
    parts = [
        f"{APP_TITLE} {__version__}: report made {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "This file was made on your computer and has not been sent anywhere. Webhook links,",
        "your home folder, user name, computer name and IP addresses have been replaced.",
        "Read it before you share it.",
        "",
        "== System ==", *_system(cfg),
        "",
        "== Status ==",
        f"State      : {status.get('state', 'service not running')}",
        f"Problem    : {status.get('problem') or '-'}",
        "",
        "== Config ==", _config_text(config_path, webhooks),
    ]
    for title, path, lines in (
        ("Service log (recorder.log)", LOG_DIR / "recorder.log", 400),
        ("Desktop app log (app.log)", LOG_DIR / "app.log", 150),
        ("Commands log (cli.log)", LOG_DIR / "cli.log", 80),
        ("Last recorder output", _latest_session(), 80),
        ("Crashes (crash.log)", CRASH_LOG, 80),
    ):
        text = tail(path, lines) if path else ""
        parts += ["", f"== {title}, last {lines} lines ==", text.strip() or "(empty)"]
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{APP_NAME}-report-{time.strftime('%Y-%m-%d-%H%M')}.txt"
    out.write_text(clean("\n".join(parts), webhooks) + "\n")
    return out


def _latest_session() -> Path | None:
    sessions = sorted(SESSIONS_DIR.glob("*.log")) if SESSIONS_DIR.exists() else []
    return sessions[-1] if sessions else None


def default_report_folder() -> Path:
    """The Desktop if there is one (XDG user dirs), else the home folder."""
    desktop = _run(["xdg-user-dir", "DESKTOP"]) if shutil.which("xdg-user-dir") else ""
    for candidate in (desktop, str(Path.home() / "Desktop")):
        if candidate and Path(candidate).is_dir() and Path(candidate) != Path.home():
            return Path(candidate)
    return Path.home()
