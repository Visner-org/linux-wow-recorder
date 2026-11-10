"""Cuts session recordings into one clip per dungeon run, and deletes old files."""

import datetime
import fcntl
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import addon
from .combatlog import (RAIDS_ALL, Cast, Run, combat_logs_between, count_special_mobs, drop_despawns,
                        elite_kills, find_moments, find_runs, read_casts, read_log_events)
from .config import STATE_DIR, Config
from .detect import find_wow

log = logging.getLogger(__name__)

# Smaller recordings are failed starts with nothing in them.
MIN_RECORDING_BYTES = 1_000_000
# Bumped when the sidecar's fields change in a way readers must know about.
SIDECAR_VERSION = 1
# Held while recordings are cut, so the service and `process` (the tray's "Cut now") never cut
# the same recording at once.
CUT_LOCK = STATE_DIR / "cutting.lock"
_NAME_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})[_ ](\d{2}-\d{2}-\d{2})")


@dataclass
class SessionResult:
    recording: Path
    clips: list[tuple[Run, Path]] = field(default_factory=list)
    problem: str | None = None   # a NOTIFY_EVENTS key: "no_combat_log" or "clip_failed"
    detail: str = ""


def new_recording_path(cfg: Config) -> Path:
    """Named after the time to the second. A recording stopped and started again within the
    same second waits for the next one, so the new file doesn't overwrite the old."""
    while True:
        path = cfg.sessions_dir / f"{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.mkv"
        if not path.exists():
            return path
        time.sleep(0.1)


def pending_recordings(cfg: Config) -> list[Path]:
    """Session recordings not cut into clips yet, oldest first."""
    if not cfg.sessions_dir.is_dir():
        return []
    found = [f for f in cfg.sessions_dir.iterdir()
             if f.is_file() and f.suffix in (".mkv", ".mp4")]
    return sorted(found, key=lambda f: f.stat().st_mtime)


# A combat log written to this recently may be the one WoW has open.
LOG_IN_USE = 600


def combat_logs(log_dir: Path | None) -> list[Path]:
    """WoW's own combat logs, right in its Logs folder (not other addons' archives in it)."""
    if not log_dir or not log_dir.is_dir():
        return []
    return sorted(f for f in log_dir.glob("WoWCombatLog*.txt") if f.is_file())


def clearable_logs(log_dir: Path | None, pending: list[Path]) -> tuple[list[Path], int]:
    """The combat logs nothing needs any more: not one a recording waiting to be cut (or being
    made) needs, nor one written in the last LOG_IN_USE seconds. Returns them, and how many
    are kept."""
    logs = combat_logs(log_dir)
    needed: set[Path] = set()
    for rec in pending:
        try:
            written = rec.stat().st_mtime
        except OSError:
            continue   # cut and moved meanwhile
        try:
            start, end = recording_span(rec) or (0.0, written)
        except (OSError, subprocess.SubprocessError):
            # It can't be read (no ffprobe, a drive that stopped answering): like one ffprobe
            # can't make sense of, it keeps every log up to its last write.
            start, end = 0.0, written
        needed.update(combat_logs_between(log_dir, start, end))
    now = time.time()

    def in_use(f: Path) -> bool:
        try:
            return now - f.stat().st_mtime < LOG_IN_USE
        except OSError:
            return True   # gone meanwhile: nothing to clear
    keep = {f for f in logs if f in needed or in_use(f)}
    return [f for f in logs if f not in keep], len(keep)


def recording_span(path: Path) -> tuple[float, float] | None:
    """(start, end) of a recording as Unix times: it ends at its last write, and starts its
    duration earlier. Falls back on the time in the file name if ffprobe can't read it."""
    end = path.stat().st_mtime
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        return end - float(result.stdout.strip()), end
    except ValueError:
        pass
    m = _NAME_TIME.search(path.name)
    if m:
        start = datetime.datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H-%M-%S").timestamp()
        return start, end
    return None


