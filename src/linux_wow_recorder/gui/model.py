"""What the tray shows for a given status, kept apart from Qt so it can be tested without one."""

import re
import time
from pathlib import Path
from dataclasses import dataclass, field

from .. import state

# Icon names; tray.py draws one for each.
IDLE, RECORDING, PROCESSING, ATTENTION, OFF = "idle", "recording", "processing", "attention", "off"


@dataclass
class View:
    icon: str
    title: str                   # first menu line, and the tooltip's
    details: list[str] = field(default_factory=list)   # more read-only lines under it
    service_running: bool = False
    recording: bool = False


def menu_text(text: str, limit: int = 80) -> str:
    """A Discord-style message as a plain menu line: no backticks, not too long."""
    text = text.replace("`", "").replace("**", "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def elapsed(seconds: float) -> str:
    h, rest = divmod(int(max(0, seconds)), 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}"


def clock(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def describe(status: dict | None, service_state: str, now: float | None = None) -> View:
    """status: state.read()'s result (None when the service isn't running).
    service_state: service.state() ("active", "inactive", "not installed", ...)."""
    now = time.time() if now is None else now
    if status is None:
        if service_state == "not installed":
            return View(OFF, "Recorder service not installed",
                        ["Open Settings to set it up"])
        if service_state == "failed":
            return View(ATTENTION, "Recorder service failed", ["See Show log for why"])
        if service_state in ("activating", "active"):
            return View(IDLE, "Recorder service starting...", service_running=True)
        return View(OFF, "Recorder service stopped", ["Nothing is being recorded"])

    s = status.get("state")
    since = status.get("session_start")
    flavor = status.get("flavor") or "WoW"
    details = []
    if s == state.RECORDING:
        view = View(RECORDING, f"Recording  {elapsed(now - since) if since else ''}".rstrip(),
                    service_running=True, recording=True)
        if since:
            details.append(f"WoW {flavor} since {clock(since)}")
    elif s == state.NOT_RECORDING:
        view = View(ATTENTION, f"WoW {flavor} running, NOT recording", service_running=True)
    elif s == state.PROCESSING:
        view = View(PROCESSING, "Cutting clips...", service_running=True)
    else:
        view = View(IDLE, "Waiting for WoW", service_running=True)

    if status.get("problem"):
        view.icon = ATTENTION
        details.append(menu_text(status["problem"]))
    if status.get("free_gb") is not None:
        details.append(f"{status['free_gb']:.0f} GB free")
    view.details = details
    return view


def last_session_lines(status: dict | None) -> list[str]:
    last = (status or {}).get("last_session")
    if not last:
        return []
    n = len(last.get("clips", []))
    lines = [f"Last session {clock(last['end'])}: {n} clip{'s' if n != 1 else ''}"]
    lines += [menu_text(f"⚠ {p}") for p in last.get("problems", [])]
    return lines


# ── Settings window ──

QUALITIES = {"": "Recorder default", "medium": "Medium", "high": "High",
             "very_high": "Very high", "ultra": "Ultra"}


# gpu-screen-recorder's "screen" is the first monitor it finds, in its own order (DRM connectors),
# not the desktop's numbering or its main monitor.
SCREEN_LABEL = "Whole screen: the first monitor gpu-screen-recorder finds (may not be your main one)"


def parse_capture_options(output: str) -> list[tuple[str, str]]:
    """(value, label) pairs from `gpu-screen-recorder --list-capture-options`. Its lines are
    monitors ("DP-1|2560x1440"), "portal", "region", "focused", "window" and webcams
    ("/dev/video0|1280x720@30hz|mjpeg", one line per mode). 6.x doesn't list "screen" (the
    first monitor) though -w still takes it, so it's always offered. "portal" (the default)
    comes first when it's listed. "screen" is the first monitor in this list, which needn't be
    the desktop's main monitor or its "monitor 1", so its label names it."""
    found = [("screen", SCREEN_LABEL)]
    monitors = []
    for line in output.splitlines():
        name, _, rest = line.strip().partition("|")
        if name == "portal":
            found.insert(0, (name, "A window or screen you pick (e.g. only WoW)"))
        elif name and not name.startswith("/") and name not in ("screen", "focused", "window", "region"):
            size = rest.partition("|")[0]
            monitors.append(name)
            found.append((name, f"Monitor {name}" + (f" ({size.replace('x', '×')})" if size else "")))
    if monitors:
        i = found.index(("screen", SCREEN_LABEL))
        found[i] = ("screen", f"Whole screen: always {monitors[0]}, the first one gpu-screen-recorder lists")
    return found if len(found) > 1 else []


def capture_monitor(capture: str, options: list[str]) -> str | None:
    """The monitor a capture setting records, from the settings window's option values:
    a monitor name itself, "screen" the first monitor listed, anything else (portal) none."""
    monitors = [o for o in options if o not in ("portal", "screen")]
    if capture == "screen":
        return monitors[0] if monitors else None
    return capture if capture in monitors else None


def combat_log_hint(folder: str, now: float | None = None) -> str:
    """A line under the combat log folder: the newest log in it and how old it is."""
    now = time.time() if now is None else now
    path = Path(folder).expanduser() if folder else None
    if not path or not path.is_dir():
        return "Folder not found"
    logs = sorted(path.glob("**/WoWCombatLog-*.txt"), key=lambda f: f.stat().st_mtime)
    if not logs:
        return "No combat logs in it yet (fine if combat logging has never been on)"
    age = now - logs[-1].stat().st_mtime
    when = (f"{int(age)} s ago" if age < 120 else f"{int(age / 60)} min ago" if age < 7200
            else f"{int(age / 3600)} h ago" if age < 172800 else f"{int(age / 86400)} days ago")
    return f"Newest: {logs[-1].name}, written {when}"


# ── Clip browser ──

# "2026-10-08 19-03 Kings' Rest +12 Completed (24m52s).mp4", for clips without a sidecar
_CLIP_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2}) (.+?) (\+\d+ \w+|\w+) \((\d+m\d+s)\)")
FILTERS = {"all": "All", "completed": "Completed", "abandoned": "Abandoned", "nokey": "No key"}


