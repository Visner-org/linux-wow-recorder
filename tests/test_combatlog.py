from linux_wow_recorder.combatlog import find_runs, parse_log_time, read_log_events

T0 = 1_800_000_000.0


def zone(t, inst, name, diff):
    return (T0 + t, "ZONE_CHANGE", [str(inst), name, str(diff)])


def key_start(t, inst, name, level):
    return (T0 + t, "CHALLENGE_MODE_START", [name, str(inst), "375", str(level), "[10", "9]"])


def key_end(t, inst, level, ms, success=None):
    """success defaults to whether there's a time, so key_end(t, inst, 0, 0) is all zeros."""
    success = bool(ms) if success is None else success
    return (T0 + t, "CHALLENGE_MODE_END", [str(inst), "1" if success else "0", str(level), str(ms)])


def boss(t):
    return (T0 + t, "ENCOUNTER_START", ["2397", "Boss", "8", "5", "2290"])


def test_completed_key():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 12),
        boss(100),
        key_end(1700, 2290, 12, 1_650_000),
        zone(1800, 0, "Ardenweald", 0),
    ])
    assert len(runs) == 1
    run = runs[0]
    assert (run.name, run.label) == ("Mists of Tirna Scithe", "+12 Completed")
    assert run.start == T0 + 10 and run.end == T0 + 1700
    assert run.length == "27m30s"   # the in-game timer, not the wall-clock time


def test_abandoned_key_ends_when_leaving():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        zone(900, 0, "Ardenweald", 0),
        key_end(1000, 2290, 0, 0),   # reset: all zeros
    ])
    assert [(r.label, r.end) for r in runs] == [("+10 Abandoned", T0 + 900)]


def test_key_end_success_field_decides_completed():
    # success=0 with the level and a time filled in: abandoned, whatever those say.
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        zone(900, 0, "Ardenweald", 0),
        key_end(1000, 2290, 10, 600_000, success=False),
    ])
    assert [(r.label, r.end, r.timer) for r in runs] == [("+10 Abandoned", T0 + 900, None)]

    # success=1: completed, with the in-game time.
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 2),
        key_end(1000, 2290, 2, 950_000, success=True),
    ])
    assert (run.label, run.timer) == ("+2 Completed", 950.0)


def test_a_key_resumed_after_a_crash_is_one_run():
    # Seen in a real log: WoW crashed mid-key and, back in, announced the same key again with
    # no END before it. A reset starts with an all-zeros END instead (test above).
    [run] = find_runs([
        zone(0, 1762, "Kings' Rest", 23),
        key_end(30, 1762, 0, 0),                      # the keystone going in
        key_start(31, 1762, "Kings' Rest", 10),
        (T0 + 150, "ENCOUNTER_START", ["2139", "The Golden Serpent", "8", "5", "1762"]),
        (T0 + 250, "ENCOUNTER_END", ["2139", "The Golden Serpent", "8", "5", "1", "99850"]),
        aura(600, 80353, caster="Mage-Realm-US"),
        aura(600, 80354),
        # The crash: the log stops; WoW's back 90 s later and says it all again.
        zone(902, 1762, "UNKNOWN AREA", 8),
        key_start(903, 1762, "Kings' Rest", 10),
        aura(904, 80354),                             # the debuff put back on: not a new lust
        (T0 + 1000, "ENCOUNTER_START", ["2143", "King Dazar", "8", "5", "1762"]),
        (T0 + 1100, "ENCOUNTER_END", ["2143", "King Dazar", "8", "5", "1", "99824"]),
        key_end(1400, 1762, 10, 1_442_631),
    ])
    assert (run.label, run.start, run.end, run.length) == ("+10 Timed", T0 + 31, T0 + 1400, "24m02s")
    assert [e.name for e in run.encounters] == ["The Golden Serpent", "King Dazar"]
    assert [(l.name, l.time - T0) for l in run.lusts] == [("Time Warp", 600)]


def test_heroic_dungeon_with_a_boss():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 2),
        boss(60),
        zone(1200, 0, "Ardenweald", 0),
        zone(2000, 1, "Orgrimmar", 0),   # more than 10 minutes later: the visit is over
    ])
    assert [(r.label, r.start, r.end) for r in runs] == [("Heroic", T0, T0 + 1200)]


def test_dungeon_without_a_boss_is_dropped():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 1),
        zone(1200, 0, "Ardenweald", 0),
        zone(2000, 1, "Orgrimmar", 0),
    ])
    assert runs == []


