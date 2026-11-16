"""Turns a real combat log into a small test fixture that names no one.

    uv run python tests/fixtures/sanitize.py WoWCombatLog-100826_185919.txt out.txt
    uv run python tests/fixtures/sanitize.py --enemies --key 3 WoWCombatLog-110526_173620.txt out.txt

Keeps only the lines the app reads (combatlog's own grep pattern), and of the creature deaths
only the first few. --enemies adds the enemies' casts and the kicks (read_casts), cut to the
fields the app reads (they're long with advanced logging); --key N keeps only the Nth key, from
the zone change before its START to its END. Every player and pet gets a made-up GUID and name
("Player3-Realm-US", "Pet2"), creature GUIDs get made-up server and spawn numbers, and the script
fails if any original player or pet name or GUID is left. Read the output before committing it
anyway.
"""

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

from linux_wow_recorder.combatlog import _ENEMY_GREP, _GREP

CREATURE_DEATHS_KEPT = 5
_PLAYER_GUID = re.compile(r"Player-\d+-[0-9A-F]+")
_PET_GUID = re.compile(r"Pet-0-\d+-\d+-\d+-\d+-[0-9A-F]+")
_CREATURE_GUID = re.compile(r"(Creature|Vehicle)-0-\d+-\d+-\d+-(\d+)-[0-9A-F]+")
# Where a unit's name follows its GUID: (GUID field, name field), after the event name.
_UNIT_FIELDS = {event: ((0, 1), (4, 5)) for event in
                ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED", "UNIT_DIED", "PARTY_KILL", "SPELL_CAST_START",
                 "SPELL_CAST_SUCCESS", "SPELL_INTERRUPT")}
# The fields read_casts reads of a cast line (with the event's name): units, spell, its school.
CAST_FIELDS = 12


class Names:
    def __init__(self):
        self.guids: dict[str, str] = {}
        self.names: dict[str, str] = {}

    def guid(self, guid: str) -> str:
        if guid not in self.guids:
            n = sum(1 for g in self.guids if g[:3] == guid[:3]) + 1
            self.guids[guid] = (f"Player-1-{n:08X}" if guid.startswith("Player-")
                                else f"Pet-0-0-0-0-0-{n:010X}")
        return self.guids[guid]

    def creature(self, m: re.Match) -> str:
        """The same NPC ID, with a made-up spawn number: two mobs of one kind stay two."""
        if m.group() not in self.guids:
            n = sum(1 for g in self.guids if g.startswith(("Creature-", "Vehicle-"))) + 1
            self.guids[m.group()] = f"{m.group(1)}-0-0-0-0-{m.group(2)}-{n:010X}"
        return self.guids[m.group()]

    def name(self, guid: str, name: str) -> str:
        if name not in self.names:
            n = sum(1 for v in self.names.values() if v.startswith(guid[:3])) + 1
            self.names[name] = f"Player{n}-Realm-US" if guid.startswith("Player-") else f"Pet{n}"
        return self.names[name]


def sanitize_line(line: str, names: Names) -> str:
    """The line with its text left as the game wrote it, but for players' and pets' names and
    GUIDs, and creatures' server and spawn numbers (each creature its own)."""
    stamp, _, payload = line.partition("  ")
    fields = next(csv.reader([payload]))
    event, f = fields[0], fields[1:]
    for guid_i, name_i in _UNIT_FIELDS.get(event, ()):
        guid = f[guid_i] if guid_i < len(f) else ""
        if guid.startswith(("Player-", "Pet-")) and f[name_i] not in ("nil", ""):
            payload = payload.replace(f'"{f[name_i]}"', f'"{names.name(guid, f[name_i])}"')
    payload = _PLAYER_GUID.sub(lambda m: names.guid(m.group()), payload)
    payload = _PET_GUID.sub(lambda m: names.guid(m.group()), payload)
    payload = _CREATURE_GUID.sub(lambda m: names.creature(m), payload)
    return f"{stamp}  {payload}"


def first_fields(payload: str, n: int) -> str:
    """The payload's first n comma-separated fields (commas inside quotes don't count)."""
    quoted = False
    for i, ch in enumerate(payload):
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            n -= 1
            if n == 0:
                return payload[:i]
    return payload


def one_key(lines: list[str], n: int) -> list[str]:
    """The lines of the nth key (1 is the first): from the zone change before its START to
    its END (not the all-zeros one before each key)."""
    starts = [i for i, line in enumerate(lines) if "  CHALLENGE_MODE_START," in line]
    if not 1 <= n <= len(starts):
        raise SystemExit(f"There are {len(starts)} keys in this log")
    start = starts[n - 1]
    first = max((i for i in range(start) if "  ZONE_CHANGE," in lines[i]), default=start)
    end = next((i for i in range(start, len(lines))
                if re.search(r"  CHALLENGE_MODE_END,\d+,(?!0,0,0,)", lines[i])), len(lines) - 1)
    return lines[first:end + 1]


def sanitize(src: Path, enemies: bool = False, key: int | None = None) -> str:
    patterns = ["-e", _GREP] + (["-e", _ENEMY_GREP] if enemies else [])
    raw = subprocess.run(["grep", "-aE", *patterns, str(src)], capture_output=True,
                         check=True).stdout.decode(errors="replace").splitlines()
    if key is not None:
        raw = one_key(raw, key)
    names, out, creature_deaths = Names(), [], 0
    for line in raw:
        if re.search(r"  SPELL_CAST_(START|SUCCESS),", line):
            stamp, _, payload = line.partition("  ")
            line = f"{stamp}  {first_fields(payload, CAST_FIELDS)}"
        if "  UNIT_DIED," in line and not re.search(r"  UNIT_DIED,[^,]*,[^,]*,[^,]*,[^,]*,Player-", line):
            creature_deaths += 1
            if creature_deaths > CREATURE_DEATHS_KEPT:
                continue
        out.append(sanitize_line(line, names))
    text = "\n".join(out) + "\n"
    leaks = [x for x in (*names.names, *names.guids) if x in text]
    if leaks:
        raise SystemExit(f"{len(leaks)} original names or GUIDs are still in the output")
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--enemies", action="store_true")
    parser.add_argument("--key", type=int)
    parser.add_argument("log", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args(sys.argv[1:])
    args.out.write_text(sanitize(args.log, args.enemies, args.key))
