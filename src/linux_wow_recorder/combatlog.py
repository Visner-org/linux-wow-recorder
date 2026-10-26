"""Dungeon runs and raid boss pulls from WoW's combat log."""

import csv
import datetime
import logging
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from . import forces, keytimers

log = logging.getLogger(__name__)

# ZONE_CHANGE difficulty IDs of dungeons run without a keystone. Keystone runs (8) are found
# from CHALLENGE_MODE_START/END instead.
DUNGEON_DIFFICULTIES = {1: "Normal", 2: "Heroic", 23: "Mythic", 24: "Timewalking", 205: "Follower"}
# Raid difficulty IDs (ENCOUNTER_START's and ZONE_CHANGE's). Each boss pull in one is a run.
RAID_DIFFICULTIES = {14: "Normal", 15: "Heroic", 16: "Mythic", 17: "LFR", 33: "Timewalking",
                     3: "10 Normal", 4: "25 Normal", 5: "10 Heroic", 6: "25 Heroic",
                     7: "LFR", 9: "40 Player"}
# Raid pulls shorter than this aren't clipped unless the boss died: a pull reset at once.
MIN_PULL_SECONDS = 30
# What find_runs keeps of raid pulls.
RAIDS_ALL, RAIDS_KILLS, RAIDS_OFF = "all", "kills", "off"
LOG_EVENTS = ("ZONE_CHANGE|CHALLENGE_MODE_START|CHALLENGE_MODE_END|ENCOUNTER_START|ENCOUNTER_END"
              "|UNIT_DIED|PARTY_KILL")

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
    forces: float | None = None  # a key's enemy forces (percent) when this boss was killed


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
    result: str = ""             # "completed", "abandoned", the difficulty without a key
                                 # ("heroic"), or a raid pull's "kill" or "wipe"
    raid: str = ""               # a raid pull's raid ("Liberation of Undermine"); "" otherwise
    difficulty: str = ""         # a raid pull's: "Heroic", "LFR", ...
    pull: int | None = None      # a raid pull's number on this boss and difficulty, from 1
    time_limit: int | None = None   # a completed key's dungeon's limit (keytimers), s
    challenge: int | None = None    # a key's challengeModeID
    kills: list[tuple[float, str, int]] = field(default_factory=list)   # a key's counted mobs:
                                    # (time, GUID, forces)

    @property
    def is_raid(self) -> bool:
        return bool(self.raid)

    @property
    def minutes(self) -> float:
        return (self.end - self.start) / 60

    @property
    def length(self) -> str:
        """The key's in-game time if it was completed, otherwise the run's (or pull's) length:
        "24m52s"."""
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
    return _grep_events(logs, _GREP)


def _grep_events(logs: list[Path], pattern: str, wanted: Callable[[str], bool] | None = None
                 ) -> Iterator[tuple[float, str, list[str]]]:
    """(time, event, fields) of the lines grep -E finds with pattern (and wanted lets through,
    given the line after its time: the CSV parsing is the slow part)."""
    for f in logs:
        result = subprocess.run(["grep", "-aE", "-e", pattern, str(f)],
                                capture_output=True, timeout=300)
        if result.returncode > 1:   # 1 is "no lines matched"
            log.warning(f"Couldn't read {f}: {result.stderr.decode(errors='replace').strip()}")
        for line in result.stdout.decode(errors="replace").splitlines():
            stamp, _, payload = line.partition("  ")
            if wanted and not wanted(payload):
                continue
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
    inferred: bool = False       # a key whose START may not be in the log (zone_change)
    raid: str = ""               # a raid pull's raid; name is then the boss
    encounter_id: int | None = None   # a raid pull's
    pull: int | None = None
    lust_debuff: int | None = None   # the debuff of lusts[-1]
    forces: int = 0              # a key's enemy forces so far (forces.py)
    dead: set[str] = field(default_factory=set)   # the GUIDs of the mobs counted in it
    kills: list[tuple[float, str, int]] = field(default_factory=list)   # (time, GUID, forces)
    encounters: list[Encounter] = field(default_factory=list)
    deaths: list[Death] = field(default_factory=list)
    lusts: list[Lust] = field(default_factory=list)

    def left(self, t: float):
        self.left_at = self.left_at or t

    def to_run(self, end: float, label: str, result: str, timer: float | None = None) -> Run:
        return Run(self.name, self.start, end, label, timer, self.level, self.encounters,
                   self.deaths, self.lusts, result, self.raid,
                   self.difficulty if self.raid else "", self.pull,
                   challenge=self.challenge, kills=self.kills)