def test_short_runs_are_dropped():
    events = [zone(0, 2290, "Mists of Tirna Scithe", 2), boss(30), zone(120, 0, "Ardenweald", 0)]
    assert find_runs(events, min_run_minutes=5) == []
    assert len(find_runs(events, min_run_minutes=1)) == 1


def test_parse_log_time():
    assert parse_log_time("10/8/2026 19:03:12.345-7") == 1791511392.345
    assert parse_log_time("not a time") is None


def test_read_log_events(tmp_path):
    log = tmp_path / "WoWCombatLog-100826_190000.txt"
    log.write_text(
        '10/8/2026 19:03:12.345-7  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1\n'
        '10/8/2026 19:03:13.000-7  ZONE_CHANGE,2290,"Mists of Tirna Scithe, Hard",8\n'
        '10/8/2026 19:03:14.000-7  SPELL_DAMAGE,Player-1,"Someone",0x511\n'
    )
    events = list(read_log_events([log]))
    assert events == [(1791511393.0, "ZONE_CHANGE", ["2290", "Mists of Tirna Scithe, Hard", "8"])]


def boss_end(t, name, killed):
    return (T0 + t, "ENCOUNTER_END", ["2397", name, "8", "5", "1" if killed else "0", "90000"])


def died(t, guid, name, feign=False):
    return (T0 + t, "UNIT_DIED", ["0000000000000000", "nil", "0x80000000", "0x80000000",
                                  guid, name, "0x511", "0x0", "1" if feign else "0"])


def test_bosses_and_deaths_of_a_key():
    runs = find_runs([
        died(-50, "Player-1-AAA", "Early-Realm-US"),   # before the key: not counted
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 12),
        (T0 + 100, "ENCOUNTER_START", ["2397", "Ingra Maloch", "8", "5", "2290"]),
        died(130, "Player-1-BBB", "Tank-Realm-US"),
        died(140, "Creature-0-1-2-3", "Some Add"),     # not a player
        died(150, "Player-1-CCC", "Hunter-Realm-US", feign=True),   # Feign Death
        boss_end(160, "Ingra Maloch", killed=False),
        (T0 + 200, "ENCOUNTER_START", ["2397", "Ingra Maloch", "8", "5", "2290"]),
        boss_end(280, "Ingra Maloch", killed=True),
        key_end(1700, 2290, 12, 1_650_000),
    ])
    [run] = runs
    assert run.level == 12 and run.result == "completed"
    assert [(e.start - T0, e.end - T0, e.killed) for e in run.encounters] == [
        (100, 160, False), (200, 280, True)]
    assert [(d.name, d.time - T0) for d in run.deaths] == [("Tank-Realm-US", 130)]


def test_dungeon_without_a_key_keeps_its_bosses():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 2),
        boss(60),
        boss_end(120, "Boss", killed=True),
        zone(1200, 0, "Ardenweald", 0),
        zone(2000, 1, "Orgrimmar", 0),
    ])
    [run] = runs
    assert run.result == "heroic" and run.level is None
    assert [(e.name, e.killed) for e in run.encounters] == [("Boss", True)]


def test_encounter_end_matches_its_start_by_id():
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 2),
        (T0 + 60, "ENCOUNTER_START", ["2397", "Ingra Maloch", "2", "5", "2290"]),
        (T0 + 70, "ENCOUNTER_START", ["2400", "Mistcaller", "2", "5", "2290"]),
        (T0 + 90, "ENCOUNTER_END", ["2397", "Ingra Maloch (renamed)", "2", "5", "1", "30000"]),
        (T0 + 95, "ENCOUNTER_END", ["9999", "Mistcaller", "2", "5", "1", "25000"]),   # not its ID
        zone(1200, 0, "Ardenweald", 0),
    ])
    assert [(e.encounter_id, e.end and e.end - T0, e.killed) for e in run.encounters] == [
        (2397, 90, True), (2400, None, False)]


def aura(t, spell, caster="Shammy-Realm-US", dest="Player-1-AAA"):
    return (T0 + t, "SPELL_AURA_APPLIED", ["Player-1-CCC", caster, "0x511", "0x0", dest,
                                           "Someone-Realm-US", "0x511", "0x0", str(spell),
                                           "Spell", "8", "BUFF"])


def test_lusts():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 12),
        # Bloodlust: the buff and Sated on each of the five players count once.
        *[aura(100, 2825, dest=f"Player-1-{i}") for i in range(5)],
        *[aura(100, 57724, dest=f"Player-1-{i}") for i in range(5)],
        aura(105, 2825, dest="Pet-0-1"),          # not a player
        # Sated applied again to one player before it ran out: a refresh, not a lust.
        aura(400, 57724, dest="Player-1-3"),
        # Drums later: only the Exhaustion debuff, no caster.
        aura(900, 57723),
        key_end(1700, 2290, 12, 1_650_000),
    ])
    [run] = runs
    assert [(l.name, l.time - T0, l.caster) for l in run.lusts] == [
        ("Bloodlust", 100, "Shammy-Realm-US"), ("Drums", 900, None)]


