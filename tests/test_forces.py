from linux_wow_recorder import forces, keytimers


def test_every_season_dungeon_has_counts():
    season = [588, 586, 249, 587, 399, 250, 584, 585]   # Midnight Season 2
    assert all(c in keytimers.TIME_LIMITS and c in forces.COUNTS for c in season)


def test_npc_id_from_a_guid():
    assert forces.npc_id("Creature-0-3767-2290-17123-164567-00004A2F1D") == 164567
    assert forces.npc_id("Vehicle-0-3767-2290-17123-164568-00004A2F1D") == 164568
    assert forces.npc_id("Player-1-AAA") is None
    assert forces.npc_id("Pet-0-3767-2290-17123-165189-0102A1B2C3") is None


def test_forces_and_percent():
    assert forces.forces(588, 261573) == 30            # Ascendant Serpent, Altar of Fangs
    assert forces.forces(588, 1) == 0                  # worth nothing
    assert forces.forces(12345, 261573) == 0           # not in the table
    assert forces.percent(588, 409) == 50.06           # of 817
    assert forces.percent(588, 900) == 100.0           # the bar stops at 100
    assert forces.percent(12345, 10) is None


def test_spawns_worth_more():
    assert forces.spawn_forces(250, 134599, 3700, 3392) == 12   # Stormcaller before Galvazzt
    assert forces.spawn_forces(250, 136076, 3707, 3407) == 30   # Nimbus, same pack
    assert forces.spawn_forces(250, 134599, 3681, 3375) is None   # the pack before: its NPC's 7
    assert forces.spawn_forces(250, 134691, 3700, 3392) is None   # not one of them
