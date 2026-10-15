"""Each Mythic+ dungeon's time limit, to tell a timed key from one over time.

The combat log has a key's time (CHALLENGE_MODE_END) but not its limit, so the limits are
kept here and updated when a season starts (see docs/development.md, "A new M+ season").
From Raider.IO's static data (keystone_timer_seconds), checked against real runs.
"""

# challengeModeID (CHALLENGE_MODE_START's third field) -> (dungeon, time limit in seconds).
# A dungeon back in a later season keeps one entry, with that season's limit.
TIME_LIMITS: dict[int, tuple[str, int]] = {
    # Midnight Season 2 (from 2026-08-18)
    588: ("Altar of Fangs", 1800),
    586: ("Den of Nalorakk", 1920),
    249: ("Kings' Rest", 1980),
    587: ("Murder Row", 2040),
    399: ("Ruby Life Pools", 1680),
    250: ("Temple of Sethraliss", 1920),
    584: ("The Blinding Vale", 1800),
    585: ("Voidscar Arena", 1800),
    # Midnight Season 1 (2026-03-24 to 2026-08-18)
    402: ("Algeth'ar Academy", 1860),
    558: ("Magisters' Terrace", 2040),
    560: ("Maisara Caverns", 1980),
    559: ("Nexus-Point Xenas", 1800),
    556: ("Pit of Saron", 1800),
    239: ("Seat of the Triumvirate", 2040),
    161: ("Skyreach", 1680),
    557: ("Windrunner Spire", 1980),
}
_BY_NAME = {name: limit for name, limit in TIME_LIMITS.values()}

# A key this far under its limit upgrades the keystone by two or three levels instead of one.
PLUS_TWO, PLUS_THREE = 0.8, 0.6


def time_limit(challenge: int | None = None, dungeon: str = "") -> int | None:
    """The dungeon's time limit in seconds, by challengeModeID or else by name; None for a
    dungeon not in the table (a new season's, before an update)."""
    if challenge in TIME_LIMITS:
        return TIME_LIMITS[challenge][1]
    return _BY_NAME.get(dungeon)


def upgrade(timer: float, limit: float) -> int:
    """Keystone levels gained: 3, 2 or 1 if timed, 0 if over time."""
    if timer > limit:
        return 0
    return 3 if timer <= limit * PLUS_THREE else 2 if timer <= limit * PLUS_TWO else 1