def test_harriers_cry_and_drums_name_their_caster():
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        aura(100, 466904, caster="Hunter-Realm-US"),
        aura(100.1, 264689, dest="Player-1-AAA"),   # its debuff, Fatigued
        aura(800, 1243972, caster="Drummer-Realm-US", dest="Player-1-BBB"),
        aura(800.1, 57723, dest="Player-1-BBB"),    # Exhaustion
        key_end(1700, 2290, 10, 1_650_000),
    ])
    assert [(l.name, l.caster) for l in run.lusts] == [("Harrier's Cry", "Hunter-Realm-US"),
                                                      ("Drums", "Drummer-Realm-US")]


def aura_removed(t, spell, dest="Player-1-AAA"):
    t, _, f = aura(t, spell, dest=dest)
    return (t, "SPELL_AURA_REMOVED", f)


def test_lust_buffs_without_a_debuff_are_not_lusts():
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        aura(50, 390386, caster="Evoker-Realm-US"),   # a talent's self-buff: no debuff
        aura(500, 390386, caster="Evoker-Realm-US"),  # the real one, with Exhaustion
        aura(500, 390435),
        aura(533, 390386, caster="Evoker-Realm-US"),  # extended: the buff again, no debuff
        key_end(1700, 2290, 10, 1_650_000),
    ])
    assert [(l.name, l.time - T0) for l in run.lusts] == [("Fury of the Aspects", 500)]


def test_a_lust_after_the_debuff_was_taken_off_counts():
    # The game takes the debuffs off after a boss wipe; a lust 4 minutes later is a new one.
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        aura(100, 80353, caster="Mage-Realm-US"),
        aura(100, 80354),
        aura_removed(150, 80354),
        aura(340, 1243972, caster="Drummer-Realm-US"),
        aura(340, 57723),
        aura(400, 57724),        # Sated on a player still exhausted: not possible, so a refresh
        key_end(1700, 2290, 10, 1_650_000),
    ])
    assert [(l.name, l.time - T0) for l in run.lusts] == [("Time Warp", 100), ("Drums", 340)]


def test_a_buff_names_only_the_lust_whose_debuff_it_leaves():
    [run] = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 8),
        key_start(10, 2290, "Mists of Tirna Scithe", 10),
        aura(95, 390386, caster="Evoker-Realm-US"),   # a self-buff just before drums
        aura(100, 57723),                             # Exhaustion: drums, not Fury
        key_end(1700, 2290, 10, 1_650_000),
    ])
    assert [(l.name, l.caster) for l in run.lusts] == [("Drums", None)]


def test_read_log_events_keeps_only_lust_auras(tmp_path):
    log = tmp_path / "WoWCombatLog-100826_190000.txt"
    log.write_text(
        '10/8/2026 19:03:13.000-7  SPELL_AURA_APPLIED,Player-1-C,"Shammy",0x511,0x0,Player-1-A,'
        '"Someone",0x511,0x0,2825,"Bloodlust",8,BUFF\n'
        '10/8/2026 19:03:14.000-7  SPELL_AURA_APPLIED,Player-1-C,"Shammy",0x511,0x0,Player-1-A,'
        '"Someone",0x511,0x0,974,"Earth Shield",8,BUFF\n'
    )
    assert [f[8] for _, _, f in read_log_events([log])] == ["2825"]


def test_parse_log_time_without_a_year():
    import datetime
    now = datetime.datetime(2027, 1, 5, 12, 0).timestamp()
    # December's log read in January belongs to last year.
    assert parse_log_time("12/30 19:03:12.000", now=now) == datetime.datetime(2026, 12, 30, 19, 3, 12).timestamp()
    assert parse_log_time("1/4 19:03:12.000", now=now) == datetime.datetime(2027, 1, 4, 19, 3, 12).timestamp()
    assert parse_log_time("10/8/2026 19:03:12.000+5.5") == datetime.datetime(
        2026, 10, 8, 13, 33, 12, tzinfo=datetime.UTC).timestamp()
    assert parse_log_time("2/30/2026 19:03:12.000") is None


# ── Raids: a run per boss pull ──

def raid_boss(t, name="Vexie", diff=17, encounter=3009, inst=2769):
    return (T0 + t, "ENCOUNTER_START", [str(encounter), name, str(diff), "25", str(inst)])


