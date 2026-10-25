"""Checks forces.py against real keys: matches each rise of the enemy forces bar, as the
ForcesLog addon (tools/ForcesLog) logged it, to the mobs that died just before it in the combat
log, and lists the NPCs whose count differs from the table or isn't in it.

    uv run python tools/forces_check.py [SavedVariables/ForcesLog.lua]

Without a path it reads every account's ForcesLog.lua next to the configured combat log folder.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from linux_wow_recorder import config, forces
from linux_wow_recorder.combatlog import combat_logs_between, read_log_events

# A death shows on the bar within this long (s); one at the same moment as a rise is in it.
MATCH_SECONDS = 3.0


def parse_lua(text: str):
    """The table WoW saves for ForcesLogDB: tables, numbers, strings, booleans."""
    tokens = re.findall(r'\[|\]|\{|\}|=|,|"(?:[^"\\]|\\.)*"|-?[\d.]+(?:e-?\d+)?|\w+|--[^\n]*',
                        text)
    tokens = [t for t in tokens if not t.startswith("--")]
    pos = 0

    def value():
        nonlocal pos
        t = tokens[pos]
        pos += 1
        if t == "{":
            items, keyed = [], {}
            while tokens[pos] != "}":
                if tokens[pos] == "[":
                    key = tokens[pos + 1]
                    pos += 4   # [ key ] =
                    keyed[key.strip('"') if key.startswith('"') else int(key)] = value()
                else:
                    items.append(value())
                if tokens[pos] == ",":
                    pos += 1
            pos += 1
            for i, item in enumerate(items, 1):
                keyed[i] = item
            return keyed
        if t.startswith('"'):
            return t[1:-1]
        if t in ("true", "false", "nil"):
            return {"true": True, "false": False, "nil": None}[t]
        return float(t) if "." in t or "e" in t else int(t)

    while tokens[pos] != "=":
        pos += 1
    pos += 1
    return value()


def as_list(table: dict) -> list:
    return [table[i] for i in range(1, len(table) + 1)]


def deaths(log_dir: Path, start: float, end: float) -> list[tuple[float, int, str]]:
    """(time, NPC ID, name) of each creature that died in [start, end], once per mob (a few log
    only a PARTY_KILL)."""
    seen, found = set(), []
    for t, event, f in read_log_events(combat_logs_between(log_dir, start, end)):
        if event not in ("UNIT_DIED", "PARTY_KILL") or not start <= t <= end or f[4] in seen:
            continue
        npc = forces.npc_id(f[4])
        if npc is not None:
            seen.add(f[4])
            found.append((t, npc, f[5]))
    return found


def clock_offset(died: list[tuple[float, int, str]], steps: list) -> float:
    """How far the combat log's clock is behind the addon's in this key (s): the shift that puts
    the most deaths just before a rise. The two clocks differ by up to a few seconds."""
    rises = sorted(t for t, _ in steps)

    def fit(shift):
        hits, i = 0, 0
        for dt, _, _ in died:
            while i < len(rises) and rises[i] < dt + shift - 0.05:
                i += 1
            hits += i < len(rises) and rises[i] - dt - shift <= 0.5
        return hits

    return max((s / 20 for s in range(-200, 201)), key=fit)


def check(runs: list[dict], log_dir: Path):
    groups = []   # (map, the bar's rise, Counter of the NPCs that died just before it)
    zeros: dict[tuple[int, int], int] = Counter()   # (map, NPC) -> deaths with no rise near
    names: dict[int, str] = {}
    for run in runs:
        challenge = run["map"]
        steps = [as_list(s) for s in as_list(run.get("steps", {}))]
        # The bar goes back to 0 when the key ends; nothing after that counts.
        drop = next((i for i in range(1, len(steps)) if steps[i][1] < steps[i - 1][1]), None)
        steps = steps[:drop]
        if len(steps) < 2:
            continue
        table = forces.COUNTS.get(challenge)
        print(f"Key {challenge} +{run.get('level')}: {len(steps)} rises, bar total "
              f"{run.get('total')}" + (f" (table: {table[0]})" if table else " (not in the table)"))
        died = deaths(log_dir, run["start"] - 15, steps[-1][0] + 15)
        shift = clock_offset(died, steps)
        died = [(dt + shift, n, name) for dt, n, name in died]
        print(f"  combat log clock {shift:+.2f} s")
        full = next((t for t, c in steps if c >= run.get("total", c + 1)), None)
        if full is not None:
            died = [d for d in died if d[0] <= full - MATCH_SECONDS]
        for _, n, name in died:
            names[n] = name
        prev_t, prev_count, lost = run["start"], 0, 0
        for t, count in steps:
            if count >= run.get("total", count + 1):
                break   # the bar stops at 100%, so the last kill's count is cut short
            group = Counter(n for dt, n, _ in died if prev_t < dt <= t + 0.05
                            and t - dt <= MATCH_SECONDS)
            if group:
                groups.append((challenge, count - prev_count, group))
            else:
                lost += count - prev_count
            prev_t, prev_count = t, count
        rises = [t for t, _ in steps]
        for dt, n, _ in died:
            if not any(-0.05 <= t - dt <= MATCH_SECONDS for t in rises):
                zeros[challenge, n] += 1
        if lost:
            print(f"  {lost} forces came with no death in the log (killed out of range?)")

    # Mobs that died alone (or with ones already worked out) tell their count; repeat until
    # nothing new is learnt. Each NPC's count is the one seen most often.
    solved = {k: 0 for k in zeros}
    seen: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for k, times in zeros.items():
        seen[k][0] += times
    for _ in range(10):
        votes: dict[tuple[int, int], Counter] = defaultdict(Counter)
        for challenge, rise, group in groups:
            unknown = [n for n in group if (challenge, n) not in solved]
            if len(unknown) != 1:
                continue
            rest = rise - sum(solved[challenge, n] * k for n, k in group.items() if n != unknown[0])
            if rest >= 0 and rest % group[unknown[0]] == 0:
                votes[challenge, unknown[0]][rest // group[unknown[0]]] += 1
        if not votes:
            break
        for k, v in votes.items():
            seen[k] += v
            solved[k] = v.most_common(1)[0][0]
    print()
    wrong = 0
    for (challenge, npc), counts in sorted(seen.items()):
        count, times = counts.most_common(1)[0]
        known = forces.forces(challenge, npc)
        if count != known:
            wrong += 1
            note = "not in the table" if not known else f"table says {known}"
            print(f"{challenge} {npc} {names.get(npc, '?')}: {count} ({times}x of "
                  f"{sum(counts.values())}; {note})")
    unsure = sorted({(c, n) for c, _, g in groups for n in g} - set(seen))
    for challenge, npc in unsure:
        print(f"{challenge} {npc} {names.get(npc, '?')}: only died with others; can't tell")
    print("All counts match the table." if not wrong else f"{wrong} to look at.")


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