def media_length(path: Path) -> float | None:
    """A video's length in seconds, as ffprobe reads it, or None."""
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, timeout=120)
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


# A cut this much shorter than asked for stopped early. ffmpeg takes a read error for the end of
# the recording and still succeeds (seen with a failing disk: 18 of 28 minutes).
SHORT_CUT_SECONDS = 5


def key_wanted(cfg: Config, run: Run) -> bool:
    """Whether a key passes the settings' rules: its level, and for a completed key whose time
    limit is known, the time left on its timer (minutes; negative is over time). Runs without a
    key always pass."""
    if run.level is None:
        return True
    if run.level < cfg.key_min_level:
        return False
    if run.result != "completed" or not (run.timer and run.time_limit):
        return True
    left = (run.time_limit - run.timer) / 60
    return not ((cfg.key_min_time_left is not None and left < cfg.key_min_time_left)
                or (cfg.key_max_time_left is not None and left > cfg.key_max_time_left))


# A file name holds 255 bytes: the names in it are cut to these, which leaves room for the
# time, the length and what copies add (" (chat hidden)", " highlight 0m45s-1m15s", ".part").
NAME_BYTES, ZONE_BYTES = 120, 50


def _name_part(text: str, limit: int) -> str:
    """text as part of a file name: no characters a file name can't have, at most limit bytes."""
    text = re.sub(r"[^\w '+()-]", "", text).strip()
    return text.encode()[:limit].decode(errors="ignore").strip()


def clip_name(run: Run, rec_start: float, rec_end: float) -> str:
    stamp = datetime.datetime.fromtimestamp(run.start).strftime("%Y-%m-%d %H-%M")
    name = _name_part(run.name, NAME_BYTES)
    if run.is_moment:   # seconds too: two can be a minute apart
        stamp = datetime.datetime.fromtimestamp(run.start).strftime("%Y-%m-%d %H-%M-%S")
        zone = _name_part(run.label, ZONE_BYTES)
        return f"{stamp} {name}{f' - {zone}' if zone else ''}.mp4"
    clip = f"{stamp} {name} {run.label} ({run.length})"
    if run.is_raid:   # the same boss is pulled again and again
        clip = f"{stamp} {name} {run.label} pull {run.pull} ({run.length})"
    # A run split over two recordings (the recorder restarted mid-run) gives one clip from
    # each; the recording's start time keeps their names apart. Names are otherwise the same
    # every time, so a retry overwrites a clip instead of making a copy.
    if run.start < rec_start or run.end > rec_end:
        part = datetime.datetime.fromtimestamp(rec_start).strftime("%H-%M-%S")
        clip += f" (part from {part})"
    return clip + ".mp4"


def clip_window(cfg: Config, rec_start: float, rec_end: float, run: Run) -> tuple[float, float] | None:
    """Where run's clip starts and stops in the recording, in seconds from its start, or None
    if less than a minute of the run is in it. A moment gets moment_seconds either side, and
    needs only to be in the recording."""
    if run.is_moment:
        if run.end < rec_start or run.start > rec_end:
            return None
        offset = max(0.0, run.start - rec_start - cfg.moment_seconds)
        stop = min(rec_end - rec_start, run.end - rec_start + cfg.moment_seconds)
        return (offset, stop) if stop - offset >= 5 else None
    offset = max(0.0, run.start - rec_start - cfg.pad_before)
    stop = min(rec_end - rec_start, run.end - rec_start + cfg.pad_after)
    return (offset, stop) if stop - offset >= 60 else None


def sidecar_path(clip: Path) -> Path:
    """The clip's details file: "<clip>.mp4" -> "<clip>.json"."""
    return clip.with_suffix(".json")


def thumbnail_path(clip: Path) -> Path:
    """The clip's picture for the clip browser: "<clip>.mp4" -> "<clip>.jpg"."""
    return clip.with_suffix(".jpg")


