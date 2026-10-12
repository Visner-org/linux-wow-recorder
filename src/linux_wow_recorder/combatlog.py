"""Dungeon runs from WoW's combat log."""

import csv
import datetime
import logging
import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ZONE_CHANGE difficulty IDs of dungeons run without a keystone. Keystone runs (8) are found
# from CHALLENGE_MODE_START/END instead.
DUNGEON_DIFFICULTIES = {1: "Normal", 2: "Heroic", 23: "Mythic", 24: "Timewalking", 205: "Follower"}
LOG_EVENTS = ("ZONE_CHANGE|CHALLENGE_MODE_START|CHALLENGE_MODE_END|ENCOUNTER_START|ENCOUNTER_END"
              "|UNIT_DIED")

# Bloodlust and the like: the haste buffs (whose caster is named), and the debuff each one
# leaves. Every source, drums included, puts one of the debuffs on the player, so a lust from a
# buff not listed here is still found, just without its caster.
LUST_BUFFS = {
    2825: "Bloodlust", 32182: "Heroism", 80353: "Time Warp", 264667: "Primal Rage",
    390386: "Fury of the Aspects",
}
LUST_DEBUFFS = {
    57724: "Bloodlust",            # Sated
    57723: "Heroism",              # Exhaustion (Heroism and drums)
    80354: "Time Warp",            # Temporal Displacement
    264689: "Primal Rage",         # Fatigued
    390435: "Fury of the Aspects", # Exhaustion (Evoker)
}
# Exhaustion alone, with no Heroism buff beside it, is drums (or another item).
_DEBUFF_ONLY_NAMES = {57723: "Drums"}
# Applications closer together than this are the same lust (one per party member).
LUST_WINDOW = 30
# The debuffs last 10 minutes, so one applied again sooner to the same player is the game
# refreshing it (seen after a reconnect), not a new lust.
LUST_DEBUFF_SECONDS = 600
_LUST_IDS = "|".join(str(i) for i in (*LUST_BUFFS, *LUST_DEBUFFS))
# SPELL_AURA_APPLIED is one of the busiest events, so grep keeps only the lust spells.
_GREP = f"  ({LOG_EVENTS}),|  SPELL_AURA_APPLIED,.*,({_LUST_IDS}),\""
# "10/8/2026 19:03:12.345-7"; older and Classic-based clients may leave out the year, and an
# offset may have a fraction for half-hour time zones.
_LOG_TIME = re.compile(r"(\d+)/(\d+)(?:/(\d+))? (\d+):(\d+):(\d+)\.(\d+)([+-]\d+(?:\.\d+)?)?$")
_LOG_NAME = re.compile(r"WoWCombatLog-(\d{6}_\d{6})\.txt$")


@dataclass
class Encounter:
    name: str
    start: float
    end: float | None = None     # None if the log ends mid-pull
    killed: bool = False


@dataclass
class Death:
    name: str                    # as logged: "Name-Realm-Region"
    time: float


@dataclass
class Lust:
    name: str                    # "Bloodlust", "Time Warp", ...
    time: float
    caster: str | None = None    # None for drums and other sources not in LUST_BUFFS


@dataclass
class Run:
    name: str
    start: float
    end: float
    label: str                   # "+12 Completed", "+10 Abandoned", "Heroic", ...
    timer: float | None = None   # a completed key's in-game time (with death penalties), s
    level: int | None = None     # keystone level; None without a key
    encounters: list[Encounter] = field(default_factory=list)   # boss pulls, kills and wipes
    deaths: list[Death] = field(default_factory=list)           # player deaths
    lusts: list[Lust] = field(default_factory=list)

    @property
    def result(self) -> str:
        """"completed", "abandoned", or the difficulty of a run without a key ("heroic", ...)."""
        return self.label.split()[-1].lower()

    @property
    def minutes(self) -> float:
        return (self.end - self.start) / 60

    @property
    def length(self) -> str:
        """The key's in-game time if it was completed, otherwise the run's length: "24m52s"."""
        mins, secs = divmod(int(self.timer or self.end - self.start), 60)
        return f"{mins}m{secs:02d}s"


