"""Turns a real combat log into a small test fixture that names no one.

    uv run python tests/fixtures/sanitize.py WoWCombatLog-100826_185919.txt out.txt

Keeps only the lines the app reads (combatlog's own grep pattern), and of the creature deaths
only the first few. Every player and pet gets a made-up GUID and name ("Player3-Realm-US",
"Pet2"), creature GUIDs lose their server and spawn numbers, and the script fails if any
original player or pet name or GUID is left. Read the output before committing it anyway.
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

from linux_wow_recorder.combatlog import _GREP

CREATURE_DEATHS_KEPT = 5
_PLAYER_GUID = re.compile(r"Player-\d+-[0-9A-F]+")
_PET_GUID = re.compile(r"Pet-0-\d+-\d+-\d+-\d+-[0-9A-F]+")
_CREATURE_GUID = re.compile(r"(Creature|Vehicle)-0-\d+-\d+-\d+-(\d+)-[0-9A-F]+")
# Where a unit's name follows its GUID: (GUID field, name field), after the event name.
_UNIT_FIELDS = {event: ((0, 1), (4, 5)) for event in
                ("SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED", "UNIT_DIED")}


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

    def name(self, guid: str, name: str) -> str:
        if name not in self.names:
            n = sum(1 for v in self.names.values() if v.startswith(guid[:3])) + 1
            self.names[name] = f"Player{n}-Realm-US" if guid.startswith("Player-") else f"Pet{n}"
        return self.names[name]


def sanitize_line(line: str, names: Names) -> str:
    """The line with its text left as the game wrote it, but for players' and pets' names and
    GUIDs, and creatures' server and spawn numbers."""
    stamp, _, payload = line.partition("  ")
    fields = next(csv.reader([payload]))
    event, f = fields[0], fields[1:]
    for guid_i, name_i in _UNIT_FIELDS.get(event, ()):
        guid = f[guid_i] if guid_i < len(f) else ""
        if guid.startswith(("Player-", "Pet-")) and f[name_i] not in ("nil", ""):
            payload = payload.replace(f'"{f[name_i]}"', f'"{names.name(guid, f[name_i])}"')
    payload = _PLAYER_GUID.sub(lambda m: names.guid(m.group()), payload)
    payload = _PET_GUID.sub(lambda m: names.guid(m.group()), payload)
    payload = _CREATURE_GUID.sub(lambda m: f"{m.group(1)}-0-0-0-0-{m.group(2)}-0000000000", payload)
    return f"{stamp}  {payload}"


def sanitize(src: Path) -> str:
    raw = subprocess.run(["grep", "-aE", _GREP, str(src)], capture_output=True,
                         check=True).stdout.decode(errors="replace").splitlines()
    names, out, creature_deaths = Names(), [], 0
    for line in raw:
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
    Path(sys.argv[2]).write_text(sanitize(Path(sys.argv[1])))
