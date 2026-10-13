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

# Bloodlust and the like. A lust is found by its debuff, which every source, drums included,
# puts on each player who wasn't sated; the haste buff, when it's listed here with the debuff it
# leaves, names it and its caster. A buff alone isn't a lust: some talents give a short one to
# the caster only, and the game re-applies the buff when a lust is extended.
LUST_BUFFS = {   # buff spell ID: (name, the debuff it leaves)
    2825: ("Bloodlust", 57724), 32182: ("Heroism", 57723), 80353: ("Time Warp", 80354),
    264667: ("Primal Rage", 264689), 390386: ("Fury of the Aspects", 390435),
    466904: ("Harrier's Cry", 264689),   # Marksmanship hunters
    # Drums: their buff names the drummer. Each expansion has new ones.
    444257: ("Drums", 57723),            # Thunderous Drums
    1243972: ("Drums", 57723),           # Void-touched Drums
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
# A key announced again within this long is the same key, resumed after a crash (find_runs).
KEY_RESUME_SECONDS = 2 * 3600
# Applications closer together than this are the same lust (one per party member).
LUST_WINDOW = 30
# The debuffs last 10 minutes. One applied again sooner to a player who still has it is the game
# refreshing it (seen after a reconnect), not a new lust. The game takes them off early after a
# boss wipe and when a key starts, so their removal is read too.
LUST_DEBUFF_SECONDS = 600
_LUST_IDS = "|".join(str(i) for i in (*LUST_BUFFS, *LUST_DEBUFFS))
_DEBUFF_IDS = "|".join(str(i) for i in LUST_DEBUFFS)
# The aura events are some of the busiest, so grep keeps only the lust spells.
_GREP = (f"  ({LOG_EVENTS}),|  SPELL_AURA_APPLIED,.*,({_LUST_IDS}),\""
         f"|  SPELL_AURA_REMOVED,.*,({_DEBUFF_IDS}),\"")
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
    encounter_id: int | None = None   # ENCOUNTER_START's; END is matched to its START by it


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
    result: str = ""             # "completed", "abandoned", or the difficulty without a key ("heroic")

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


@dataclass
class _OpenRun:
    """A run being read from the log: a key (level set), or a dungeon visit without one."""
    name: str
    instance: int
    start: float
    level: int | None = None     # a key's
    challenge: int | None = None   # a key's challengeModeID: which dungeon's keystone
    difficulty: str = ""         # a visit's: "Heroic", ...
    left_at: float | None = None   # when the player left the instance; None while inside
    keyed: bool = False          # a visit a key was started in: the key's run is used instead
    lust_debuff: int | None = None   # the debuff of lusts[-1]
    encounters: list[Encounter] = field(default_factory=list)
    deaths: list[Death] = field(default_factory=list)
    lusts: list[Lust] = field(default_factory=list)

    def left(self, t: float):
        self.left_at = self.left_at or t

    def to_run(self, end: float, label: str, result: str, timer: float | None = None) -> Run:
        return Run(self.name, self.start, end, label, timer, self.level, self.encounters,
                   self.deaths, self.lusts, result)


class _RunFinder:
    """find_runs' state as it walks the events: at most one open key and one open visit."""

    def __init__(self):
        self.runs: list[Run] = []
        self.key: _OpenRun | None = None
        self.visit: _OpenRun | None = None
        self.instance: int | None = None   # ZONE_CHANGE also fires for sub-zones and reloads
        self.last_t = 0.0
        self.sated: dict[str, float] = {}   # player GUID -> when the lust debuff they have was applied
        self.last_buff: tuple[float, int, str] | None = None   # (time, buff spell ID, caster)
        self.handlers = {
            "ZONE_CHANGE": self.zone_change,
            "CHALLENGE_MODE_START": self.key_start,
            "CHALLENGE_MODE_END": self.key_end,
            "ENCOUNTER_START": self.encounter_start,
            "ENCOUNTER_END": self.encounter_end,
            "SPELL_AURA_APPLIED": self.aura_applied,
            "SPELL_AURA_REMOVED": self.aura_removed,
            "UNIT_DIED": self.unit_died,
        }

    def feed(self, t: float, event: str, f: list[str]):
        self.last_t = t
        handler = self.handlers.get(event)
        if handler is None:
            return
        try:
            handler(t, f)
        except (IndexError, ValueError):
            log.debug(f"Unreadable combat log line: {event} {f}")

    def finish(self) -> list[Run]:
        if self.key:
            self.close_key(self.last_t)
        if self.visit:
            self.close_visit()
        return self.runs

    def open_runs(self) -> list[_OpenRun]:
        """The open key and visit, if the player is inside their instance."""
        return [r for r in (self.key, self.visit) if r and r.instance == self.instance]

    def close_key(self, t: float, completed: bool = False, timer_ms: int = 0):
        key, self.key = self.key, None
        if completed:
            self.runs.append(key.to_run(t, f"+{key.level} Completed", "completed",
                                        timer_ms / 1000 if timer_ms > 0 else None))
        else:
            self.runs.append(key.to_run(key.left_at or t, f"+{key.level} Abandoned", "abandoned"))

    def close_visit(self):
        visit, self.visit = self.visit, None
        if not visit.keyed and visit.encounters:
            self.runs.append(visit.to_run(visit.left_at or self.last_t, visit.difficulty,
                                          visit.difficulty.lower()))

    # ── Events; f is the fields after the event name ──

    def zone_change(self, t: float, f: list[str]):
        # ZONE_CHANGE,instanceID,"Name",difficultyID
        inst, name, diff = int(f[0]), f[1], int(f[2])
        if inst == self.instance:
            return
        self.instance = inst
        if self.key:
            if inst == self.key.instance:
                self.key.left_at = None
            else:
                self.key.left(t)
        if self.visit:
            if inst == self.visit.instance:
                self.visit.left_at = None   # back in after a release or a disconnect
                return
            self.visit.left(t)
        if inst and diff and diff not in DUNGEON_DIFFICULTIES and diff != 8:
            # Raids and scenarios, or a dungeon difficulty this app doesn't know yet (a new
            # game version's); logged so it can be added.
            log.info(f"Not a dungeon this app knows: {name} (difficulty {diff})")
        if inst and diff in DUNGEON_DIFFICULTIES:
            if self.visit:
                self.close_visit()
            self.visit = _OpenRun(name, inst, t, difficulty=DUNGEON_DIFFICULTIES[diff])
        elif self.visit and t - self.visit.left_at > 600:
            self.close_visit()

    def key_start(self, t: float, f: list[str]):
        # CHALLENGE_MODE_START,"Name",instanceID,challengeModeID,keystoneLevel,[affixes]
        instance, challenge, level = int(f[1]), int(f[2]), int(f[3])
        key = self.key
        if (key and (key.instance, key.challenge, key.level) == (instance, challenge, level)
                and t - key.start < KEY_RESUME_SECONDS):
            # The same key again with no END between: WoW announces the key once more when it
            # comes back after a crash or a disconnect. A reset or a new keystone always has an
            # END (all zeros) first. The run goes on.
            log.info(f"{key.name} +{level} resumed (WoW restarted or reconnected).")
            key.left_at = None
            return
        if key:
            self.close_key(t)   # a new key started without the old one ending: abandoned
        self.key = _OpenRun(f[0], instance, t, level=level, challenge=challenge)
        self.sated.clear()   # starting a key takes the lust debuffs off
        if self.visit:
            self.visit.keyed = True

    def key_end(self, t: float, f: list[str]):
        # CHALLENGE_MODE_END,instanceID,success,keystoneLevel,totalTime. success 0 (an END
        # with all zeros, too) means the key was reset or abandoned.
        if self.key:
            self.close_key(t, completed=int(f[1]) > 0, timer_ms=int(f[3]))

    def encounter_start(self, t: float, f: list[str]):
        # ENCOUNTER_START,encounterID,"Name",difficultyID,groupSize,instanceID
        for r in self.open_runs():
            r.encounters.append(Encounter(f[1], t, encounter_id=int(f[0])))

    def encounter_end(self, t: float, f: list[str]):
        # ENCOUNTER_END,encounterID,"Name",difficultyID,groupSize,success,fightTime
        encounter_id = int(f[0])
        for r in self.open_runs():
            pull = next((e for e in reversed(r.encounters)
                         if e.encounter_id == encounter_id and e.end is None), None)
            if pull:
                pull.end, pull.killed = t, f[4] == "1"

    def aura_applied(self, t: float, f: list[str]):
        # SPELL_AURA_APPLIED,source GUID,"Caster",flags,raid flags,dest GUID,"Name",flags,
        # raid flags,spellID,"Spell",school,BUFF|DEBUFF
        spell = int(f[8])
        if not f[4].startswith("Player-"):
            return
        if spell in LUST_BUFFS:
            self.last_buff = (t, spell, f[1])
            for r in self.open_runs():   # a buff logged just after its debuff names that lust
                last = r.lusts[-1] if r.lusts else None
                if (last and not last.caster and t - last.time < LUST_WINDOW
                        and LUST_BUFFS[spell][1] == r.lust_debuff):
                    last.name, last.caster = LUST_BUFFS[spell][0], f[1]
            return
        before = self.sated.get(f[4])
        self.sated[f[4]] = t
        if before is not None and t - before < LUST_DEBUFF_SECONDS:
            return   # still sated: the game refreshing the debuff
        buff = self.last_buff
        if not (buff and t - buff[0] < LUST_WINDOW and LUST_BUFFS[buff[1]][1] == spell):
            buff = None
        for r in self.open_runs():
            last = r.lusts[-1] if r.lusts else None
            if last and t - last.time < LUST_WINDOW:
                continue   # the same lust on another player
            r.lust_debuff = spell
            if buff:
                r.lusts.append(Lust(LUST_BUFFS[buff[1]][0], buff[0], buff[2]))
            else:
                r.lusts.append(Lust(_DEBUFF_ONLY_NAMES.get(spell, LUST_DEBUFFS[spell]), t))

    def aura_removed(self, t: float, f: list[str]):
        # SPELL_AURA_REMOVED, the same fields as SPELL_AURA_APPLIED
        if f[4].startswith("Player-") and int(f[8]) in LUST_DEBUFFS:
            self.sated.pop(f[4], None)

    def unit_died(self, t: float, f: list[str]):
        # UNIT_DIED,source GUID,name,flags,raid flags,dest GUID,"Name",flags,raid flags,
        # unconsciousOnDeath; that last one is 1 for a hunter's Feign Death.
        if f[4].startswith("Player-") and f[8:9] != ["1"]:
            for r in self.open_runs():
                r.deaths.append(Death(f[5], t))


def find_runs(events: Iterable[tuple[float, str, list[str]]], min_run_minutes: int = 5) -> list[Run]:
    """One Run per dungeon: a keystone from CHALLENGE_MODE_START to its END, or a dungeon
    without a key from entering it to leaving it (kept only if a boss was pulled)."""
    finder = _RunFinder()
    for t, event, f in events:
        finder.feed(t, event, f)
    # Completed keys always count; anything else only if it's long enough to be a real run.
    return [r for r in finder.finish() if r.result == "completed" or r.minutes >= min_run_minutes]
