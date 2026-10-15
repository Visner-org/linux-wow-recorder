from linux_wow_recorder import keytimers


def test_time_limits_by_id_or_name():
    assert keytimers.time_limit(249) == 1980
    assert keytimers.time_limit(dungeon="Kings' Rest") == 1980
    assert keytimers.time_limit(12345, "Not A Dungeon") is None


def test_upgrade_levels():
    assert [keytimers.upgrade(t, 1800) for t in (1081, 1080, 1441, 1440, 1800, 1801)] == \
        [2, 3, 1, 2, 1, 0]
