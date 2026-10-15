"""What the tray shows for a given status, kept apart from Qt so it can be tested without one."""

import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import keytimers, state

# Icon names; tray.py draws one for each.
IDLE, RECORDING, PROCESSING, ATTENTION, OFF = "idle", "recording", "processing", "attention", "off"


@dataclass
class View:
    icon: str
    title: str                   # first menu line, and the tooltip's
    details: list[str] = field(default_factory=list)   # more read-only lines under it
    service_running: bool = False
    recording: bool = False
    paused: bool = False         # WoW is running and the user paused recording
    # "Record monitor instead" makes sense: a window's picked (or awaited), or it was switched
    can_switch: bool = False
    monitor_switch: bool = False  # switched to the monitor this session
    can_repick: bool = False      # recording a window picked in the dialog: pick another


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
    elif s == state.PAUSED:
        view = View(IDLE, "Paused for this session", service_running=True, paused=True)
        details.append(f"WoW {flavor} running since {clock(since)}; the next session records"
                       if since else "The next session records")
    elif s == state.NOT_RECORDING:
        view = View(ATTENTION, f"WoW {flavor} running, NOT recording", service_running=True)
    elif s == state.PROCESSING:
        view = View(PROCESSING, "Cutting clips...", service_running=True)
    else:
        view = View(IDLE, "Waiting for WoW", service_running=True)

    view.monitor_switch = bool(status.get("monitor_switch"))
    view.can_switch = bool(since) and s in (state.RECORDING, state.NOT_RECORDING) and (
        status.get("capture") == "portal" or view.monitor_switch)
    view.can_repick = bool(since) and s == state.RECORDING and status.get("capture") == "portal"
    if status.get("problem"):
        view.icon = ATTENTION
        details.append(menu_text(status["problem"]))
    if status.get("free_gb") is not None:
        details.append(f"{status['free_gb']:.0f} GB free")
    view.details = details
    return view


# Opens the screen-share dialog again (tray menu, clips window).
REPICK_TEXT = "Pick another window"


def switch_text(monitor_switch: bool) -> str:
    """The "Record monitor instead" button's text (tray menu, clips window)."""
    return "Record the window again" if monitor_switch else "Record monitor instead"


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


# How far combat_log_hint looks: the Logs folder and a few levels of subfolders (Raider.IO's
# archive), not a whole home folder typed by mistake.
HINT_MAX_DEPTH = 3
HINT_MAX_FOLDERS = 300


def newest_combat_log(folder: Path, max_depth: int = HINT_MAX_DEPTH,
                      max_folders: int = HINT_MAX_FOLDERS) -> tuple[tuple[float, str] | None, bool]:
    """((mtime, name) of the newest combat log under folder, or None), and whether the search
    stopped early because the folder has too many subfolders."""
    newest, seen, todo = None, 0, [(folder, 0)]
    while todo:
        if seen >= max_folders:
            return newest, True
        path, depth = todo.pop()
        seen += 1
        try:
            entries = list(os.scandir(path))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < max_depth:
                        todo.append((e.path, depth + 1))
                elif e.name.startswith("WoWCombatLog-") and e.name.endswith(".txt"):
                    mtime = e.stat().st_mtime
                    if newest is None or mtime > newest[0]:
                        newest = (mtime, e.name)
            except OSError:
                continue
    return newest, False


def combat_log_hint(folder: str, now: float | None = None) -> str:
    """A line under the combat log folder: the newest log in it and how old it is."""
    now = time.time() if now is None else now
    path = Path(folder).expanduser() if folder else None
    if not path or not path.is_dir():
        return "Folder not found"
    newest, stopped = newest_combat_log(path)
    if not newest:
        if stopped:
            return "No combat logs near the top of this folder: is it WoW's Logs folder?"
        return "No combat logs in it yet (fine if combat logging has never been on)"
    age = now - newest[0]
    when = (f"{int(age)} s ago" if age < 120 else f"{int(age / 60)} min ago" if age < 7200
            else f"{int(age / 3600)} h ago" if age < 172800 else f"{int(age / 86400)} days ago")
    return f"Newest: {newest[1]}, written {when}"


# ── Clip browser ──