class _RunFinder:
    """find_runs' state as it walks the events: at most one open key and one open visit."""

    def __init__(self):
        self.runs: list[Run] = []
        self.key: _OpenRun | None = None
        self.visit: _OpenRun | None = None
        self.pull: _OpenRun | None = None   # a raid boss being fought
        self.pulls: dict[tuple, int] = {}   # (instance, encounter, difficulty) -> pulls so far
        self.instance: int | None = None   # ZONE_CHANGE also fires for sub-zones and reloads
        self.zone_name = ""                # the instance's name, for a raid pull's raid
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
            "PARTY_KILL": self.party_kill,
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
        if self.pull:
            self.close_pull(self.last_t, killed=False)
        if self.key and self.key.inferred:
            self.key = None   # no END: nothing to time it by
        if self.key:
            self.close_key(self.last_t)
        if self.visit:
            self.close_visit()
        return self.runs

    def open_runs(self) -> list[_OpenRun]:
        """The open key, visit and raid pull, if the player is inside their instance."""
        return [r for r in (self.key, self.visit, self.pull) if r and r.instance == self.instance]

    def close_pull(self, t: float, killed: bool):
        pull, self.pull = self.pull, None
        pull.encounters[0].end, pull.encounters[0].killed = t, killed
        result = "kill" if killed else "wipe"
        self.runs.append(pull.to_run(t, f"{pull.difficulty} {result.capitalize()}", result))

    def close_key(self, t: float, completed: bool = False, timer_ms: int = 0):
        key, self.key = self.key, None
        if completed:
            timer = timer_ms / 1000 if timer_ms > 0 else None
            limit = keytimers.time_limit(key.challenge, key.name)
            # "Completed" when the dungeon's limit isn't known (a new season's dungeon).
            done = ("Completed" if not (timer and limit) else
                    "Timed" if keytimers.upgrade(timer, limit) else "Over time")
            run = key.to_run(t, f"+{key.level} {done}", "completed", timer)
            run.time_limit = limit
            self.runs.append(run)
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
        self.instance, self.zone_name = inst, name
        if self.pull:
            self.close_pull(t, killed=False)   # left mid-pull: a disconnect, or a hearth
        if self.key:
            if inst == self.key.instance:
                self.key.left_at = None
            else:
                self.key.left(t)
        if inst and diff == 8 and (self.key is None
                                   or self.key.inferred and self.key.instance != inst):
            # Inside a key with none open: logging may have started after the key did (an addon
            # turning it on late), so its START isn't in the log. Read it anyway; a START drops
            # this, and its END times it (key_end). The dungeon's name gives its ID, for forces.
            self.key = _OpenRun(name, inst, t, inferred=True,
                                challenge=keytimers.challenge_id(name))
        if self.visit:
            if inst == self.visit.instance:
                self.visit.left_at = None   # back in after a release or a disconnect
                return
            self.visit.left(t)
        if inst and diff and diff not in DUNGEON_DIFFICULTIES and diff not in RAID_DIFFICULTIES \
                and diff != 8:
            # Scenarios, delves, or a difficulty this app doesn't know yet (a new game
            # version's); logged so it can be added.
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
        if self.key and self.key.inferred:
            self.key = None
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
        key = self.key
        if key and key.inferred:
            timer_ms = int(f[3])
            if int(f[0]) != key.instance or int(f[1]) <= 0 or timer_ms <= 0:
                self.key = None   # a reset, or not this dungeon's key: nothing to time it by
                return
            # No START: the key began its in-game time before this END (close enough for a clip,
            # which has pad_before too).
            key.start, key.level = t - timer_ms / 1000, int(f[2])
            log.info(f"{key.name} +{key.level}: its start isn't in the combat log (logging began "
                     f"late); timed from its end.")
        if self.key:
            self.close_key(t, completed=int(f[1]) > 0, timer_ms=int(f[3]))

    def encounter_start(self, t: float, f: list[str]):
        # ENCOUNTER_START,encounterID,"Name",difficultyID,groupSize,instanceID
        encounter_id, diff = int(f[0]), int(f[2])
        if diff in RAID_DIFFICULTIES:
            if self.pull:
                self.close_pull(t, killed=False)   # no END for the last one (a disconnect)
            inst = int(f[4])
            n = self.pulls[inst, encounter_id, diff] = self.pulls.get((inst, encounter_id, diff), 0) + 1
            self.pull = _OpenRun(f[1], inst, t, difficulty=RAID_DIFFICULTIES[diff],
                                 raid=self.zone_name if inst == self.instance else "Raid",
                                 encounter_id=encounter_id, pull=n,
                                 encounters=[Encounter(f[1], t, encounter_id=encounter_id)])
            self.instance = inst   # a log started inside the raid has no ZONE_CHANGE yet
            return
        for r in self.open_runs():
            r.encounters.append(Encounter(f[1], t, encounter_id=encounter_id))

    def encounter_end(self, t: float, f: list[str]):
        # ENCOUNTER_END,encounterID,"Name",difficultyID,groupSize,success,fightTime
        encounter_id = int(f[0])
        if self.pull and self.pull.encounter_id == encounter_id:
            self.close_pull(t, killed=f[4] == "1")
            return
        for r in self.open_runs():
            pull = next((e for e in reversed(r.encounters)
                         if e.encounter_id == encounter_id and e.end is None), None)
            if pull:
                pull.end, pull.killed = t, f[4] == "1"
                if pull.killed and r.challenge:
                    pull.forces = forces.percent(r.challenge, r.forces)

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
            return
        self.mob_killed(t, f[4])

    def party_kill(self, t: float, f: list[str]):
        # PARTY_KILL, the same fields as UNIT_DIED. Now and then a mob gets only this line, no
        # UNIT_DIED, and the bar still counts it.
        if not f[4].startswith("Player-"):
            self.mob_killed(t, f[4])

    def mob_killed(self, t: float, guid: str):
        """Counts a mob in the key's enemy forces, once (it may have a PARTY_KILL and a
        UNIT_DIED)."""
        key = self.key
        if key and key.challenge and key.instance == self.instance and guid not in key.dead:
            worth = forces.forces(key.challenge, forces.npc_id(guid))
            if worth:
                key.dead.add(guid)
                key.forces += worth
                key.kills.append((t, guid, worth))


