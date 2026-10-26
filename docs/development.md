# Development

```bash
uv sync --extra gui        # the venv, with the desktop app's Qt
uv run pytest              # the tests (the ones needing ffmpeg/ffprobe are skipped without)
uv run --extra gui --group gui-test pytest   # also the window tests (pytest-qt), offscreen
uv run ruff check src tests
uv run linux-wow-recorder status
uv run linux-wow-recorder-gui
```

`tests/conftest.py` points the XDG folders at a throwaway folder before the app is imported,
so tests never read or write the real config, state, logs or autostart file. The window tests
(`tests/test_gui_windows.py`) are skipped without pytest-qt, which won't load without Qt; that's
why it's a group of its own and not in `dev`.

CI (`.github/workflows/ci.yml`) runs ruff, the core tests on Python 3.11 to 3.13 without Qt,
and the window tests offscreen on Ubuntu 24.04 (Qt needs `libegl1 libgl1 libxkbcommon0
libfontconfig1 libdbus-1-3 libglib2.0-0t64 libpulse0` there).

## Design rules

- **One computer.** The app assumes it runs on a single computer, and everything it does stays
  on that computer. It doesn't upload, sync, copy to other machines, mount or connect drives, or
  talk to servers (the webhook messages aside). Anything beyond that is the user's own business,
  and `after_clips` is the one place for it: the app runs that command and goes no further. A
  folder setting is a local path; how files get there (a mounted share, a sync tool) isn't the
  app's concern.
- **The core has no dependencies.** Everything outside `gui/` uses only the standard library and
  the programs it runs (gpu-screen-recorder, ffmpeg, ffprobe, grep). A player who wants the
  command line only installs a few hundred KB.
- **The service is the only thing that records.** The desktop app is a client: it reads the
  status file, the config and the clips folder, and controls the service through systemd.
  Closing it never stops a recording.
- **The recorder is a separate program.** gpu-screen-recorder runs as its own process (in its own
  process group), and SIGINT makes it finish the file. libobs isn't embedded.
- **Nothing is guessed from the system except the WoW process.** The user points the app at the
  combat log folder and the output folder.
- **The app writes and deletes only in its three output subfolders**, its config and state
  folders, and the service and .desktop files. The clip browser's delete moves the clip and its
  `.json` to the desktop's trash (`QFile.moveToTrash`) and deletes for good only when there's
  no trash and the user agrees. The `other_folders` it lists clips from are read only: no
  pictures made there, no Keep, no retention; only that delete, when the user asks for it.
- **Clips are never re-encoded.** ffmpeg stream-copies, so cutting takes seconds and loses
  nothing.

## How a session goes

```
daemon.run() loop, every poll_interval seconds
  detect.find_wow()                   scans /proc for Wow*.exe in a process's command line
  WoW found → start_session()
      recorder.start()                gpu-screen-recorder → <output>/sessions/<time>.mkv
      status.json: recording
  WoW still running → check_session()
      check_pause()                   control.json asks to pause this session: stop the
                                      recorder (status.json: paused), or record again
      check_capture()                 the recorder says its window is gone ("error" from
                                      pipewire), or WoW is a new process with portal capture:
                                      stop it (the recording so far is kept) and start a new
                                      one when WoW is back; say so only if WoW's still there
                                      after 15 s (quitting WoW drops the window first)
      free space every 60 s           warn / stop below the limits
      recorder died → restart it      up to 3 times per session
  WoW gone twice in a row → end_session()
      recorder.stop()                 SIGINT, wait for the file to be saved
      status.json: waiting
      start_cutting()                 finish() in the cutter thread; the loop goes on, so WoW
                                      started again meanwhile is recorded
      finish()
        clips.process_pending()       every recording in sessions/, oldest first, except the
                                      one being written; stops between recordings on SIGTERM:
          recording_span()              start = file mtime − ffprobe duration
          combat_logs_between()         log files overlapping that span
          combatlog.read_log_events()   grep keeps only the needed events
          combatlog.find_runs()         runs, with bosses, deaths and lusts
          combatlog.read_casts()        the group's casts (grep, once per recording)
          cut_clip()                    ffmpeg -ss/-t stream copy → clips/<name>.mp4 + .json
          → processed/ + .json          the recording's details, for Full sessions (stays in
                                        sessions/ if a cut failed: retried next time)
        report()                      Discord/Fluxer webhooks
        run_hook() in a thread        after_clips
        cleanup()                     retention: processed/ and clips/, never sessions/
        warn_stale()                  un-cut recordings past retention: logged, clip_failed once
      status.json: processing while cutting, unless a new session is recording
```