# "2026-10-08 19-03 Kings' Rest +12 Completed (24m52s).mp4", for clips without a sidecar
_CLIP_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2}) (.+?) (\+\d+ \w+|\w+) \((\d+m\d+s)\)")
# The same name with its spaces made hyphens, as some programs rename what they're given
# ("2026-10-08-19-03-Kings'-Rest-+12-Completed-(24m52s).mp4").
_HYPHENATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(.+?)-(\+\d+-\w+|\w+)-\((\d+m\d+s)\)")
FILTERS = {"all": "All", "completed": "Completed", "abandoned": "Abandoned"}
RAID_FILTERS = {"all": "All", "kill": "Kills", "wipe": "Wipes"}   # as many buttons as FILTERS
# What the clip browser lists: a clip per dungeon run, a clip per raid boss pull, or the full
# recordings they were cut from.
KEYS, RAIDS, SESSIONS = "keys", "raids", "sessions"


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
    chip: str = ""                # the result chip's text, when it isn't the result ("3 runs")
    details_file: Path | None = None   # where details came from, when it isn't beside the video
    elsewhere: bool = False       # in one of the other folders (Settings: other_folders)

    @property
    def filter_key(self) -> str:
        return self.result if self.result in ("completed", "abandoned", "kill", "wipe") else "nokey"

    @property
    def chat_hidden(self) -> bool:
        """A copy made with Hide chat."""
        return bool(self.details.get("chat_hidden"))

    @property
    def is_raid(self) -> bool:
        """A raid boss pull's clip; its dungeon is the boss."""
        return self.details.get("kind") == "raid"

    @property
    def raid(self) -> str:
        return self.details.get("raid") or ""

    @property
    def pull(self) -> int | None:
        return self.details.get("pull")

    @property
    def timer(self) -> float | None:
        """A completed key's in-game time, s: from its details, or the length in its name (a
        completed key's is the in-game time) for clips cut without one."""
        if self.result != "completed":
            return None
        if self.details.get("timer"):
            return self.details["timer"]
        m = re.fullmatch(r"(\d+)m(\d{2})s", self.length or "")
        return int(m[1]) * 60 + int(m[2]) if m else None

    @property
    def time_limit(self) -> int | None:
        """A completed key's dungeon's time limit, s: from its details, or the table for clips
        cut before they had it. None if not known."""
        if not self.timer:
            return None
        return self.details.get("time_limit") or keytimers.time_limit(dungeon=self.dungeon)

    @property
    def upgrade(self) -> int | None:
        """A completed key's keystone levels gained (0: over time), or None if not known."""
        limit = self.time_limit
        return keytimers.upgrade(self.timer, limit) if limit else None

    @property
    def status(self) -> str:
        """The result, with "overtime" for a key completed over time (for its colour)."""
        return "overtime" if self.upgrade == 0 else self.result

    @property
    def result_text(self) -> str:
        """The result for people: "Timed +2", "Over time", "Heroic", "LFR Kill", ..."""
        if self.is_raid and not self.chip:
            return self.label
        if not self.chip and self.upgrade is not None:
            return f"Timed +{self.upgrade}" if self.upgrade else "Over time"
        return self.chip or self.result.capitalize()

    @property
    def time_left(self) -> str:
        """A completed key's time to spare, "6m48s left", or "4m31s over"; "" if not known."""
        limit = self.time_limit
        if not limit:
            return ""
        spare = limit - self.timer
        m, s = divmod(int(abs(spare)), 60)
        return f"{m}m{s:02d}s {'left' if spare >= 0 else 'over'}"

    @property
    def is_session(self) -> bool:
        return self.result in ("session", "uncut")


