"""Checks the enemy forces clips show at each boss kill against the game's bar: finds each key
the ForcesLog addon (tools/ForcesLog) logged in the combat log, as clips do, and compares each
killed boss's forces with the bar's at that moment.

    uv run python tools/boss_forces_check.py [SavedVariables/ForcesLog.lua]

Without a path it reads every account's ForcesLog.lua next to the configured combat log folder.
A boss off by a mob killed out of range (no death in the log) can't be fixed from the log;
forces_check.py says which rises came with no death.
"""

import sys
from pathlib import Path

from forces_check import as_list, clock_offset, deaths, parse_lua
from linux_wow_recorder import config
from linux_wow_recorder.combatlog import (combat_logs_between, count_special_mobs,
                                          drop_despawns, find_runs, read_log_events)

# How close the recorder's percent must be to the bar's to count as right.
TOLERANCE = 0.15


def join_halves(runs: list[dict]) -> list[dict]:
    """The addon logs a key again when WoW reconnects mid-key; this joins the second half (its
    bar already up, the first half's not back to 0) to the first."""
    joined = []
    for run in sorted(runs, key=lambda r: r["start"]):
        steps = [as_list(s) for s in as_list(run.get("steps", {}))]
        last = joined[-1] if joined else None
        if (last and last["map"] == run["map"] and steps and last["steps"]
                and 0 < last["steps"][-1][1] <= steps[0][1]):
            last["steps"] += steps
        else:
            joined.append({**run, "steps": steps})
    return joined


def check(runs: list[dict], log_dir: Path):
    off = 0
    for run in join_halves(runs):
        steps = run["steps"]
        # The bar goes back to 0 when the key ends; nothing after that counts.
        drop = next((i for i in range(1, len(steps)) if steps[i][1] < steps[i - 1][1]), None)
        steps = steps[:drop]
        if len(steps) < 2:
            continue
        logs = combat_logs_between(log_dir, run["start"] - 60, steps[-1][0] + 600)
        found = [r for r in find_runs(read_log_events(logs))
                 if r.challenge == run["map"] and abs(r.start - run["start"]) < 120]
        if not found:
            # Most often the addon's second log of one key (WoW reconnected mid-key).
            print(f"Key {run['map']} +{run.get('level')}: not in the combat log")
            continue
        key = found[0]
        drop_despawns([key], logs)
        count_special_mobs([key], logs)
        shift = clock_offset(deaths(log_dir, run["start"] - 15, steps[-1][0] + 15), steps)
        print(f"{key.name} +{key.level} ({key.label})")
        for e in key.encounters:
            if not e.killed:
                continue
            count = max((c for t, c in steps if t <= e.end + shift + 1.0), default=0)
            bar = round(min(100.0, count * 100 / run["total"]), 2)
            wrong = e.forces is None or abs(e.forces - bar) >= TOLERANCE
            off += wrong
            print(f"  {e.name:32} clip {e.forces}%  bar {bar}%" + ("   <-- off" if wrong else ""))
    print("All boss kills match the bar." if not off else f"{off} boss kills off.")


def main():
    cfg = config.load()
    if not cfg.combat_log_dir:
        sys.exit("No combat log folder set (combat_log_dir).")
    if len(sys.argv) > 1:
        files = [Path(sys.argv[1])]
    else:
        wtf = cfg.combat_log_dir.parent / "WTF" / "Account"
        files = sorted(wtf.glob("*/SavedVariables/ForcesLog.lua"))
    runs = [r for f in files for r in as_list(parse_lua(f.read_text()).get("runs", {}))]
    if not runs:
        sys.exit("No keys logged yet: run one with the ForcesLog addon, then /reload or log out.")
    check(runs, cfg.combat_log_dir)


if __name__ == "__main__":
    main()
