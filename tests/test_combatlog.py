from linux_wow_recorder.combatlog import find_runs, parse_log_time, read_log_events

T0 = 1_800_000_000.0


def zone(t, inst, name, diff):
    return (T0 + t, "ZONE_CHANGE", [str(inst), name, str(diff)])


def key_start(t, inst, name, level):
    return (T0 + t, "CHALLENGE_MODE_START", [name, str(inst), "375", str(level), "[10", "9]"])


def key_end(t, inst, level, ms):
    return (T0 + t, "CHALLENGE_MODE_END", [str(inst), "1", str(level), str(ms)])


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
        2026, 10, 8, 13, 33, 12, tzinfo=datetime.timezone.utc).timestamp()
    assert parse_log_time("2/30/2026 19:03:12.000") is None