def clip_info(path: Path, details: dict) -> ClipInfo | None:
    """A clip's details from its sidecar, or from its name when it has none."""
    if details.get("dungeon"):
        return ClipInfo(path, details.get("run_start") or path.stat().st_mtime,
                        details["dungeon"], details.get("level"), details.get("label", ""),
                        details.get("result", ""), details.get("run_length", ""),
                        details.get("deaths_total"), details.get("bosses_killed"),
                        bool(details.get("keep")), details)
    m = _CLIP_NAME.match(path.name)
    hyphenated = None if m else _HYPHENATED_NAME.match(path.name)
    if not (m or hyphenated):
        return None
    # A clip cut before sidecars existed, or moved without them: its name is all there is.
    day, hh, mm, dungeon, label, length = (m or hyphenated).groups()
    if hyphenated:
        dungeon, label = dungeon.replace("-", " "), label.replace("-", " ")
    started = time.mktime(time.strptime(f"{day} {hh}:{mm}", "%Y-%m-%d %H:%M"))
    level_text, _, result = label.rpartition(" ")
    level = int(level_text[1:]) if level_text.startswith("+") else None
    return ClipInfo(path, started, dungeon, level, label, result.lower(), length,
                    keep=bool(details.get("keep")), details=details)


def folder_to_watch(clips_dir: Path) -> Path | None:
    """The folder the clip browser watches for new clips: the clips folder, or before the
    first clip, the nearest folder above it that exists (the output folder or its parent), so
    the clips folder showing up is noticed."""
    for folder in (clips_dir, *clips_dir.parents[:2]):
        if folder.is_dir():
            return folder
    return None


def clip_key(name: str) -> str:
    """A clip's name reduced to its letters and digits, so the same clip matches after it's been
    renamed with hyphens for spaces: "2026-10-08 19-03 Kings' Rest +12 Completed (24m52s)"."""
    return re.sub(r"[^0-9a-z]", "", Path(name).stem.lower())


def load_clips(clips_dir: Path, read_sidecar, other_dirs: list[Path] = ()) -> list[ClipInfo]:
    """Every clip in clips_dir, and in other_dirs (folders the user moves clips to), newest
    first. A clip moved elsewhere finds its details where it was cut: a .json left in clips_dir
    whose video is gone, matched by clip_key. The same clip in both places is listed once, from
    clips_dir. read_sidecar is clips.read_sidecar."""
    found: dict[str, ClipInfo] = {}

    def add(f: Path, details: dict, **extra):
        try:
            info = clip_info(f, details)
        except (OSError, ValueError, TypeError, KeyError):
            info = None
        if info:
            for k, v in extra.items():
                setattr(info, k, v)
            found.setdefault(clip_key(f.name), info)

    left_behind: dict[str, Path] = {}   # clip_key -> a .json in clips_dir without its video
    if clips_dir.is_dir():
        for f in clips_dir.glob("*.mp4"):
            add(f, _read(read_sidecar, f))
        for f in clips_dir.glob("*.json"):
            if not f.with_suffix(".mp4").exists():
                left_behind[clip_key(f.name)] = f
    for folder in other_dirs:
        if folder == clips_dir or not folder.is_dir():
            continue
        try:
            videos = sorted(folder.glob("*.mp4"))
        except OSError:   # a share that went away
            continue
        for f in videos:
            key = clip_key(f.name)
            if key in found:
                continue
            beside, kept = _read(read_sidecar, f), left_behind.get(key)
            if beside or not kept:
                add(f, beside, elsewhere=True)
            else:
                add(f, _read(read_sidecar, kept), elsewhere=True, details_file=kept)
    return sorted(found.values(), key=lambda c: c.started, reverse=True)


def _read(read_sidecar, path: Path) -> dict:
    try:
        return read_sidecar(path)
    except (OSError, ValueError):
        return {}


def in_mode(clip: ClipInfo, mode: str) -> bool:
    """Whether the clip browser lists the clip in mode: raid pulls under Raids, dungeon runs
    under Keystones."""
    return mode == SESSIONS or clip.is_raid == (mode == RAIDS)


def matches(clip: ClipInfo, filter_key: str, search: str) -> bool:
    if filter_key != "all" and clip.filter_key != filter_key:
        return False
    return search.lower() in f"{clip.dungeon} {clip.raid}".lower() if search else True


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
    for r in details.get("runs", []):   # a full session's
        found.append(Marker(r["run_offset"], "start", f"{r['dungeon']} {r['label']} starts"))
    return sorted(found, key=lambda m: m.at)


# ── The clip's timeline and event list ──

LEAD_IN = 3        # seconds of lead-up when jumping to an event
LUST_SECONDS = 40  # how long a lust's haste lasts: drawn as a bracket that long