def drop_despawns(runs: list[Run], logs: list[Path]):
    """Takes the mobs that despawned out of the keys' enemy forces. A despawn (some packs go as
    a boss is pulled) gets a UNIT_DIED like a kill, but the bar doesn't count it; it's the only
    line with that mob's GUID, where a mob that was fought is in its damage and auras too (and
    its PARTY_KILL)."""
    guids = {g for r in runs for _, g, _ in r.kills}
    if not guids or not logs:
        return
    fought = _guids_fought(logs, guids)
    if fought is None:
        return
    for r in runs:
        if all(g in fought for _, g, _ in r.kills):
            continue
        log.info(f"{r.name}: {sum(g not in fought for _, g, _ in r.kills)} mob(s) despawned, "
                 "not counted in its enemy forces.")
        r.kills = [k for k in r.kills if k[1] in fought]
        _recount(r)


def _recount(r: Run):
    """The forces at each boss kill again, from the key's kills."""
    for e in r.encounters:
        if e.forces is not None and e.end is not None:
            e.forces = forces.percent(r.challenge, sum(w for t, _, w in r.kills if t <= e.end))


def count_special_mobs(runs: list[Run], logs: list[Path]):
    """Adds to the keys' enemy forces what their deaths alone get wrong, read from the damage
    lines (advanced combat logging): mobs that took a killing blow but have no death line (some
    never get one, like Temple's Static Anomalies; some die seconds later, maybe out of the
    logger's range), and spawns worth more than their NPC (forces.SPAWNS), told apart by where
    they died."""
    keys = [r for r in runs if r.challenge in forces.COUNTS]
    if not keys or not logs:
        return
    npcs = {n for r in keys for n in forces.COUNTS[r.challenge][1]}
    placed = {n for c, n in forces.SPAWNS if any(r.challenge == c for r in keys)}
    counted = {g for r in keys for _, g, _ in r.kills}

    def wanted(payload: str) -> bool:
        # Most damage lines are on mobs already counted; those are only read for SPAWNS.
        guid = payload.split(",", 6)[5]
        npc = forces.npc_id(guid)
        return npc in npcs and (guid not in counted or npc in placed)
    killed: dict[str, float] = {}   # GUID -> when it took its first killing blow
    where: dict[str, tuple[float, float]] = {}   # GUID -> where it last took damage
    pattern = "_DAMAGE(_LANDED)?,.*-(" + "|".join(map(str, sorted(npcs))) + ")-"
    for t, event, f in _grep_events(logs, pattern, wanted):
        info = _damaged_unit(event, f)
        if info:
            guid, x, y, overkill = info
            if overkill > 0:
                killed.setdefault(guid, t)
            where[guid] = (x, y)
    for r in keys:
        known = {g for _, g, _ in r.kills}
        kills = [(t, g, forces.forces(r.challenge, forces.npc_id(g))) for g, t in killed.items()
                 if g not in known and r.start <= t <= r.end]
        kills = [k for k in r.kills + kills if k[2]]
        kills = [(t, g, g in where and forces.spawn_forces(r.challenge, forces.npc_id(g),
                                                            *where[g]) or worth)
                 for t, g, worth in kills]
        if sorted(kills) != sorted(r.kills):
            r.kills = sorted(kills)
            _recount(r)


# The group's casts: players whose source flags say mine (0x1: the player who logged, 0x511),
# party (0x2, 0x512) or raid (0x4, 0x514), not outsiders (0x8: anyone else nearby). Pets are
# mine or party too, but don't have Player- GUIDs.
_CAST_GREP = r'  SPELL_CAST_(START|SUCCESS),Player-[^,]*,"[^"]*",0x[0-9a-fA-F]*[1-7],'
# A START this long or less before a SUCCESS of the same spell is that cast's start (a hard cast).
MAX_CAST_SECONDS = 10