def parse_log_time(stamp: str, now: float | None = None) -> float | None:
    """A combat log timestamp ("10/8/2026 19:03:12.345-7") as a Unix time. Without an offset
    it's local time; without a year it's the latest one that isn't in the future."""
    m = _LOG_TIME.match(stamp)
    if not m:
        return None
    mo, d, y, h, mi, s, ms, off = m.groups()
    tz = datetime.timezone(datetime.timedelta(hours=float(off))) if off else None
    now_dt = datetime.datetime.fromtimestamp(time.time() if now is None else now, tz)
    try:
        dt = datetime.datetime(int(y) if y else now_dt.year, int(mo), int(d), int(h), int(mi),
                               int(s), int(ms.ljust(6, "0")[:6]), tzinfo=tz)
        if not y and dt > now_dt + datetime.timedelta(days=1):
            dt = dt.replace(year=dt.year - 1)   # a log from December read in January
    except ValueError:   # Feb 29 in the wrong year, or garbage
        return None
    return dt.timestamp()


def combat_logs_between(log_dir: Path, start: float, end: float) -> list[Path]:
    """Combat log files under log_dir (subfolders too, e.g. Raider.IO's archive) that overlap
    [start, end], oldest first."""
    found = []
    for f in log_dir.glob("**/WoWCombatLog-*.txt"):
        m = _LOG_NAME.search(f.name)
        if not m:
            continue
        opened = datetime.datetime.strptime(m.group(1), "%m%d%y_%H%M%S").timestamp()
        if opened <= end and f.stat().st_mtime >= start:
            found.append((opened, f))
    return [f for _, f in sorted(found)]


def read_log_events(logs: list[Path]) -> Iterator[tuple[float, str, list[str]]]:
    """Yields (time, event, fields) for the events find_runs needs. grep does the filtering:
    the logs reach 1 GB, and grep reads one in about half a second."""
    for f in logs:
        result = subprocess.run(["grep", "-aE", _GREP, str(f)],
                                capture_output=True, timeout=300)
        if result.returncode > 1:   # 1 is "no lines matched"
            log.warning(f"Couldn't read {f}: {result.stderr.decode(errors='replace').strip()}")
        for line in result.stdout.decode(errors="replace").splitlines():
            stamp, _, payload = line.partition("  ")
            t = parse_log_time(stamp)
            if t is None:
                continue
            fields = next(csv.reader([payload]))
            yield t, fields[0], fields[1:]