@dataclass
class Span:
    start: float
    end: float
    kind: str                     # "kill", "wipe", "pull" (still going at the clip's end), "lust"
    text: str


def boss_spans(details: dict, length: float) -> list[Span]:
    """Each boss pull from its start to its kill or wipe (or the clip's end)."""
    spans = []
    for e in details.get("encounters", []):
        end = e.get("end")
        kind = "pull" if end is None else "kill" if e.get("killed") else "wipe"
        spans.append(Span(e["start"], end if end is not None else length, kind,
                          f"{e['name']}: {'killed' if kind == 'kill' else 'wipe' if kind == 'wipe' else 'pulled'}"))
    return spans


def lust_spans(details: dict, length: float) -> list[Span]:
    return [Span(l["at"], min(length, l["at"] + LUST_SECONDS), "lust", lust_text(l))
            for l in details.get("lusts", [])]


def lust_text(lust: dict) -> str:
    who = f" ({short_name(lust['caster'])})" if lust.get("caster") else ""
    return f"{lust['name']}{who}"


def seek_target(at: float) -> float:
    """Where jumping to an event starts playing: a few seconds before it."""
    return max(0.0, at - LEAD_IN)


@dataclass
class EventRow:
    at: float
    kind: str                     # as Marker.kind
    text: str
    detail: str = ""
    children: list["EventRow"] = field(default_factory=list)


def event_rows(details: dict) -> list[EventRow]:
    """The clip's events for the list beside the video: each boss pull as a row holding the
    deaths and lusts during it and its kill or wipe; everything else on its own, in time order."""
    rows: list[EventRow] = []
    if details.get("run_offset") is not None:
        rows.append(EventRow(details["run_offset"], "start", "Run starts"))
    loose = [EventRow(d["at"], "death", f"{short_name(d['name'])} died")
             for d in details.get("deaths", [])]
    loose += [EventRow(l["at"], "lust", lust_text(l)) for l in details.get("lusts", [])]
    for e in details.get("encounters", []):
        end = e.get("end")
        kind = "pull" if end is None else "kill" if e.get("killed") else "wipe"
        inside = [r for r in loose if e["start"] <= r.at <= (end if end is not None else float("inf"))]
        loose = [r for r in loose if r not in inside]
        deaths = sum(r.kind == "death" for r in inside)
        detail = ("killed in " if kind == "kill" else "wipe after " if kind == "wipe" else "") \
            + (mmss(end - e["start"]) if end is not None else "pulled")
        if deaths:
            detail += f" · {deaths} death{'s' if deaths != 1 else ''}"
        children = sorted(inside, key=lambda r: r.at)
        if end is not None:
            children.append(EventRow(end, kind, "Killed" if kind == "kill" else "Wipe"))
        rows.append(EventRow(e["start"], kind, e["name"], detail, children))
    rows += loose
    return sorted(rows, key=lambda r: r.at)



# ── YouTube ──

YOUTUBE_UPLOAD_URL = "https://www.youtube.com/upload"