def drawings(clip: Path) -> list[Path]:
    """The pictures drawn on the clip, beside it: "<clip> 12m34s.png", "<clip> 12m34s (2).png"
    (the clips window's save_drawing)."""
    name = re.compile(re.escape(clip.stem) + r" \d+m\d{2}s(?: \(\d+\))?\.png")
    try:
        return [f for f in clip.parent.iterdir() if name.fullmatch(f.name)]
    except OSError:
        return []


def thumbnail_time(details: dict) -> float:
    """Where in the clip its picture is taken: 20 s into the first boss pull, or a minute into
    the run, within the clip."""
    encounters = details.get("encounters") or []
    at = ((details.get("run_offset") or 0) if details.get("kind") == "moment" else
          encounters[0]["start"] + 20 if encounters
          else (details.get("run_offset") or 0) + 60)
    length = details.get("clip_length") or 0
    return max(0.0, min(at, length - 1)) if length else at


def thumbnail_command(clip: Path, at: float) -> list[str]:
    """ffmpeg grabbing one frame at `at` s into a .part file (renamed when done)."""
    part = thumbnail_path(clip).with_name(thumbnail_path(clip).name + ".part")
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.1f}",
            "-i", str(clip), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", "-f", "mjpeg",
            str(part)]


def make_thumbnail(clip: Path, details: dict) -> Path | None:
    """The clip's picture, or None if ffmpeg couldn't make one (the browser shows a blank)."""
    out = thumbnail_path(clip)
    part = out.with_name(out.name + ".part")
    try:
        result = subprocess.run(thumbnail_command(clip, thumbnail_time(details)),
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"No thumbnail for {clip.name}: {e}")
        return None
    if result.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        log.warning(f"No thumbnail for {clip.name}: {result.stderr.strip()}")
        return None
    part.replace(out)
    return out