def find_runs(events: Iterable[tuple[float, str, list[str]]], min_run_minutes: int = 5) -> list[Run]:
    """One Run per dungeon: a keystone from CHALLENGE_MODE_START to its END, or a dungeon
    without a key from entering it to leaving it (kept only if a boss was pulled)."""
    runs: list[Run] = []
    key = None        # open keystone run
    visit = None      # open dungeon visit without a key
    instance = None   # current instance ID (ZONE_CHANGE also fires for sub-zones and reloads)
    last_t = 0.0

    def close_key(t, timer_ms=0):
        if timer_ms:
            run = Run(key["name"], key["start"], t, f"+{key['level']} Completed", timer_ms / 1000)
        else:
            end = key["left_at"] or t
            run = Run(key["name"], key["start"], end, f"+{key['level']} Abandoned")
        run.level, run.encounters, run.deaths = key["level"], key["encounters"], key["deaths"]
        run.lusts = key["lusts"]
        runs.append(run)

    def close_visit():
        if not visit["keyed"] and visit["encounters"]:
            end = visit["left_at"] or last_t
            runs.append(Run(visit["name"], visit["start"], end, visit["difficulty"],
                            encounters=visit["encounters"], deaths=visit["deaths"],
                            lusts=visit["lusts"]))

    def open_runs():
        """The open key and visit, if the player is inside their instance."""
        return [r for r in (key, visit) if r and r["instance"] == instance]

    debuffed: dict[str, float] = {}   # player GUID -> when a lust debuff was last applied
    for t, event, f in events:
        last_t = t
        try:
            if event == "ZONE_CHANGE":
                inst, name, diff = int(f[0]), f[1], int(f[2])
                if inst == instance:
                    continue
                instance = inst
                if key:
                    key["left_at"] = None if inst == key["instance"] else (key["left_at"] or t)
                if visit:
                    if inst == visit["instance"]:
                        visit["left_at"] = None   # back in after a release or a disconnect
                        continue
                    visit["left_at"] = visit["left_at"] or t
                if inst and diff and diff not in DUNGEON_DIFFICULTIES and diff != 8:
                    # Raids and scenarios, or a dungeon difficulty this app doesn't know yet
                    # (a new game version's); logged so it can be added.
                    log.info(f"Not a dungeon this app knows: {name} (difficulty {diff})")
                if inst and diff in DUNGEON_DIFFICULTIES:
                    if visit:
                        close_visit()
                    visit = {"name": name, "instance": inst, "start": t, "left_at": None,
                             "difficulty": DUNGEON_DIFFICULTIES[diff], "encounters": [],
                             "deaths": [], "lusts": [], "keyed": False}
                elif visit and t - visit["left_at"] > 600:
                    close_visit()
                    visit = None
            elif event == "CHALLENGE_MODE_START":
                if key:
                    close_key(t)   # a new key started without the old one ending: abandoned
                key = {"name": f[0], "instance": int(f[1]), "level": int(f[3]),
                       "start": t, "left_at": None, "encounters": [], "deaths": [],
                       "lusts": []}
                if visit:
                    visit["keyed"] = True
            elif event == "CHALLENGE_MODE_END":
                if key:
                    # An END with all zeros means the key was reset or abandoned.
                    close_key(t, timer_ms=int(f[3]) if int(f[2]) > 0 else 0)
                    key = None
            elif event == "ENCOUNTER_START":
                # ENCOUNTER_START,encounterID,"Name",difficultyID,groupSize,instanceID
                for r in open_runs():
                    r["encounters"].append(Encounter(f[1], t))
            elif event == "ENCOUNTER_END":
                # ENCOUNTER_END,encounterID,"Name",difficultyID,groupSize,success,fightTime
                for r in open_runs():
                    pull = next((e for e in reversed(r["encounters"])
                                 if e.name == f[1] and e.end is None), None)
                    if pull:
                        pull.end, pull.killed = t, f[4] == "1"
            elif event == "SPELL_AURA_APPLIED":
                # SPELL_AURA_APPLIED,source GUID,"Caster",flags,raid flags,dest GUID,"Name",
                # flags,raid flags,spellID,"Spell",school,BUFF|DEBUFF
                spell = int(f[8])
                if not f[4].startswith("Player-"):
                    continue
                if spell in LUST_DEBUFFS:
                    before = debuffed.get(f[4])
                    debuffed[f[4]] = t
                    if before is not None and t - before < LUST_DEBUFF_SECONDS:
                        continue
                for r in open_runs():
                    last = r["lusts"][-1] if r["lusts"] else None
                    if last and t - last.time < LUST_WINDOW:
                        if spell in LUST_BUFFS and not last.caster:
                            last.name, last.caster = LUST_BUFFS[spell], f[1]
                        continue
                    if spell in LUST_BUFFS:
                        r["lusts"].append(Lust(LUST_BUFFS[spell], t, f[1]))
                    elif spell in LUST_DEBUFFS:
                        name = _DEBUFF_ONLY_NAMES.get(spell, LUST_DEBUFFS[spell])
                        r["lusts"].append(Lust(name, t))
            elif event == "UNIT_DIED":
                # UNIT_DIED,source GUID,name,flags,raid flags,dest GUID,"Name",flags,raid flags,
                # unconsciousOnDeath; that last one is 1 for a hunter's Feign Death.
                if f[4].startswith("Player-") and f[8:9] != ["1"]:
                    for r in open_runs():
                        r["deaths"].append(Death(f[5], t))
        except (IndexError, ValueError):
            log.debug(f"Unreadable combat log line: {event} {f}")

    if key:
        close_key(last_t)
    if visit:
        close_visit()

    # Completed keys always count; anything else only if it's long enough to be a real run.
    return [r for r in runs if r.label.endswith("Completed") or r.minutes >= min_run_minutes]
