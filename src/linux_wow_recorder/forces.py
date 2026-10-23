"""Enemy forces: how much of a Mythic+ dungeon's forces bar each mob gives, to show how far
along a key was at each boss kill.

The combat log has the deaths but not the bar, so the counts are kept here and updated when a
season starts (see docs/development.md, "A new M+ season"). They're from Mythic Dungeon Tools
(https://github.com/Nnoggie/MythicDungeonTools, by Nnoggie and its contributors), with thanks.
"""

# challengeModeID -> (forces the bar needs, {NPC ID: forces}). Mobs worth nothing are left out.
# A few of Temple of Sethraliss's spawns are worth more than their NPC's count; the log can't
# tell spawns apart, so those count as the NPC does.
COUNTS: dict[int, tuple[int, dict[int, int]]] = {
    # Midnight Season 2 (from 2026-08-18; MDT d673bb141e)
    588: (817, {   # Altar of Fangs
        261550: 1,   # Venom Leech
        261552: 5,   # Bloodletter
        261553: 5,   # Ravenous Descendant
        261554: 25,   # Twinfang Harrower
        261557: 7,   # High Evolutionist
        261560: 7,   # Primal Serpent
        261573: 30,   # Ascendant Serpent
        262011: 25,   # Rattling Writhe
        263109: 25,   # Ula'tek's Chosen
        263112: 1,   # Living Venom
        270306: 25,   # Ritual Chieftain
        271453: 5,   # Blade of the Altar
    }),
    586: (729, {   # Den of Nalorakk
        241808: 8,   # Territorial Matriarch
        241813: 5,   # Thornclaw Gatherer
        241814: 7,   # Earthwhisper Tender
        241816: 7,   # Keen-Eyed Striker
        241869: 28,   # Avatar of Determination
        241872: 9,   # Frigid Mauler
        241874: 5,   # Frostfang
        241876: 7,   # Glacial Revenant
        241911: 7,   # Terra Rumbler
        244889: 35,   # Loa Speaker Nanea
        245139: 7,   # Stormbound Mystic
        245143: 5,   # Ruthless Totemcaller
        245145: 6,   # Bonded Beasttamer
        245146: 25,   # Grizzled Warbringer
        245190: 5,   # Loyal Saberfang
        245752: 7,   # Keen-Eyed Striker
        245855: 25,   # Spirit of Hunger
        250478: 50,   # The Winter Squall
    }),
    249: (608, {   # Kings' Rest
        133935: 22,   # Animated Guardian
        134157: 5,   # Umbral Warrior
        134158: 25,   # Shadow-Borne Champion
        134174: 20,   # Risen Hexer
        134251: 10,   # Seneschal M'bara
        134331: 25,   # King Rahu'ai
        134739: 25,   # Purification Construct
        135167: 22,   # Royal Berserker
        135192: 5,   # Honored Raptor
        135204: 7,   # Phantom Hex Priest
        135231: 25,   # Ghostly Brute
        135239: 7,   # Spectral Shaman
        137473: 10,   # Guard Captain Atu
        137474: 25,   # King Timalji
        137478: 25,   # Queen Wasi
        137484: 25,   # King A'akul
        137485: 7,   # Bloodsworn Assassin
        137486: 25,   # Queen Patlaa
        137487: 10,   # Skeletal Hunting Raptor
        137969: 15,   # Interment Construct
        137989: 1,   # Embalming Fluid
        138489: 30,   # Shadow of Zul
        270502: 7,   # Half-Finished Mummy
        273050: 7,   # Half-Finished Mummy, as logged on 2026-10-22 (not in MDT)
    }),
    587: (655, {   # Murder Row
        234849: 2,   # Unleashed Imp
        235257: 1,   # Demon Fly
        235261: 5,   # Trained Felhunter
        235265: 25,   # Corrupted Warlock
        235267: 5,   # Wrathguard Flayer
        235268: 7,   # Fel Invoker
        235322: 35,   # Defiled Golem
        235465: 25,   # Shivan Punisher
        236071: 25,   # Bribed Guard
        236073: 3,   # Row Hooligan
        236082: 6,   # Seductive Sayaad
        236084: 7,   # Felonious Mage
        236085: 1,   # Felwyrm
        236091: 3,   # Street Sneak
        236893: 2,   # Warehouse Worker
        236897: 7,   # Keen Taskmaster
        236902: 12,   # Massive Felwyrm
        236905: 30,   # Felmaster Lucsei
        252529: 35,   # Bribed Captain
        255604: 6,   # Seductive Sayaad
    }),
    399: (551, {   # Ruby Life Pools
        187897: 30,   # Defier Draghar
        187969: 5,   # Deepstone Earthshaper
        188011: 5,   # Earthbound Guardian
        188067: 7,   # Flashfrost Chillweaver
        188244: 25,   # Primal Juggernaut
        190034: 25,   # Blazebound Destroyer
        190206: 7,   # Ashseer Flamelasher
        190207: 7,   # Primalist Cinderweaver
        195119: 10,   # Ruinous Stormbringer
        197535: 30,   # High Channeler Ryvati
        197697: 40,   # Flamegullet
        197698: 48,   # Thunderhead
        197982: 5,   # Storm Warrior
        198047: 25,   # Tempest Channeler
    }),
    250: (687, {   # Temple of Sethraliss
        134364: 7,   # Faithless Subjugator
        134599: 7,   # Imbued Stormcaller
        134600: 7,   # Sandswept Hunter
        134602: 7,   # Shrouded Fang
        134616: 5,   # Barbed Krolusk
        134629: 25,   # Sand-Sworn Rider
        134686: 16,   # Krolusk Matriarch
        134691: 5,   # Static Anomaly
        134990: 7,   # Storm Adept
        134991: 25,   # Sandfury Stonefist
        135007: 25,   # Orb Watcher
        135562: 7,   # Poisonous Viper
        135846: 5,   # Lightning Serpent
        136076: 25,   # Agitated Nimbus
        136250: 25,   # Twisted Hexxer
        139110: 5,   # Spark Channeler
        139422: 7,   # Dutiful Tamer
        139425: 7,   # Brood Tender
        265057: 5,   # Spark Channeler
        268317: 5,   # Faithless Tormentor
        269227: 5,   # Temple Disruptor
    }),
    584: (686, {   # The Blinding Vale
        245336: 7,   # Radiant Spellsower
        245339: 6,   # Underbrush Stalker
        245345: 7,   # Lightgorged Lasher
        245346: 20,   # Virid Grovekeeper
        245410: 1,   # Lasher
        245460: 7,   # Leafy Grovecrawler
        245473: 5,   # Thorny Saptor
        245484: 7,   # Lightfeather Petalwing
        245513: 25,   # Overgrown Hydra
        245527: 1,   # Spineshield Beetle
        246871: 22,   # Luminous Thornmaw
        249756: 60,   # Potatoad Matriarch
        254850: 25,   # Sporeblight Belcher
    }),
    585: (738, {   # Voidscar Arena
        238883: 7,   # Dominated Brawler
        241496: 7,   # Enthralled Shaman
        243736: 1,   # Blistercreep
        243766: 7,   # Kilivore Screamer
        243835: 5,   # Savage Shredclaw
        243983: 4,   # Sycophantic Tarasek
        243985: 5,   # Longtooth Tuskarr
        243988: 4,   # Feral Saberon
        243996: 4,   # Lost Sethrak
        244260: 25,   # Chitigoth
        244309: 25,   # Brutok
        244708: 7,   # Voidminder
        245950: 65,   # Watchful Harrower
        249461: 5,   # Abducted Drakonid
        249590: 8,   # Angry Krolusk
        249603: 5,   # Protective Turtle
        249608: 5,   # Raging Raptor
        252053: 25,   # Brutal Overseer
        252072: 25,   # Voidtouched Magi
        252508: 1,   # Scavenging Siphoid
        263228: 25,   # Agitated Voidscythe
        267545: 40,   # Aegyra the Unyielding
        267546: 40,   # Raj'kess the Spellstorm
        268184: 30,   # Devouring Brutalizer
    }),
}


def npc_id(guid: str) -> int | None:
    """"Creature-0-3767-2290-17123-164567-00004A2F1D" -> 164567; None for players and pets."""
    parts = guid.split("-")
    if parts[0] not in ("Creature", "Vehicle") or len(parts) < 7:
        return None
    return int(parts[5])


def forces(challenge: int | None, npc: int | None) -> int:
    """What killing the NPC adds to the dungeon's bar; 0 for a dungeon not in the table."""
    return COUNTS[challenge][1].get(npc, 0) if challenge in COUNTS else 0


def percent(challenge: int | None, count: int) -> float | None:
    """The bar's percent at that count, as the game shows it (it stops at 100); None for a
    dungeon not in the table (a new season's, before an update)."""
    if challenge not in COUNTS:
        return None
    return round(min(100.0, count * 100 / COUNTS[challenge][0]), 2)