def read_sidecar(clip: Path) -> dict:
    """The clip's details, or {} if it has none (cut by an older version) or it's unreadable."""
    try:
        data = json.loads(sidecar_path(clip).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_sidecar(clip: Path, data: dict):
    path = sidecar_path(clip)
    tmp = path.with_name(path.name + ".part")
    text = json.dumps({**data, "casts": None} if "casts" in data else data, indent=2,
                      ensure_ascii=False)
    if "casts" in data:   # thousands of them: on one line, not a line per number
        text = text.replace('\n  "casts": null', '\n  "casts": '
                            + json.dumps(data["casts"], separators=(",", ":")), 1)
    tmp.write_text(text + "\n")
    tmp.replace(path)


def set_keep(clip: Path, keep: bool):
    """Marks a clip to be kept forever (or not), whatever clip_retention_days says."""
    data = read_sidecar(clip)
    data["keep"] = keep
    write_sidecar(clip, data)


def cast_details(casts: list[Cast], clip_start: float, length: float) -> dict:
    """The group's casts inside a clip, for its details: "casters", the players, the one
    recording first ("player"; the one with the most casts if that changed) and then the others
    by how much they cast; "casts" as [at, spell ID, caster's index] (and the cast time of a
    hard cast); "spells" naming the IDs."""
    inside = [c for c in casts if 0 <= c.time - clip_start <= length]
    counts: dict[str, int] = {}
    for c in inside:
        counts[c.player] = counts.get(c.player, 0) + 1
    mine = [c.player for c in inside if c.mine]
    player = max(set(mine), key=mine.count) if mine else None
    casters = sorted(counts, key=lambda name: (name != player, -counts[name]))
    index = {name: i for i, name in enumerate(casters)}
    return {
        "player": player,
        "casters": casters,
        "casts": [[round(c.time - clip_start, 1), c.spell, index[c.player],
                   *([c.cast] if c.cast else [])] for c in inside],
        "spells": {str(c.spell): c.name for c in inside},
    }


def sidecar_for(run: Run, clip_start: float, length: float, recording: Path,
                casts: list[Cast] | None = None) -> dict:
    """The clip's details. Times inside the clip are seconds from its start; anything outside
    the clip (a run split over two recordings) is left out."""
    def at(t: float) -> float | None:
        offset = t - clip_start
        return round(offset, 1) if 0 <= offset <= length else None

    encounters = [{"name": e.name, "start": at(e.start),
                   "end": at(e.end) if e.end is not None else None, "killed": e.killed,
                   "forces": e.forces}
                  for e in run.encounters if at(e.start) is not None]
    deaths = [{"name": d.name, "at": at(d.time)} for d in run.deaths if at(d.time) is not None]
    lusts = [{"name": l.name, "caster": l.caster, "at": at(l.time)}
             for l in run.lusts if at(l.time) is not None]
    return {
        "version": SIDECAR_VERSION,
        "dungeon": run.name,   # a raid pull's boss
        "label": run.label,
        "level": run.level,
        "result": run.result,
        "run_start": run.start,
        "run_end": run.end,
        "run_length": run.length,
        "timer": run.timer,
        "time_limit": run.time_limit,
        "clip_start": clip_start,
        "clip_length": round(length, 1),
        "run_offset": round(max(0.0, run.start - clip_start), 1),
        "bosses_killed": sum(e.killed for e in run.encounters),
        "deaths_total": len(run.deaths),
        "encounters": encounters,
        "deaths": deaths,
        "lusts": lusts,
        "recording": recording.name,
        "keep": False,
        **({"kind": "raid", "raid": run.raid, "difficulty": run.difficulty, "pull": run.pull}
           if run.is_raid else {}),
        **({"kind": "moment", "zone": run.label,
            "run_length": f"{int(length) // 60}m{int(length) % 60:02d}s",   # the clip's
            "moments": [{"kind": m.kind, "what": m.what, "at": at(m.time)}
                        for m in run.moments if at(m.time) is not None]}
           if run.is_moment else {}),
        **({"log_late": run.log_late} if run.log_late else {}),
        **(cast_details(casts, clip_start, length) if casts is not None else {}),
    }


# Room kept at the front of a clip for its index (moov): about 1 KB per second is used at 30 fps
# with Opus audio, so this leaves plenty for 60 fps or more audio packets.
MOOV_BYTES_PER_SECOND = 4096
MOOV_BYTES_EXTRA = 1_000_000


def cut_command(src: Path, offset: float, length: float, part: Path, faststart: bool) -> list[str]:
    """ffmpeg copying length s from offset s into part, no re-encode. -ss before -i starts on
    the keyframe before the run. The index goes at the front, so players and web galleries can
    start the clip before it's fully loaded: written once into space kept for it (moov_size),
    or with faststart, a second pass that rewrites the whole clip (twice the disk work)."""
    index = (["-movflags", "+faststart"] if faststart
             else ["-moov_size", str(int(MOOV_BYTES_EXTRA + MOOV_BYTES_PER_SECOND * length))])
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{offset:.3f}", "-i", str(src), "-t", f"{length:.3f}",
            "-map", "0:v", "-map", "0:a?", "-c", "copy", *index, "-f", "mp4", str(part)]