**Updates:** the service and the tray app each keep a `service.UpdateWatch`, which compares the
installed package's version (its metadata on disk) with the one installed when they started, once a
minute; a new one counts once two checks in a row see it, so a package still being installed isn't
caught halfway. The service restarts only with no session, no cutter and no `after_clips` thread
running; the tray app only with none of its windows open. Both `os.execv` themselves (the same
process ID, so systemd and the autostart see nothing), the tray app after releasing its lock and
local server. Nothing runs from the package's install scripts: they run as root, outside the
user's session.

On start, the service stops a recorder left running by a run that crashed
(`recorder.adopt_leftover`), and cuts whatever an earlier run left in `sessions/`.

One cutter thread at a time: a session that ends while it's cutting makes it go round once
more. `Daemon._lock` covers `session` and the cutter's fields, so the cutter never writes
"processing" or "waiting" over a new session's "recording". On SIGTERM, the service waits up to
10 s for the recording being cut (a cut cut short is redone); the rest stay in `sessions/` for
next time.

## Finding runs in the combat log

`combatlog.find_runs` walks the events in order:

- **Keys**: `CHALLENGE_MODE_START` opens one and `CHALLENGE_MODE_END` closes it. END's fields
  are `instanceID, success, keystoneLevel, totalTime`. An END whose success field is 0 means the
  key was abandoned, whatever the level and time say. A START for the key that's already open
  (same instance, keystone and level, no END between) is the same key resumed: WoW announces it
  again when it comes back after a crash or a disconnect; the run then ends when the player left the
  instance. A START while a key is open abandons the old one.
- **Dungeons without a key**: a `ZONE_CHANGE` into an instance whose difficulty is in
  `DUNGEON_DIFFICULTIES` opens a visit. Leaving it, then being somewhere else for 10 minutes,
  closes it. Coming back (after a release or a disconnect) continues it. A visit counts only if
  a boss was pulled, and isn't used when a key ran in it.
- **Inside a run**, counted only while the player is in its instance:
  - `ENCOUNTER_START`/`END`: boss pulls, kills and wipes. An END closes the open pull with its
    encounter ID (the first field), not its name.
  - `UNIT_DIED` with a `Player-` GUID: deaths.
  - Lusts: `SPELL_AURA_APPLIED` of a lust debuff (Sated, Exhaustion, Temporal Displacement,
    Fatigued, Exhaustion (Evoker)) on a player who isn't sated. Applications within 30 seconds
    are one lust. The haste buff that leaves that debuff names it and its caster (or the
    drummer). A buff alone is never a lust: in real logs, an Evoker talent gives the caster a
    short Fury of the Aspects every ~100 s, and an extended lust re-applies the buff. The game
    takes the debuffs off after a boss wipe and when a key starts, so `SPELL_AURA_REMOVED` of a
    debuff is read too; a debuff applied again while the player still has it is a refresh.
- Runs shorter than `min_run_minutes` are dropped, except completed keys.
- **Casts** (`read_casts`, a grep of its own): `SPELL_CAST_SUCCESS` lines whose source is a
  `Player-` GUID with mine, party or raid affiliation in its flags' last digit (0x511 is the
  player who logged, 0x512 a party member, 0x514 raid; outsiders, 0x548 and the like, are
  left out); pets aren't `Player-`. A `SPELL_CAST_START` of the same spell up to 10 s before
  gives a hard cast's cast time. About 5000 casts in a 20-minute key, ~90 KB in the clip's
  `.json` (the casts list is written on one line; `write_sidecar`).