def raid_boss_end(t, killed, name="Vexie", diff=17, encounter=3009):
    return (T0 + t, "ENCOUNTER_END", [str(encounter), name, str(diff), "25",
                                      "1" if killed else "0", "120000"])


def test_raid_pulls_are_runs():
    runs = find_runs([
        zone(0, 2769, "Liberation of Undermine", 17),
        raid_boss(100),
        died(150, "Player-1-AAA", "Tank-Realm-US"),
        raid_boss_end(250, killed=False),
        raid_boss(400),
        aura(410, 2825, dest="Player-1-AAA"), aura(410, 57724, dest="Player-1-AAA"),
        raid_boss_end(700, killed=True),
        raid_boss(900, "Cauldron of Carnage", encounter=3010),
        raid_boss_end(910, False, "Cauldron of Carnage", encounter=3010),   # reset at once
        zone(1200, 0, "Undermine", 0),
    ])
    wipe, kill = runs
    assert (wipe.name, wipe.raid, wipe.label, wipe.result, wipe.pull) == (
        "Vexie", "Liberation of Undermine", "LFR Wipe", "wipe", 1)
    assert wipe.start == T0 + 100 and wipe.end == T0 + 250 and wipe.length == "2m30s"
    assert [d.name for d in wipe.deaths] == ["Tank-Realm-US"]
    assert [(e.name, e.killed) for e in wipe.encounters] == [("Vexie", False)]
    assert (kill.label, kill.pull, kill.difficulty) == ("LFR Kill", 2, "LFR")
    assert [lust.name for lust in kill.lusts] == ["Bloodlust"] and not kill.deaths
    assert kill.encounters[0].killed and kill.is_raid and not kill.level


def test_raid_kills_only_or_none():
    events = [zone(0, 2769, "Liberation of Undermine", 15),
              raid_boss(100, diff=15), raid_boss_end(250, False, diff=15),
              raid_boss(300, diff=15), raid_boss_end(310, True, diff=15)]   # a short kill counts
    assert [r.label for r in find_runs(events)] == ["Heroic Wipe", "Heroic Kill"]
    assert [r.label for r in find_runs(events, raids="kills")] == ["Heroic Kill"]
    assert find_runs(events, raids="off") == []


def test_a_raid_pull_without_an_end():
    runs = find_runs([
        zone(0, 2769, "Liberation of Undermine", 16),
        raid_boss(100, diff=16),
        zone(200, 0, "Undermine", 0),        # disconnected or hearthed mid-pull
        zone(300, 2769, "Liberation of Undermine", 16),
        raid_boss(400, diff=16),
        raid_boss(600, diff=16),             # crashed: no END before the next pull
        raid_boss_end(700, True, diff=16),
    ])
    assert [(r.result, r.pull, r.end - T0) for r in runs] == [
        ("wipe", 1, 200), ("wipe", 2, 600), ("kill", 3, 700)]


def test_a_log_started_inside_a_raid():
    runs = find_runs([raid_boss(100, diff=14), raid_boss_end(400, True, diff=14)])
    assert [(r.raid, r.label) for r in runs] == [("Raid", "Normal Kill")]


def test_dungeon_bosses_arent_raid_pulls():
    runs = find_runs([
        zone(0, 2290, "Mists of Tirna Scithe", 2),
        boss(100),
        (T0 + 200, "ENCOUNTER_END", ["2397", "Boss", "2", "5", "1", "99000"]),
        zone(500, 0, "Ardenweald", 0),
    ])
    assert [(r.label, r.is_raid) for r in runs] == [("Heroic", False)]


def test_a_completed_key_is_timed_or_over_time_by_its_dungeons_limit():
    def run(ms, name="Murder Row"):   # 34 minutes
        return find_runs([
            zone(0, 2290, name, 8),
            (T0 + 10, "CHALLENGE_MODE_START", [name, "2290", "587", "12", "[10", "9]"]),
            boss(100),
            key_end(2200, 2290, 12, ms),
        ])[0]
    timed = run(1_600_000)
    assert (timed.label, timed.result, timed.time_limit) == ("+12 Timed", "completed", 2040)
    over = run(2_046_000)   # 34m06s: by 6 seconds, as on 10-14
    assert (over.label, over.result) == ("+12 Over time", "completed")
    # A dungeon not in the table (a new season's, before an update): "Completed" as before.
    unknown = find_runs([zone(0, 2290, "New Dungeon", 8), key_start(10, 2290, "New Dungeon", 12),
                         boss(100), key_end(1700, 2290, 12, 1_600_000)])[0]
    assert (unknown.label, unknown.time_limit) == ("+12 Completed", None)
