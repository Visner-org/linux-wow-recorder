"""Major cooldowns: the big damage, healing and defensive spells, for the clips window's "Major
cooldowns only" tick. By spell ID, as the combat log has them (Midnight season 1); the IDs were
checked against real logs, except those marked "not seen" (from earlier expansions, kept in case
they still exist). A new expansion renames and replaces some: see docs/development.md."""

OFFENSIVE, HEALING, DEFENSIVE = "damage", "healing", "defensive"

MAJOR: dict[int, str] = {
    # ── Damage ──
    # Group-wide
    2825: OFFENSIVE,     # Bloodlust
    32182: OFFENSIVE,    # Heroism
    80353: OFFENSIVE,    # Time Warp
    272678: OFFENSIVE,   # Primal Rage
    264667: OFFENSIVE,   # Primal Rage (not seen)
    390386: OFFENSIVE,   # Fury of the Aspects
    466904: OFFENSIVE,   # Harrier's Cry
    444257: OFFENSIVE,   # Thunderous Drums
    1243972: OFFENSIVE,  # Void-touched Drums
    10060: OFFENSIVE,    # Power Infusion
    # Death Knight
    51271: OFFENSIVE,    # Pillar of Frost
    1249658: OFFENSIVE,  # Breath of Sindragosa
    279302: OFFENSIVE,   # Frostwyrm's Fury
    1265384: OFFENSIVE,  # Frostwyrm's Fury
    42650: OFFENSIVE,    # Army of the Dead
    1263566: OFFENSIVE,  # Abomination Limb
    1263569: OFFENSIVE,  # Abomination Limb
    # Demon Hunter
    200166: OFFENSIVE,   # Metamorphosis (Havoc)
    370965: OFFENSIVE,   # The Hunt
    1246167: OFFENSIVE,  # The Hunt
    1246169: OFFENSIVE,  # The Hunt
    # Druid
    102560: OFFENSIVE,   # Incarnation: Chosen of Elune
    194223: OFFENSIVE,   # Celestial Alignment (not seen)
    383410: OFFENSIVE,   # Celestial Alignment (not seen)
    102543: OFFENSIVE,   # Incarnation: Avatar of Ashamane
    106951: OFFENSIVE,   # Berserk
    391528: OFFENSIVE,   # Convoke the Spirits
    # Evoker
    375087: OFFENSIVE,   # Dragonrage
    403631: OFFENSIVE,   # Breath of Eons
    # Hunter
    288613: OFFENSIVE,   # Trueshot
    359844: OFFENSIVE,   # Call of the Wild (not seen)
    # Mage
    365350: OFFENSIVE,   # Arcane Surge
    190319: OFFENSIVE,   # Combustion
    12472: OFFENSIVE,    # Icy Veins (not seen)
    # Monk
    123904: OFFENSIVE,   # Invoke Xuen, the White Tiger
    1249625: OFFENSIVE,  # Zenith
    137639: OFFENSIVE,   # Storm, Earth, and Fire (not seen)
    132578: OFFENSIVE,   # Invoke Niuzao, the Black Ox
    # Paladin
    31884: OFFENSIVE,    # Avenging Wrath
    454351: OFFENSIVE,   # Avenging Wrath
    231895: OFFENSIVE,   # Crusade (not seen)
    389539: OFFENSIVE,   # Sentinel
    # Priest
    228260: OFFENSIVE,   # Voidform
    391109: OFFENSIVE,   # Dark Ascension (not seen)
    # Rogue
    360194: OFFENSIVE,   # Deathmark
    13750: OFFENSIVE,    # Adrenaline Rush
    121471: OFFENSIVE,   # Shadow Blades
    # Shaman
    114050: OFFENSIVE,   # Ascendance (Elemental)
    114051: OFFENSIVE,   # Ascendance (Enhancement)
    192249: OFFENSIVE,   # Storm Elemental (not seen)
    198067: OFFENSIVE,   # Fire Elemental (not seen)
    # Warlock
    265187: OFFENSIVE,   # Summon Demonic Tyrant
    1122: OFFENSIVE,     # Summon Infernal
    205180: OFFENSIVE,   # Summon Darkglare
    442726: OFFENSIVE,   # Malevolence
    # Warrior
    107574: OFFENSIVE,   # Avatar
    1719: OFFENSIVE,     # Recklessness
    446035: OFFENSIVE,   # Bladestorm

    # ── Healing ──
    31821: HEALING,      # Aura Mastery
    633: HEALING,        # Lay on Hands
    471195: HEALING,     # Lay on Hands
    64843: HEALING,      # Divine Hymn
    64844: HEALING,      # Divine Hymn
    200183: HEALING,     # Apotheosis
    62618: HEALING,      # Power Word: Barrier (not seen)
    472433: HEALING,     # Evangelism
    421453: HEALING,     # Ultimate Penitence
    740: HEALING,        # Tranquility
    157982: HEALING,     # Tranquility
    33891: HEALING,      # Incarnation: Tree of Life (not seen)
    108280: HEALING,     # Healing Tide Totem
    98008: HEALING,      # Spirit Link Totem
    114052: HEALING,     # Ascendance (Restoration)
    115310: HEALING,     # Revival
    325197: HEALING,     # Invoke Chi-Ji, the Red Crane
    322118: HEALING,     # Invoke Yu'lon, the Jade Serpent (not seen)
    443028: HEALING,     # Celestial Conduit
    363534: HEALING,     # Rewind
    359816: HEALING,     # Dream Flight (not seen)

    # ── Defensive: group-wide, your own, and those cast on someone else ──
    97462: DEFENSIVE,    # Rallying Cry
    196718: DEFENSIVE,   # Darkness
    51052: DEFENSIVE,    # Anti-Magic Zone
    374227: DEFENSIVE,   # Zephyr
    48792: DEFENSIVE,    # Icebound Fortitude
    55233: DEFENSIVE,    # Vampiric Blood
    49028: DEFENSIVE,    # Dancing Rune Weapon
    48707: DEFENSIVE,    # Anti-Magic Shell
    49039: DEFENSIVE,    # Lichborne
    48743: DEFENSIVE,    # Death Pact
    187827: DEFENSIVE,   # Metamorphosis (Vengeance)
    198589: DEFENSIVE,   # Blur
    204021: DEFENSIVE,   # Fiery Brand
    196555: DEFENSIVE,   # Netherwalk (not seen)
    61336: DEFENSIVE,    # Survival Instincts
    102558: DEFENSIVE,   # Incarnation: Guardian of Ursoc
    102342: DEFENSIVE,   # Ironbark
    363916: DEFENSIVE,   # Obsidian Scales
    357170: DEFENSIVE,   # Time Dilation
    186265: DEFENSIVE,   # Aspect of the Turtle
    109304: DEFENSIVE,   # Exhilaration
    264735: DEFENSIVE,   # Survival of the Fittest
    45438: DEFENSIVE,    # Ice Block
    342245: DEFENSIVE,   # Alter Time
    55342: DEFENSIVE,    # Mirror Image
    115203: DEFENSIVE,   # Fortifying Brew
    122470: DEFENSIVE,   # Touch of Karma
    116849: DEFENSIVE,   # Life Cocoon
    115176: DEFENSIVE,   # Zen Meditation (not seen)
    642: DEFENSIVE,      # Divine Shield
    31850: DEFENSIVE,    # Ardent Defender
    86659: DEFENSIVE,    # Guardian of Ancient Kings
    212641: DEFENSIVE,   # Guardian of Ancient Kings
    498: DEFENSIVE,      # Divine Protection
    403876: DEFENSIVE,   # Divine Protection
    6940: DEFENSIVE,     # Blessing of Sacrifice
    1022: DEFENSIVE,     # Blessing of Protection
    204018: DEFENSIVE,   # Blessing of Spellwarding
    47788: DEFENSIVE,    # Guardian Spirit
    33206: DEFENSIVE,    # Pain Suppression
    47585: DEFENSIVE,    # Dispersion
    19236: DEFENSIVE,    # Desperate Prayer
    31224: DEFENSIVE,    # Cloak of Shadows
    5277: DEFENSIVE,     # Evasion
    108271: DEFENSIVE,   # Astral Shift
    104773: DEFENSIVE,   # Unending Resolve
    108416: DEFENSIVE,   # Dark Pact
    871: DEFENSIVE,      # Shield Wall
    12975: DEFENSIVE,    # Last Stand (not seen)
    118038: DEFENSIVE,   # Die by the Sword
    184364: DEFENSIVE,   # Enraged Regeneration
}


def kind(spell: int) -> str | None:
    """"damage", "healing" or "defensive" for a major cooldown, None for anything else."""
    return MAJOR.get(spell)