def chapter_time(seconds: float) -> str:
    """A YouTube chapter time: "4:05", or "1:02:05" past an hour."""
    h, rest = divmod(int(max(0, seconds)), 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else mmss(rest)


def youtube_chapters(details: dict) -> list[tuple[float, str]]:
    """The clip's chapters: the start, the run starting and each boss pull, or [] when that's
    too few for YouTube (it wants 3 or more, starting at 0:00, each 10 s or longer)."""
    marks = [(0.0, "Start")]
    if details.get("run_offset"):
        marks.append((details["run_offset"], "Run starts"))
    pulls: dict[str, int] = {}
    for e in sorted(details.get("encounters", []), key=lambda e: e["start"]):
        pulls[e["name"]] = pulls.get(e["name"], 0) + 1
        result = "" if e.get("end") is None else " (kill)" if e.get("killed") else " (wipe)"
        again = f" pull {pulls[e['name']]}" if pulls[e["name"]] > 1 else ""
        marks.append((e["start"], f"{e['name']}{again}{result}"))
    chapters = []
    for at, text in marks:
        if not chapters or at - chapters[-1][0] >= 10:
            chapters.append((at, text))
    return chapters if len(chapters) >= 3 else []


def youtube_description(clip: ClipInfo) -> str:
    """A description to paste into YouTube's upload page (which takes the title from the file
    name): the run in a sentence, then its chapters."""
    if clip.is_raid:
        about = f"{clip.raid}: {clip.dungeon}, {clip.label}, pull {clip.pull}, {clip.length}"
    else:
        about = f"{clip.dungeon} {clip.label}, {clip.length}"
    facts = [about, time.strftime("%Y-%m-%d", time.localtime(clip.started))]
    if clip.deaths is not None:
        facts.append(f"{clip.deaths} death{'s' if clip.deaths != 1 else ''}")
    lines = [", ".join(facts) + "."]
    chapters = youtube_chapters(clip.details)
    if chapters:
        lines += ["", *(f"{chapter_time(at)} {text}" for at, text in chapters)]
    # YouTube refuses descriptions with angle brackets.
    return "\n".join(lines).replace("<", "").replace(">", "") + "\n"


# ── The settings window's checks ──

OK, INFO, WARNING, ERROR = "ok", "info", "warning", "error"


@dataclass
class Check:
    level: str                    # OK, INFO, WARNING or ERROR
    text: str


def field_checks(data: dict, free_gb=None) -> dict[str, Check]:
    """A line under each settings field saying what's wrong with it, or what it found. data is
    shaped like config.DEFAULTS; free_gb(path) is shutil.disk_usage's free space (for tests)."""
    if free_gb is None:
        def free_gb(path):
            return shutil.disk_usage(path).free / 1e9
    checks: dict[str, Check] = {}
    p, r, s = data["paths"], data["recorder"], data["storage"]

    folder = p["combat_log_dir"].strip()
    if not folder:
        checks["combat_log_dir"] = Check(ERROR, "Needed: the Logs folder next to WoW's _retail_ folder")
    else:
        hint = combat_log_hint(folder)
        checks["combat_log_dir"] = Check(ERROR if hint == "Folder not found" else
                                         OK if hint.startswith("Newest") else WARNING, hint)

    out = p["output_dir"].strip()
    if not out:
        checks["output_dir"] = Check(ERROR, "Needed: where recordings and clips go")
    else:
        path = Path(out).expanduser()
        existing = next((f for f in (path, *path.parents) if f.exists()), None)
        if existing is None or not existing.is_dir():
            checks["output_dir"] = Check(ERROR, "Not a folder")
        elif not os.access(existing, os.W_OK):
            checks["output_dir"] = Check(ERROR, f"Can't write in {existing}")
        else:
            try:
                free = f"{free_gb(existing):.0f} GB free"
            except OSError:
                free = ""
            checks["output_dir"] = (Check(OK, free) if path.is_dir() else
                                    Check(INFO, "Will be created when you save" + (f" ({free})" if free else "")))

    try:
        cmd = shlex.split(r["command"])
    except ValueError:
        cmd = None
    if not cmd:
        checks["recorder_command"] = Check(ERROR, "Needed: gpu-screen-recorder, or the Flatpak's command")
    elif not shutil.which(os.path.expanduser(cmd[0])):
        checks["recorder_command"] = Check(ERROR, f"{cmd[0]} isn't installed")
    else:
        checks["recorder_command"] = Check(OK, "Found")

    hook = data["hooks"]["after_clips"].strip()
    if hook:
        try:
            first = os.path.expanduser(shlex.split(hook)[0])
            checks["after_clips"] = (Check(OK, "Found") if shutil.which(first) else
                                     Check(WARNING, f"{first} isn't there or can't be run"))
        except (ValueError, IndexError):
            checks["after_clips"] = Check(ERROR, "Unclosed quote")

    bad = [i for i, url in enumerate(data["discord"]["webhook_urls"], 1)
           if url.strip() and not url.strip().startswith("https://")]
    if bad:
        checks["webhooks"] = Check(ERROR, "Not a webhook link (they start with https://): "
                                          + ", ".join(f"#{i}" for i in bad))

    missing = [f for f in data["clips"].get("other_folders", [])
               if f.strip() and not Path(f).expanduser().is_dir()]
    if missing:
        checks["other_folders"] = Check(WARNING, "Not found (not mounted?): " + ", ".join(missing))

    if s["stop_below_gb"] >= s["min_free_gb"] > 0:
        checks["stop_below_gb"] = Check(WARNING, "Recording stops before the warning comes: "
                                                 "set it below \"Warn below\"")
    return checks


def missing_tools() -> list[str]:
    """The programs the service runs that aren't installed."""
    return [t for t in ("ffmpeg", "ffprobe", "grep") if not shutil.which(t)]


# ── The setup checklist on the main window ──

@dataclass
class Step:
    ok: bool
    title: str
    hint: str = ""                # what to do when it isn't ok


def setup_checklist(cfg, service_state: str) -> list[Step]:
    """What the app needs, each ticked or not. cfg is a config.Config (None: no config yet)."""
    if cfg is None:
        return [Step(False, "Set up", "Open Settings and choose the two folders.")]
    recorder = cfg.recorder_command[0] if cfg.recorder_command else ""
    missing = missing_tools()
    hint = combat_log_hint(str(cfg.combat_log_dir)) if cfg.combat_log_dir else "Folder not found"
    out = cfg.output_dir
    return [
        Step(bool(recorder and shutil.which(os.path.expanduser(recorder))),
             "gpu-screen-recorder installed",
             "Install gpu-screen-recorder from your distribution or Flathub, then check the "
             "command in Settings > Advanced."),
        Step(not missing, "ffmpeg, ffprobe and grep installed",
             f"Install {', '.join(missing)}: clips can't be cut without "
             f"{'them' if len(missing) > 1 else 'it'}." if missing else ""),
        Step(hint != "Folder not found", "Combat log folder found",
             "Settings > Folders: the Logs folder next to WoW's _retail_ folder."),
        Step(hint.startswith("Newest"), "Combat logging on",
             "No combat log in that folder yet. In WoW: Options > Network > Advanced Combat "
             "Logging, then type /combatlog (each time you log in), or let an addon such as "
             "SimpleCombatLogger or AutoCombatLogger do it."),
        Step(bool(out and out.is_dir() and os.access(out, os.W_OK)), "Output folder ready",
             "Settings > Folders: a folder you can write to (is the drive mounted?)."),
        Step(service_state in ("active", "activating"), "Recorder service running",
             "Start it from the tray menu, or Save in Settings."),
    ]


# ── The settings window's About page ──

def license_text() -> str | None:
    """The GPL's full text as installed with the app (the wheel's dist-info, which the RPM and
    the Arch package install too), or a source checkout's LICENSE; None if neither is there."""
    from importlib import metadata

    from .. import APP_NAME
    try:
        text = metadata.distribution(APP_NAME).read_text("licenses/LICENSE")
    except metadata.PackageNotFoundError:
        text = None
    if not text:
        checkout = Path(__file__).resolve().parents[3] / "LICENSE"
        try:
            text = checkout.read_text()
        except OSError:
            return None
    return text


# ── Full sessions ──

# "2026-10-12_16-39-07.mkv": when the recording started
_RECORDING_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")


def hours_minutes(seconds: float) -> str:
    """2h14m, or 34m05s under an hour."""
    h, rest = divmod(int(max(0, seconds)), 3600)
    return f"{h}h{rest // 60:02d}m" if h else f"{rest // 60}m{rest % 60:02d}s"


def shift_run(run: dict, by: float) -> dict:
    """A clip's details with every time moved by `by` seconds: from the clip's start to the
    recording's."""
    def moved(t):
        return None if t is None else round(t + by, 1)
    out = dict(run)
    out["run_offset"] = moved(run.get("run_offset") or 0.0)
    out["encounters"] = [{**e, "start": moved(e["start"]), "end": moved(e.get("end"))}
                         for e in run.get("encounters", [])]
    out["deaths"] = [{**d, "at": moved(d["at"])} for d in run.get("deaths", [])]
    out["lusts"] = [{**lust, "at": moved(lust["at"])} for lust in run.get("lusts", [])]
    return out


def session_info(path: Path, details: dict, clips: list[dict], cut: bool = True) -> ClipInfo | None:
    """A full recording as a ClipInfo, so the browser lists it like a clip. details: its own
    details file (session_details); recordings made before those existed have none, and their
    runs come from the clips cut from them (clips: those clips' details). None for a file that
    isn't a recording of this app's."""
    runs = details.get("runs")
    start = details.get("recording_start")
    if start is None:
        m = _RECORDING_NAME.match(path.name)
        if not m:
            return None
        day, hh, mm, ss = m.groups()
        start = time.mktime(time.strptime(f"{day} {hh}:{mm}:{ss}", "%Y-%m-%d %H:%M:%S"))
    if runs is None:
        runs = [shift_run(c, c["clip_start"] - start) for c in clips if c.get("clip_start")]
    runs = sorted(runs, key=lambda r: r.get("run_offset") or 0)
    length = details.get("length")
    names = list(dict.fromkeys(r.get("raid") or r["dungeon"] for r in runs))
    title = ", ".join(names) if names else ("Not cut into clips yet" if not cut else "No runs")
    merged = {
        "clip_length": length,
        "runs": runs,
        "encounters": [e for r in runs for e in r.get("encounters", [])],
        "deaths": [d for r in runs for d in r.get("deaths", [])],
        "lusts": [lust for r in runs for lust in r.get("lusts", [])],
        "keep": bool(details.get("keep")),
    }
    n = len(runs)
    # Before it's cut, how many deaths and bosses it has isn't known yet.
    known = cut or bool(runs)
    pulls = sum(r.get("kind") == "raid" for r in runs)
    counts = [f"{k} {word}{'s' if k != 1 else ''}"
              for k, word in ((n - pulls, "run"), (pulls, "pull")) if k]
    chip = (", ".join(counts) or "Session") if cut else "Not cut yet"
    return ClipInfo(path, start, title, None, "", "session" if cut else "uncut",
                    hours_minutes(length) if length else "",
                    sum(r.get("deaths_total", 0) for r in runs) if known else None,
                    sum(r.get("bosses_killed", 0) for r in runs) if known else None,
                    bool(details.get("keep")), merged, chip=chip)


def load_sessions(processed_dir: Path, sessions_dir: Path, clips_dir: Path, read_sidecar,
                  recording_now: str | None = None, min_bytes: int = 1_000_000) -> list[ClipInfo]:
    """Every full recording, newest first: the cut ones in processed/ and those still waiting in
    sessions/, but not the one being recorded or failed starts (under min_bytes)."""
    by_recording: dict[str, list[dict]] = {}
    if clips_dir.is_dir():
        # The .json files, not the videos: a clip moved elsewhere leaves its details here.
        for f in clips_dir.glob("*.json"):
            try:
                details = read_sidecar(f)
            except (OSError, ValueError):
                continue
            if details.get("recording"):
                by_recording.setdefault(details["recording"], []).append(details)
    found = []
    for folder, cut in ((processed_dir, True), (sessions_dir, False)):
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix not in (".mkv", ".mp4") or str(f) == recording_now:
                continue
            try:
                if not f.is_file() or f.stat().st_size < min_bytes:
                    continue
                info = session_info(f, read_sidecar(f) if cut else {},
                                    by_recording.get(f.name, []), cut)
            except (OSError, ValueError, TypeError, KeyError):
                info = None
            if info:
                found.append(info)
    return sorted(found, key=lambda c: c.started, reverse=True)


def session_event_rows(details: dict) -> list[EventRow]:
    """A full session's events: a row per dungeon run holding its boss pulls (each holding its
    deaths and lusts, as event_rows makes them)."""
    rows = []
    for r in details.get("runs", []):
        inner = [row for row in event_rows(r) if row.kind != "start"]
        deaths = r.get("deaths_total") or 0
        detail = r.get("run_length", "") + (f" · {deaths} death{'s' if deaths != 1 else ''}"
                                             if deaths else "")
        rows.append(EventRow(r.get("run_offset") or 0.0, "start",
                             f"{r['dungeon']} {r.get('label', '')}".strip()
                             + (f" pull {r['pull']}" if r.get("pull") else ""), detail, inner))
    return rows