grep does the filtering, because logs reach 1 GB. The lust part of the pattern matches only the
listed spell IDs, so the busy `SPELL_AURA_APPLIED` lines cost nothing: about 0.6 s for 540 MB.

Timestamps are `M/D/YYYY H:M:S.ms±offset`; the year and the offset may be missing (Classic-based
clients, older logs). Without an offset, it's local time.

### Real combat logs in the tests

`tests/fixtures/logs/` holds three real retail sessions (September and October 2026, 25 keys),
cut down to the lines the app reads and with every player's and pet's name and GUID replaced.
`tests/fixtures/sanitize.py` makes them from a log and fails if an original name or GUID is
left; read the result before committing it anyway. What they showed:

- `UNIT_DIED`'s last field (`unconsciousOnDeath`) is 1 for about a third of party members'
  deaths, and 92% of those players cast again within 5 seconds (24% of the others): Feign Death
  and the like, so not counting them is right. It's never 1 for the player writing the log.
- Each key starts with an all-zeros `CHALLENGE_MODE_END` (the keystone going in), and END has
  two more fields after `totalTime`.
- Lust buffs come back without a debuff (an extended lust, an Evoker talent's self-buff), and
  the debuffs come off after a boss wipe; hence lusts are found by their debuff.

### Adding a dungeon difficulty

When the log enters an instance with a difficulty the app doesn't know, the service logs
`Not a dungeon this app knows: <name> (difficulty N)`. If it's a 5-player dungeon, add `N` to
`DUNGEON_DIFFICULTIES` with its name, and add a test.

### A new expansion: major cooldowns

`cooldowns.py` lists the big damage, healing and defensive spells by spell ID, for the clips
window's "Major cooldowns only". An expansion replaces some. Count what the group cast in real
logs and look for new IDs of the same names, and new cooldowns:

```bash
grep -ahE '  SPELL_CAST_SUCCESS,Player-[^,]*,"[^"]*",0x[0-9a-fA-F]*[1-7],' WoWCombatLog-*.txt \
  | awk -F, '{print $10 "\t" $11}' | sort | uniq -c | sort -rn
```

Spells with a short cooldown (cast hundreds of times an hour) aren't major. Mark an ID kept
from an earlier expansion that wasn't in the logs "(not seen)".

### A new M+ season

The combat log has a key's time but not the dungeon's limit, so `keytimers.py` lists them, by
challengeModeID (`CHALLENGE_MODE_START`'s third field). When a season starts, add its dungeons
from Raider.IO's static data (expansion 11 is Midnight) and drop seasons older than the last:

```bash
curl -s 'https://raider.io/api/v1/mythic-plus/static-data?expansion_id=11' > static-data.json
python3 - static-data.json <<'EOF'
import json, sys
season = json.load(open(sys.argv[1]))["seasons"][0]
print("    #", season["name"])
for d in season["dungeons"]:
    print(f'    {d["challenge_mode_id"]}: ({json.dumps(d["name"])}, {d["keystone_timer_seconds"]}),')
EOF
```

Check a few against real runs (a timed key raises the rating at the end of `CHALLENGE_MODE_END`;
one over time doesn't). Until then, the new dungeons' keys say "Completed". Only this
development step uses Raider.IO; the app doesn't.

`forces.py` has each season dungeon's enemy forces: what each NPC ID adds to the bar and the
bar's total. They're from Mythic Dungeon Tools' dungeon files (`Midnight/<Dungeon>.lua` in
github.com/Nnoggie/MythicDungeonTools): each enemy's `["id"]` and `["count"]` (leave out the 0s
and the per-spawn counts under `["clones"]`) and `dungeonTotalCount`'s `normal`; `mapInfo`'s
`mapID` is the challengeModeID. Note the MDT commit in the season's comment. Then check against
real runs: a timed key's forces should reach 100% by its last boss. A mob killed that the table
doesn't know (often an NPC ID that changed) shows up as a creature `UNIT_DIED` that adds nothing;
add it with a comment saying it was logged, not from MDT.

Some kills have no death line: a few mobs never get one (Temple's Static Anomalies, Kings'
Rest's Shadow of Zul), and some die seconds after their killing blow (Ruby Life Pools'
Flamelashers and Destroyers), out of range if the logger died meanwhile. So a killing blow
(overkill in a damage line) counts too. `SPAWNS` in `forces.py` has MDT's clones worth more
than their NPC, placed by where they died in real keys. Both need advanced combat logging
(`tools/boss_forces_check.py` shows a boss kill that's still off).

To check the table against the game itself: link `tools/ForcesLog` into WoW's
`Interface/AddOns`, run keys with combat logging on, `/reload` or log out (that's when WoW saves
the addon's data), then `uv run python tools/forces_check.py`. It matches each rise of the bar to
the mobs that died just before it and lists the NPCs whose count differs from the table.

### Adding a lust

Add the buff's spell ID to `LUST_BUFFS` with its name and the debuff it leaves. If that debuff is
new, add it to `LUST_DEBUFFS`; without it, the lust isn't found at all.

### The WoW addon

`src/linux_wow_recorder/addon/LinuxWoWRecorder/` is shipped in the package and copied into
`<WoW>/_retail_/Interface/AddOns/` by `addon.install` (Settings or `install-addon`). It turns
combat logging on in dungeons and raids at the moments zone-only loggers miss (Raider.IO checks
on zone changes only, so a dungeon entered at Heroic and then keyed had no log for its first
72 s on 10-26). It never turns logging off. It's opt-in: only the user installs it (Settings
asks first), since it leaves logging on and changes a CVar; `refresh` only updates a copy
that's already there. Its `## Version:` is what `addon.status` compares:
bump it with any change to the addon, and the service (`addon.refresh` at start) updates
installed copies. Bump `## Interface:` with each WoW patch, or WoW lists it as out of date.

A completed key whose log has no START is timed from its END (`key_end`). Its timer began at
END minus the in-game time plus 15 s per group death (`DEATH_PENALTY`, measured 15 to 16 s; a
hunter's Feign Death has no penalty and isn't counted). If the log began more than 20 s after
that (`LATE_LOG_SECONDS`), kills may be missing: `Run.log_late` is set and its forces dropped.

## Finding WoW

`detect.wow_flavor` looks for `Wow.exe`, `WowT.exe`, `WowB.exe`, `WowClassic.exe` and so on in
each process's command line; that covers every launcher, because they all run the game through
Wine. An unknown `Wow*.exe` counts only inside a game folder (`_forever_`), and is named after
the folder ("Forever"). Helpers next to the game (`WowVoiceProxy.exe`, ...) and the Battle.net
launcher, which names the game in its arguments, are skipped. Add a new client's .exe to
`FLAVORS` once its name is known.

## The code

| File | Does |
|---|---|
| `detect.py` | finds a running WoW client in /proc |
| `recorder.py` | starts and stops gpu-screen-recorder |
| `combatlog.py` | finds dungeon runs, bosses, deaths and lusts in the combat log (`_RunFinder`: one method per event), and the group's casts (`read_casts`) |
| `cooldowns.py` | the major cooldowns, by spell ID and kind (damage, healing, defensive) |
| `clips.py` | cuts recordings into clips with ffmpeg, writes the `.json` details, retention cleanup |
| `notify.py` | Discord or Fluxer webhooks (the same JSON) |
| `logs.py` | log files, kept on this computer and cleaned; the report file ([Logging](#logging)) |
| `daemon.py` | the main loop |
| `state.py` | `status.json`, and `control.json` (pause requests to the service) |
| `config.py` | the config file: defaults, loading, checking, writing it with comments |
| `cli.py` | the commands and `setup` |
| `service.py` | the systemd user service |
| `gui/__init__.py` | `linux-wow-recorder-gui`: checks for PySide6, `--autostart` |
| `gui/model.py` | what the windows show, without Qt, so the tests need no display |
| `gui/tray.py` | the tray icon (drawn in code) and its menu |
| `gui/settings.py` | the settings window |
| `gui/browser.py` | the clip browser: cards or table, player, timeline, events by boss |
| `gui/theme.py` | colours for light and dark themes (timeline marks, status text) |
| `gui/desktop.py` | the autostart and app menu `.desktop` files |

### Desktop app notes

- **Tray menu**: on KDE and GNOME, the menu is sent over D-Bus, so it can hold only plain actions,
  not widgets. Status lines are disabled actions, rebuilt only when their text changes.
- **Updates**: the tray reads `status.json` every second (a small file). The service's state
  comes from systemd's `PropertiesChanged` signals on the session bus (`tray.ServiceWatcher`,
  after `Subscribe`); `systemctl is-active` runs once a minute as a check, or every 5 seconds
  without the bus. Whether recordings wait in `sessions/` is worked out again only when the
  status's state or last session changes, after an action, or every 30 seconds.
- **Package**: the `gui` extra is `PySide6-Essentials` + `PySide6-Addons`. Addons is for
  QtMultimedia; the full `PySide6` package would also bring Chromium (QtWebEngine), which isn't
  used.
- **Pause**: the tray's "Pause recording this session" (or `pause`/`resume`) writes
  `control.json` beside `status.json`, naming the session it's for; the service reads it every
  poll and stops or restarts its own recorder. A new session clears it, so the next WoW records.
  The app stays a client: it asks, the service records or doesn't.
- **Cut the clips so far**: the same file, with the time it was asked. The service stops the
  recorder, starts a new recording and runs the cutter, which skips the file being written. It
  first reads the session's combat logs (`combatlog.run_in_progress`, every 30 s while waiting):
  inside a key, a dungeon run or a boss pull it waits, so a run isn't cut as abandoned and split.
  WoW writes the log in batches, often a minute or more after the events (measured 10-26: lines
  up to 75 s old, writes up to 33 s apart mid-fight), so it first waits for a line timestamped
  after the click (`combatlog.last_event_time`, at most 120 s): a key started just before it is
  in the file by then.
- **Setup checklist**: the clip browser shows `model.setup_checklist` (recorder, ffmpeg and co.,
  folders, combat logging, the service) at the top while anything on it isn't ticked.
- **Window and tray**: `run()` opens the window unless started with `--tray` (the autostart
  entry). Closing it hides it; `setQuitOnLastWindowClosed(False)` keeps the tray. One app per
  user: a lock file, plus a `QLocalServer` that a second launch tells to show the window. The
  app's desktop file name is `linux-wow-recorder-gui`, which is how Wayland taskbars find its
  icon (`gui/icon.svg`); the tray icon is drawn in code to show the state.
- **RPM**: `packaging/build-rpm.sh` builds the wheel with uv and wraps it with
  `packaging/linux-wow-recorder.spec`. It installs into the system Python and requires
  `python3-pyside6` and `/usr/bin/ffmpeg` from the distro, so it's tied to one Fedora release.
  `gpu-screen-recorder` (and `flatpak`) are only Recommends: Nobara has it, plain Fedora doesn't,
  and there the user installs the Flatpak; dnf installs weak dependencies where it can. Fedora's
  `ffmpeg-free` has OpenH264, which `hidechat` falls back to. Tested with `dnf install` in a
  clean `fedora:44` container. Flatpak was ruled out: the sandbox can't see the WoW process, run
  gpu-screen-recorder with its root helper, or install the systemd user service.
- **Arch package**: `packaging/aur/PKGBUILD` builds from the GitHub release tarball of the tag
  `pkgver` (tags have no `v`), runs the tests in `check()`, and installs the wheel, menu entry and
  icon. Arch's `pyside6` doesn't include QtMultimedia, so `qt6-multimedia` is a separate
  dependency; gpu-screen-recorder is in Arch's `extra` repo. Tested in an `archlinux` container
  with `makepkg -si`.
- **.deb**: `packaging/build-deb.sh` builds the wheel with uv, then puts the package together in
  an `ubuntu:26.04` container (podman or docker), so it builds on any distro. Inside,
  `packaging/deb/build.sh` installs the wheel with `installer` into Debian's own layout
  (`deb_system`: `/usr/lib/python3/dist-packages`, shared by every Python 3, so the package isn't
  tied to one Python version), adds the menu entry, icon and copyright file, and wraps it with
  `dpkg-deb`. `postinst` byte-compiles with `py3compile`, `prerm` cleans up with `py3clean`, as
  Debian's own Python packages do. It depends on the `python3-pyside6.*` modules the app imports
  (Ubuntu 25.10+, Debian 13; Ubuntu 24.04 has none) and recommends `flatpak`, because
  gpu-screen-recorder isn't in Ubuntu's or Debian's repos. Tested by installing it in clean
  `ubuntu:26.04` and `debian:13` containers and on an Ubuntu 26.04 desktop.
- **Running commands**: settings and the tray run `python -m linux_wow_recorder <command>`
  (test-recorder, process) instead of calling the functions, so a slow command never blocks
  the window.
- **Colours**: `gui/theme.py` picks each colour's light or dark value from the palette's window
  colour, and windows set them again on `QEvent.PaletteChange`. The timeline's kill, death and
  lust colours were checked as a set for colour-blind separation and contrast on Breeze's light
  and dark windows; text colours are 4.5:1 on both. Text stays in the palette's text colours;
  colour goes on marks beside it.
- **Clip browser**: cards (painted by `ClipCard`) or the table, remembered in `gui.ini` in the
  config folder. Missing pictures (clips cut before they existed) are made one at a time with
  `QProcess`. The timeline and the event list are worked out in `model.py` (`boss_spans`,
  `lust_spans`, `event_rows`), so they're tested without a display.
  Full sessions (`model.load_sessions`) are `ClipInfo`s too, so the cards, table, player and
  timeline serve both; a recording without a `.json` (cut by 0.5.0 or earlier) gets its runs
  from its clips' details, moved by `shift_run`.
- **Settings window**: pages in a sidebar (Folders and capture, Sound, Clips and storage,
  Notifications, Advanced, About: the version, links and the GPL from the
  installed package's `licenses/LICENSE`, via `model.license_text`); with no config file, the same pieces are a three-step setup
  (folders, what to record, a 5-second test). Each field has a check line under it
  (`model.field_checks`), and a page with a problem gets a ⚠ in the sidebar; an empty needed
  field turns red only after Next or Save. The service reads the config once, when it starts,
  so Save restarts it only if the form differs from the config file (or it should run and
  isn't); the two "at login" boxes alone don't.
- **Settings window, speed**: nothing slow runs while it opens or while you type. The capture list
  starts with "portal" and "screen" and is filled from `--list-capture-options` through a
  `QProcess` when the recorder answers. The combat log folder's hint waits for a 300 ms pause in
  typing and looks only a few folders deep (`model.newest_combat_log`).

## Logging

Rules, for every change:

1. **Logs never leave the computer.** No code that writes or reads logs uses the network
   (`logs.py` has a test for that). The only network traffic is the user's own webhooks.
2. **Webhook messages carry no log lines, error output or file paths**: they say what happened and
   point to the log on this computer (`daemon.SEE_LOG`). A test checks every `notifier.send` in
   `daemon.py`.
3. **No uploading, no crash reporting, no "send" button.** The report file is written where the
   user chooses; sharing it is up to them.
4. **Nothing identifying**: `logs.clean` replaces webhook links, the home folder, the computer's
   name, the user name and IP addresses in every log line and in the report. Don't log other
   players' names from the combat log.
5. **Capped**: 1 MB per file with 4 old copies, 10 session logs, nothing older than 30 days.
6. **Plain text**, so the user can read exactly what a report contains.

`logs.setup(name)` is called once per process: `recorder` for `run` (the service), `app` for the
desktop app and the other commands. Uncaught errors in any thread are logged with their
traceback; `faulthandler` writes hard crashes to `crash.log`.

## Version numbers

`0.MINOR.PATCH` until the first stable release, then `MAJOR.MINOR.PATCH`
([semantic versioning](https://semver.org)). The version lives in `src/linux_wow_recorder/__init__.py`.

- **PATCH** (0.2.0 → 0.2.1): bug fixes only; nothing changes for someone who doesn't hit the bug.
- **MINOR** (0.2.x → 0.3.0): new features, changed defaults, or new config settings. Older config
  files keep working. The numbers aren't decimals: after 0.9.0 comes 0.10.0, so 1.0 never has to
  come early.
- **MAJOR** (1.0.0): the first release meant for anyone, after it has run for a while without
  problems. After 1.0, a change that breaks an existing config file or command means 2.0.

Each release gets an entry in the `%changelog` of `packaging/linux-wow-recorder.spec`, and the RPM's
`Release` stays 1 unless only the packaging changed (likewise the `.deb`'s revision:
`DEB_REVISION=2 ./packaging/build-deb.sh`). Build both (`build-rpm.sh`, then `build-deb.sh`,
which rebuilds the same wheel) and attach the RPM, the `.deb` and the wheel to the release. After the GitHub release (tag `0.5.0`, no
`v`), set `pkgver` in `packaging/aur/PKGBUILD` (and `pkgrel=1`), then in that folder
`updpkgsums && makepkg --printsrcinfo > .SRCINFO`; on an Arch-less machine, run both in an
`archlinux` container.

## Distro checks

Installs and setup were checked in October 2026 in libvirt VMs and podman containers. None of
them has a graphics card, so nothing was recorded; recording is only proven on Nobara 44.

| Distro | Route | How it was checked |
|---|---|---|
| Nobara 44 | RPM | daily use, recording |
| Fedora 44 | RPM | `dnf install` in a `fedora:44` container: gpu-screen-recorder is skipped (only Recommends), `ffmpeg-free` and PySide6 come in, the GUI modules load |
| Fedora Silverblue / Kinoite 44 | uv | their images (`quay.io/fedora/fedora-silverblue:44`, `-kinoite:44`) ship `ffmpeg-free`, git and Flatpak, no Flathub remote |
| CachyOS | PKGBUILD | VM (KDE, Wayland): `makepkg -si` from the GitHub tag, setup finished |
| Bazzite | uv + gsr Flatpak | VM (KDE, Wayland): setup finished; it made the menu entry and found the Flatpak |
| Ubuntu 26.04 | uv, then .deb | VM (GNOME, Wayland): both; switching from uv to the .deb found the stale-unit bug |
| Debian 13 | .deb | `debian:13` container: installs, the GUI modules load |
| Ubuntu 24.04 / Pop!_OS 24.04 | uv | `ubuntu:24.04` container (Python 3.12); Pop!_OS live ISO in a VM: COSMIC registers the tray icon (StatusNotifierWatcher) and its menu |
| openSUSE Tumbleweed | uv | container: gpu-screen-recorder, PySide6 (`python313-PySide6`), ffmpeg and Flatpak are in its OSS repo; ffmpeg has OpenH264 only |
| SteamOS 3.8 | uv | the recovery image's rootfs, read with `btrfs restore`: ffmpeg 7.1 (x264, VAAPI), ffprobe, Flatpak with Flathub, git, Python 3.13; not booted |

## Not done yet

- Recording on anything but Nobara: every distro above was checked without a graphics card.
- SteamOS Game Mode: whether the screen-share dialog or the root helper work there.
- A native openSUSE package (its package names differ from Fedora's, so it'd be its own spec).
- On COSMIC the app draws with Qt's own light style, not COSMIC's colours.

- Timed vs depleted for keys: the log only says completed. It needs each dungeon's time limit,
  which changes every season.
- Choosing the encoder GPU on machines with two GPUs.
- Checked against real logs: Classic, WoW Forever, dungeons without a key (Mythic 0, Heroic),
  raids. Retail Mythic+ was checked on a month of logs (130 keys): deaths, lusts, wipes and
  restarted keys; see [Real combat logs in the tests](#real-combat-logs-in-the-tests).
- The gpu-screen-recorder Flatpak: stopping it relies on `flatpak run` passing SIGINT on.
- The output format of `--list-capture-options` that the settings window parses.
- "Only WoW's sound" on retail is checked: `--list-application-audio` lists the game as
  `World of Warcraft`, not its .exe, and recordings asking for `app:WoW.exe` alone were silent
  (October 12, 2026). The recorder asks for both names, and gpu-screen-recorder picks the sound
  up when the game opens it after the recording started. Not checked: Classic and other
  clients' names. The service logs the list 90 s into each session and warns if WoW isn't in it.