@dataclass
class Cast:
    time: float
    spell: int                   # spell ID
    name: str                    # the spell's
    player: str                  # who cast it: "Name-Realm-Region"
    cast: float | None = None    # a hard cast's cast time, s; None for an instant or a channel
    mine: bool = False           # cast by the player who logged (the one recording)


def read_casts(logs: list[Path]) -> list[Cast]:
    """The spells the player who logged and their group cast (each SPELL_CAST_SUCCESS), in time
    order. Cast times come from the START before it; an interrupted cast has no SUCCESS and
    isn't listed."""
    started: dict[tuple[str, int], float] = {}
    casts = []
    for t, event, f in _grep_events(logs, _CAST_GREP):
        try:
            key = (f[0], int(f[8]))
        except (IndexError, ValueError):
            continue
        if event == "SPELL_CAST_START":
            started[key] = t
            continue
        began = started.pop(key, None)
        cast = round(t - began, 1) if began is not None and 0 < t - began <= MAX_CAST_SECONDS \
            else None
        casts.append(Cast(t, key[1], f[9], f[1], cast or None, int(f[2], 16) & 1 == 1))
    return sorted(casts, key=lambda c: c.time)


def _damaged_unit(event: str, f: list[str]) -> tuple[str, float, float, int] | None:
    """(GUID, x, y, overkill) of the unit a damage line hit, from its unit info; overkill is -1
    when the hit didn't kill it. None for other lines and logs without advanced logging."""
    if not (event.endswith("_DAMAGE") or event.endswith("_DAMAGE_LANDED")):
        return None
    i = 8 if event.startswith("SWING_") else 11   # the unit info, after the spell's fields
    if len(f) < i + 22 or f[i] != f[4]:   # amount, base amount and overkill follow it
        return None
    try:
        return f[i], float(f[i + 14]), float(f[i + 15]), int(f[i + 21])
    except ValueError:
        return None


def _guids_fought(logs: list[Path], guids: set[str]) -> set[str] | None:
    """The GUIDs that are in a line other than their UNIT_DIED; None if grep failed."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as pats:
        pats.write("\n".join(guids) + "\n")
        pats.flush()
        # grep the lines with any of them, drop the UNIT_DIEDs, then print which GUIDs are left.
        steps = [["grep", "-ahF", "-f", pats.name, *map(str, logs)],
                 ["grep", "-av", "  UNIT_DIED,"], ["grep", "-aohF", "-f", pats.name]]
        procs = []
        for cmd in steps:
            procs.append(subprocess.Popen(cmd, stdin=procs[-1].stdout if procs else None,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
            if len(procs) > 1:
                procs[-2].stdout.close()
        try:
            out, _ = procs[-1].communicate(timeout=300)
        except subprocess.TimeoutExpired:
            out = None
        for p in procs:
            p.kill()
            p.wait()
    if out is None or procs[0].returncode not in (0, 1):
        log.warning("Couldn't check the combat log for despawned mobs.")
        return None
    return set(out.decode(errors="replace").split())


def run_in_progress(events: Iterable[tuple[float, str, list[str]]]) -> bool:
    """Whether the events end inside a key, a dungeon run without one or a raid boss pull:
    cutting the recording now would split that run in two. Standing in the dungeon after a key
    ended isn't (its visit is the key's)."""
    finder = _RunFinder()
    for t, event, f in events:
        finder.feed(t, event, f)
    return any(not r.keyed for r in finder.open_runs())


def find_runs(events: Iterable[tuple[float, str, list[str]]], min_run_minutes: int = 5,
              raids: str = RAIDS_ALL) -> list[Run]:
    """One Run per dungeon: a keystone from CHALLENGE_MODE_START to its END, or a dungeon
    without a key from entering it to leaving it (kept only if a boss was pulled). And one per
    raid boss pull, from ENCOUNTER_START to its END: every pull, the kills only, or none
    (raids: RAIDS_ALL, RAIDS_KILLS or RAIDS_OFF)."""
    finder = _RunFinder()
    for t, event, f in events:
        finder.feed(t, event, f)

    def keep(r: Run) -> bool:
        if r.is_raid:
            # A pull reset at once isn't worth a clip; a kill always is.
            return raids != RAIDS_OFF and (r.result == "kill" or (
                raids == RAIDS_ALL and r.end - r.start >= MIN_PULL_SECONDS))
        # Completed keys always count; anything else only if it's long enough to be a real run.
        return r.result == "completed" or r.minutes >= min_run_minutes
    return [r for r in finder.finish() if keep(r)]
