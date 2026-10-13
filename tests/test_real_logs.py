"""find_runs on real combat logs: three sessions of Mythic+ keys, cut down to the lines the app
reads and with every player's name and GUID replaced (tests/fixtures/sanitize.py)."""

from pathlib import Path

import pytest

from linux_wow_recorder.combatlog import find_runs, read_log_events

LOGS = Path(__file__).parent / "fixtures" / "logs"


def runs_in(name: str):
    return find_runs(read_log_events([LOGS / name]))


def summary(run):
    return (run.name, run.label, run.length, len(run.deaths), [l.name for l in run.lusts],
            [e.killed for e in run.encounters])


def test_an_evening_of_keys():
    # Each key starts with an all-zeros CHALLENGE_MODE_END (the keystone going in), END has two
    # more fields than the wiki lists, and Murder Row's sub-zone fires ZONE_CHANGE. The first
    # Kings' Rest was restarted after 3 minutes: too short to keep.
    assert [summary(r) for r in runs_in("WoWCombatLog-100826_185919.txt")] == [
        ("Kings' Rest", "+12 Completed", "24m52s", 3, ["Bloodlust"] * 3, [True] * 4),
        ("Ruby Life Pools", "+8 Completed", "19m06s", 3, ["Bloodlust"] * 2, [True] * 3),
        ("Murder Row", "+8 Completed", "22m52s", 3, ["Bloodlust"] * 2, [True, False, True, True, True]),
    ]


def test_harriers_cry_drums_and_an_extended_fury():
    runs = runs_in("WoWCombatLog-092326_123202.txt")
    assert len(runs) == 14 and all(r.result == "completed" for r in runs)
    lusts = [l for r in runs for l in r.lusts]
    assert all(l.caster for l in lusts)
    names = {l.name for l in lusts}
    assert {"Harrier's Cry", "Drums", "Fury of the Aspects"} <= names
    # The Fury buff is applied again 33 s later without a new debuff: still one lust.
    vale = next(r for r in runs if r.name == "The Blinding Vale" and r.level == 13)
    assert [l.name for l in vale.lusts] == ["Fury of the Aspects"] * 2 + ["Bloodlust"]
    assert [round(l.time - vale.start) for l in vale.lusts] == [28, 866, 1484]


@pytest.mark.parametrize("name, flagged, plain", [
    ("WoWCombatLog-100126_164114.txt", 62, 28),
    ("WoWCombatLog-092326_123202.txt", 18, 28),
])
def test_feigned_deaths_are_not_counted(name, flagged, plain):
    # UNIT_DIED with unconsciousOnDeath=1: in real logs the "dead" player (a hunter) is casting
    # again within seconds. Only the others are deaths; these sessions have none outside keys.
    lines = [l for l in (LOGS / name).read_text().splitlines()
             if "  UNIT_DIED," in l and ",Player-" in l]
    assert (sum(l.endswith(",1") for l in lines), sum(l.endswith(",0") for l in lines)) == (flagged, plain)
    assert sum(len(r.deaths) for r in runs_in(name)) == plain