def cut_clip(cfg: Config, src: Path, rec_start: float, rec_end: float, run: Run,
             casts: list[Cast] | None = None) -> Path | None:
    """Cuts run's clip out of src and writes its sidecar (with the player's casts during it).
    None if ffmpeg failed."""
    offset, stop = clip_window(cfg, rec_start, rec_end, run)
    cfg.clips_dir.mkdir(parents=True, exist_ok=True)
    out = cfg.clips_dir / clip_name(run, rec_start, rec_end)
    part = out.with_name(out.name + ".part")
    log.info(f"Cutting {out.name}")
    result = subprocess.run(cut_command(src, offset, stop - offset, part, faststart=False),
                            capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        # The index didn't fit the space kept for it (shouldn't happen): the slow way.
        log.warning(f"Cutting {out.name} again with faststart:\n{result.stderr.strip()}")
        result = subprocess.run(cut_command(src, offset, stop - offset, part, faststart=True),
                                capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        log.error(f"ffmpeg failed on {out.name}:\n{result.stderr}")
        return None
    length = media_length(part)
    if length is None or length < stop - offset - SHORT_CUT_SECONDS:
        part.unlink(missing_ok=True)
        got = f"{length:.0f} s" if length is not None else "an unreadable clip"
        log.error(f"{out.name} came out short ({got} of {stop - offset:.0f} s): the recording "
                  f"couldn't be read to the end (a disk read error?). ffmpeg said:\n"
                  f"{result.stderr.strip() or '(nothing)'}")
        return None
    part.replace(out)
    details = sidecar_for(run, rec_start + offset, stop - offset, src, casts)
    details["keep"] = read_sidecar(out).get("keep", False)   # a retry keeps the user's choice
    thumbnail = make_thumbnail(out, details)
    details["thumbnail"] = thumbnail.name if thumbnail else None
    write_sidecar(out, details)
    return out


def _move_done(cfg: Config, src: Path, details: dict | None = None):
    """Moves a cut recording to processed/, with its details file when there are details."""
    cfg.processed_dir.mkdir(parents=True, exist_ok=True)
    done = cfg.processed_dir / src.name
    src.replace(done)
    if details is not None:
        write_sidecar(done, details)


def session_details(rec_start: float, rec_end: float, runs: list[Run], recording: Path,
                    casts: list[Cast] | None = None, moments: bool = False) -> dict:
    """A full recording's details, for the clip browser's Full sessions: its start and length,
    each dungeon run in it shaped like a clip's details, and the player's casts, with times
    from the recording's start."""
    length = rec_end - rec_start
    return {
        "version": SIDECAR_VERSION,
        "recording_start": rec_start,
        "length": round(length, 1),
        "runs": [sidecar_for(run, rec_start, length, recording) for run in runs],
        "keep": False,
        **({"moments": MOMENTS_PENDING} if moments else {}),
        **(cast_details(casts, rec_start, length) if casts is not None else {}),
    }


def process_recording(cfg: Config, src: Path) -> SessionResult:
    """Cuts one clip per dungeon run out of a recording, then moves it to processed/. If a
    clip fails, the recording stays in sessions/ and is tried again next time."""
    if src.stat().st_size < MIN_RECORDING_BYTES:
        log.info(f"{src.name} is nearly empty (a failed start); moved to processed/.")
        _move_done(cfg, src)
        return SessionResult(src)

    span = recording_span(src)
    if not span:
        return SessionResult(src, problem="clip_failed",
                             detail=f"Couldn't read the length of `{src.name}`.")
    rec_start, rec_end = span

    logs = combat_logs_between(cfg.combat_log_dir, rec_start, rec_end)
    if not logs:
        # Nothing to cut by; processed anyway, so it isn't retried after every session. Its
        # level-ups can still be cut (cut_moments).
        _move_done(cfg, src, session_details(rec_start, rec_end, [], src,
                                             moments="level" in cfg.moments))
        keep = (f"for {cfg.session_retention_days} days" if cfg.session_retention_days
                else "until you delete it")
        return SessionResult(src, problem="no_combat_log", detail=(
            f"No combat log covers `{src.name}`. Is combat logging on? "
            f"The full recording is kept in processed/ {keep}."))

    runs = [r for r in find_runs(read_log_events(logs), cfg.min_run_minutes, cfg.raids)
            if r.end > rec_start and r.start < rec_end]
    skipped = [r for r in runs if not key_wanted(cfg, r)]
    for r in skipped:
        log.info(f"{r.name} {r.label} ({r.length}) doesn't pass the key rules in Settings; "
                 "not clipped.")
    runs = [r for r in runs if r not in skipped]
    drop_despawns(runs, logs)
    count_special_mobs(runs, logs)
    casts = read_casts(logs)
    pulls = sum(r.is_raid for r in runs)
    log.info(f"{src.name}: {len(runs) - pulls} dungeon run(s) and {pulls} raid pull(s) in the "
             "combat log.")

    result = SessionResult(src)
    failed = 0
    for run in runs:
        if not clip_window(cfg, rec_start, rec_end, run):
            log.info(f"{run.name} {run.label} is barely in this recording; skipped.")
            continue
        clip = cut_clip(cfg, src, rec_start, rec_end, run, casts)
        if clip:
            result.clips.append((run, clip))
        else:
            failed += 1
    if failed:
        result.problem = "clip_failed"
        result.detail = (f"{failed} clip(s) from `{src.name}` couldn't be cut; "
                         "trying again after the next session.")
        return result

    _move_done(cfg, src, session_details(rec_start, rec_end, runs, src, casts,
                                         moments=bool(cfg.moments)))
    return result


# A full recording's details say this until its moments are cut (cut_moments).
MOMENTS_PENDING = "pending"


def cut_moments(cfg: Config) -> list[SessionResult]:
    """Cuts the moment clips (deaths, level-ups, elite kills) of the cut recordings that still
    wait for them. Only once WoW is closed: the addon's level-ups and elites are saved then."""
    results = []
    if not cfg.processed_dir.is_dir():
        return results
    waiting = []
    for f in sorted(cfg.processed_dir.glob("*.json")):
        details = read_sidecar(f)
        if details.get("moments") == MOMENTS_PENDING:
            rec = next((v for v in (f.with_suffix(".mkv"), f.with_suffix(".mp4")) if v.exists()),
                       None)
            waiting.append((f, details, rec))
    if not waiting:
        return results
    levels, elites = addon.read_saved(cfg.combat_log_dir)
    for f, details, rec in waiting:
        if rec is None:   # deleted meanwhile
            details.pop("moments", None)
            write_sidecar(f, details)
            continue
        try:
            result = _cut_moments_of(cfg, rec, details, levels, elites)
        except Exception:
            log.exception(f"Cutting the moments of {rec.name} failed")
            result = SessionResult(rec, problem="clip_failed",
                                   detail=f"Cutting moments from `{rec.name}` failed; see the log.")
        if not result.problem:
            details.pop("moments", None)
            write_sidecar(f, details)
        if result.clips or result.problem:
            results.append(result)
    return results


def _cut_moments_of(cfg: Config, rec: Path, details: dict, levels: list[tuple[float, int]],
                    elites: dict[int, str]) -> SessionResult:
    rec_start = details["recording_start"]
    rec_end = rec_start + details["length"]
    logs = combat_logs_between(cfg.combat_log_dir, rec_start, rec_end)
    events = list(read_log_events(logs)) if logs else []
    # Not inside any run or pull, clipped or not (a key the rules skipped, a short run)
    spans = [(r.start, r.end) for r in find_runs(events, 0, RAIDS_ALL)]
    moments = find_moments(
        events, cfg.moments, [lv for lv in levels if rec_start <= lv[0] <= rec_end],
        elite_kills(logs, elites) if "elite" in cfg.moments else [], spans, cfg.moment_seconds)
    moments = [m for m in moments if clip_window(cfg, rec_start, rec_end, m)]
    result = SessionResult(rec)
    if not moments:
        return result
    log.info(f"{rec.name}: {len(moments)} moment clip(s).")
    casts = read_casts(logs) if logs else None
    for m in moments:
        clip = cut_clip(cfg, rec, rec_start, rec_end, m, casts)
        if clip:
            result.clips.append((m, clip))
        else:
            result.problem = "clip_failed"
            result.detail = f"A moment from `{rec.name}` couldn't be cut; trying again later."
    return result


def process_pending(cfg: Config, stop: threading.Event | None = None,
                    in_use: Callable[[], Path | None] = lambda: None,
                    wait: bool = True) -> list[SessionResult] | None:
    """Cuts every waiting recording, oldest first. stop is checked between recordings (the
    rest wait for next time); in_use() names the recording being written now, which is
    skipped. One program cuts at a time: if another is cutting, this waits for it (or for
    stop), or with wait False returns None."""
    CUT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CUT_LOCK, "a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not wait:
                    return None
                if stop is not None and stop.wait(1):
                    return []
                if stop is None:
                    time.sleep(1)
        return _process_all(cfg, stop, in_use)


def _process_all(cfg: Config, stop: threading.Event | None,
                 in_use: Callable[[], Path | None]) -> list[SessionResult]:
    results = []
    for src in pending_recordings(cfg):
        if stop is not None and stop.is_set():
            log.info("Stopping; the other recordings are cut next time.")
            break
        if src == in_use():
            continue
        try:
            results.append(process_recording(cfg, src))
        except Exception:
            log.exception(f"Processing {src.name} failed")
            results.append(SessionResult(src, problem="clip_failed",
                                         detail=f"Processing `{src.name}` failed; see the log."))
    if in_use() is None and not (stop is not None and stop.is_set()) and find_wow() is None:
        results += cut_moments(cfg)
    return results


def add_casts(cfg: Config) -> tuple[int, int] | None:
    """Adds the player's casts to the details of the clips and full recordings cut before casts
    were read, from the combat logs still there. Returns (how many got them, how many have no
    combat log left), or None while clips are being cut."""
    CUT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CUT_LOCK, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        added = missing = 0
        read: dict[tuple[Path, ...], list[Cast]] = {}   # a big log is read once for all its clips
        for folder in (cfg.clips_dir, cfg.processed_dir):
            if not folder.is_dir():
                continue
            for f in sorted(folder.glob("*.json")):
                details = read_sidecar(f)
                start = details.get("clip_start", details.get("recording_start"))
                length = details.get("clip_length", details.get("length"))
                # "casters": read by this version (the first kept only the player's own)
                if "casters" in details or start is None or not length:
                    continue
                logs = tuple(combat_logs_between(cfg.combat_log_dir, start, start + length))
                if not logs:
                    missing += 1
                    continue
                if logs not in read:
                    read[logs] = read_casts(list(logs))
                details.update(cast_details(read[logs], start, length))
                write_sidecar(f, details)
                added += 1
        return added, missing


def stale_recordings(cfg: Config) -> list[Path]:
    """Recordings still waiting in sessions/ (their clips couldn't be cut) though they're older
    than session_retention_days. cleanup() leaves them alone: they'd be lost un-cut."""
    if cfg.session_retention_days <= 0:
        return []
    cutoff = time.time() - cfg.session_retention_days * 86400
    return [f for f in pending_recordings(cfg) if f.stat().st_mtime < cutoff]


def cleanup(cfg: Config) -> int:
    """Deletes cut recordings (processed/) and clips older than their retention, except those
    marked keep. Recordings not cut yet (sessions/) are never deleted; see stale_recordings.
    Returns the count (a video and its details, picture and drawings count as one)."""
    deleted = 0
    for folders, days in (((cfg.processed_dir,), cfg.session_retention_days),
                          ((cfg.clips_dir,), cfg.clip_retention_days)):
        if days <= 0:
            continue
        cutoff = time.time() - days * 86400
        for folder in folders:
            if not folder.is_dir():
                continue
            for f in folder.iterdir():
                # Details, pictures and drawings go with their video, below; one whose video is
                # gone is left.
                if f.suffix in (".json", ".jpg", ".png"):
                    continue
                try:
                    if not f.is_file() or f.stat().st_mtime >= cutoff:
                        continue
                    if read_sidecar(f).get("keep"):
                        continue
                    log.info(f"Deleting {f.name} (older than {days} days)")
                    f.unlink()
                    sidecar_path(f).unlink(missing_ok=True)
                    thumbnail_path(f).unlink(missing_ok=True)
                    for drawing in drawings(f):
                        drawing.unlink(missing_ok=True)
                    deleted += 1
                except OSError as e:
                    log.warning(f"Couldn't delete {f}: {e}")
    return deleted


def free_gb(cfg: Config) -> float:
    return shutil.disk_usage(cfg.output_dir).free / 1e9