@dataclass
class ClipInfo:
    path: Path
    started: float                # when the run started (Unix time)
    dungeon: str
    level: int | None
    label: str                    # "+12 Completed", "Heroic", ...
    result: str                   # "completed", "abandoned", "heroic", ...
    length: str                   # "24m52s"
    deaths: int | None = None     # None: no sidecar, so not known
    bosses_killed: int | None = None
    keep: bool = False
    details: dict = field(default_factory=dict)   # the sidecar

    @property
    def filter_key(self) -> str:
        return self.result if self.result in ("completed", "abandoned") else "nokey"


def clip_info(path: Path, details: dict) -> ClipInfo | None:
    """A clip's details from its sidecar, or from its name when it has none."""
    if details.get("dungeon"):
        return ClipInfo(path, details.get("run_start") or path.stat().st_mtime,
                        details["dungeon"], details.get("level"), details.get("label", ""),
                        details.get("result", ""), details.get("run_length", ""),
                        details.get("deaths_total"), details.get("bosses_killed"),
                        bool(details.get("keep")), details)
    m = _CLIP_NAME.match(path.name)
    if not m:
        return None
    day, hh, mm, dungeon, label, length = m.groups()
    started = time.mktime(time.strptime(f"{day} {hh}:{mm}", "%Y-%m-%d %H:%M"))
    level = int(label.split()[0][1:]) if label.startswith("+") else None
    return ClipInfo(path, started, dungeon, level, label, label.split()[-1].lower(), length,
                    keep=bool(details.get("keep")), details=details)


def load_clips(clips_dir: Path, read_sidecar) -> list[ClipInfo]:
    """Every clip in clips_dir, newest first. read_sidecar is clips.read_sidecar."""
    if not clips_dir.is_dir():
        return []
    found = []
    for f in clips_dir.glob("*.mp4"):
        try:
            info = clip_info(f, read_sidecar(f))
        except (OSError, ValueError, TypeError, KeyError):
            info = None
        if info:
            found.append(info)
    return sorted(found, key=lambda c: c.started, reverse=True)


def matches(clip: ClipInfo, filter_key: str, search: str) -> bool:
    if filter_key != "all" and clip.filter_key != filter_key:
        return False
    return search.lower() in clip.dungeon.lower() if search else True


def mmss(seconds: float) -> str:
    m, s = divmod(int(max(0, seconds)), 60)
    return f"{m}:{s:02d}"


@dataclass
class Marker:
    at: float                     # seconds into the clip
    kind: str                     # "kill", "wipe", "pull", "death", "lust", "start"
    text: str


def short_name(name: str) -> str:
    """"Tank-Realm-US" -> "Tank"."""
    return name.split("-")[0] if name else "?"


def markers(details: dict) -> list[Marker]:
    """Everything worth jumping to in a clip, in time order."""
    found = []
    if details.get("run_offset") is not None:
        found.append(Marker(details["run_offset"], "start", "Run starts"))
    for e in details.get("encounters", []):
        if e.get("end") is not None:
            found.append(Marker(e["start"], "pull", f"{e['name']} pulled"))
            found.append(Marker(e["end"], "kill" if e.get("killed") else "wipe",
                                f"{e['name']} {'killed' if e.get('killed') else 'wipe'}"))
        else:
            found.append(Marker(e["start"], "pull", f"{e['name']} pulled"))
    for d in details.get("deaths", []):
        found.append(Marker(d["at"], "death", f"{short_name(d['name'])} died"))
    for l in details.get("lusts", []):
        who = f" ({short_name(l['caster'])})" if l.get("caster") else ""
        found.append(Marker(l["at"], "lust", f"{l['name']}{who}"))
    return sorted(found, key=lambda m: m.at)
