# Development

```bash
uv sync --extra gui        # the venv, with the desktop app's Qt
uv run pytest              # all tests (one needs ffmpeg; it's skipped without)
uv run linux-wow-recorder status
uv run linux-wow-recorder-gui
```

## Design rules

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
  folders, and the service and .desktop files.
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
      free space every 60 s           warn / stop below the limits
      recorder died → restart it      up to 3 times per session
  WoW gone twice in a row → end_session()
      recorder.stop()                 SIGINT, wait for the file to be saved
      finish()
        clips.process_pending()       every recording in sessions/, oldest first:
          recording_span()              start = file mtime − ffprobe duration
          combat_logs_between()         log files overlapping that span
          combatlog.read_log_events()   grep keeps only the needed events
          combatlog.find_runs()         runs, with bosses, deaths and lusts
          cut_clip()                    ffmpeg -ss/-t stream copy → clips/<name>.mp4 + .json
          → processed/                  (stays in sessions/ if a cut failed: retried next time)
        report()                      Discord/Fluxer webhooks
        run_hook() in a thread        after_clips
        cleanup()                     retention
      status.json: waiting
```

On start, the service stops a recorder left running by a run that crashed
(`recorder.adopt_leftover`), and cuts whatever an earlier run left in `sessions/`.

## Finding runs in the combat log

`combatlog.find_runs` walks the events in order:

- **Keys**: `CHALLENGE_MODE_START` opens one and `CHALLENGE_MODE_END` closes it. An END whose
  success field is 0 means the key was abandoned; the run then ends when the player left the
  instance. A START while a key is open abandons the old one.
- **Dungeons without a key**: a `ZONE_CHANGE` into an instance whose difficulty is in
  `DUNGEON_DIFFICULTIES` opens a visit. Leaving it, then being somewhere else for 10 minutes,
  closes it. Coming back (after a release or a disconnect) continues it. A visit counts only if
  a boss was pulled, and isn't used when a key ran in it.
- **Inside a run**, counted only while the player is in its instance:
  - `ENCOUNTER_START`/`END`: boss pulls, kills and wipes.
  - `UNIT_DIED` with a `Player-` GUID: deaths.
  - `SPELL_AURA_APPLIED` of a lust buff or its debuff (Sated, Exhaustion, Temporal Displacement,
    Fatigued, Exhaustion (Evoker)) on a player: lusts. Applications within 30 seconds are one
    lust. The buff names the caster; the debuff catches sources without a buff (drums).
- Runs shorter than `min_run_minutes` are dropped, except completed keys.

grep does the filtering, because logs reach 1 GB. The lust part of the pattern matches only the
listed spell IDs, so the busy `SPELL_AURA_APPLIED` lines cost nothing: about 0.6 s for 540 MB.

Timestamps are `M/D/YYYY H:M:S.ms±offset`; the year and the offset may be missing (Classic-based
clients, older logs). Without an offset, it's local time.

### Adding a dungeon difficulty

When the log enters an instance with a difficulty the app doesn't know, the service logs
`Not a dungeon this app knows: <name> (difficulty N)`. If it's a 5-player dungeon, add `N` to
`DUNGEON_DIFFICULTIES` with its name, and add a test.

### Adding a lust

Add the buff's spell ID to `LUST_BUFFS`. If it leaves a new debuff, add that to `LUST_DEBUFFS`.

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
| `combatlog.py` | finds dungeon runs, bosses, deaths and lusts in the combat log |
| `clips.py` | cuts recordings into clips with ffmpeg, writes the `.json` details, retention cleanup |
| `notify.py` | Discord or Fluxer webhooks (the same JSON) |
| `logs.py` | log files, kept on this computer and cleaned; the report file ([Logging](#logging)) |
| `daemon.py` | the main loop |
| `state.py` | `status.json` |
| `config.py` | the config file: defaults, loading, checking, writing it with comments |
| `cli.py` | the commands and `setup` |
| `service.py` | the systemd user service |
| `gui/__init__.py` | `linux-wow-recorder-gui`: checks for PySide6, `--autostart` |
| `gui/model.py` | what the windows show, without Qt, so the tests need no display |
| `gui/tray.py` | the tray icon (drawn in code) and its menu |
| `gui/settings.py` | the settings window |
| `gui/browser.py` | the clip browser: list, player, timeline |
| `gui/desktop.py` | the autostart and app menu `.desktop` files |

### Desktop app notes

- **Tray menu**: on KDE and GNOME, the menu is sent over D-Bus, so it can hold only plain actions,
  not widgets. Status lines are disabled actions, rebuilt only when their text changes.
- **Updates**: the tray polls `status.json` every second, and systemd every 5 seconds.
- **Package**: the `gui` extra is `PySide6-Essentials` + `PySide6-Addons`. Addons is for
  QtMultimedia; the full `PySide6` package would also bring Chromium (QtWebEngine), which isn't
  used.
- **Window and tray**: `run()` opens the window unless started with `--tray` (the autostart
  entry). Closing it hides it; `setQuitOnLastWindowClosed(False)` keeps the tray. One app per
  user: a lock file, plus a `QLocalServer` that a second launch tells to show the window. The
  app's desktop file name is `linux-wow-recorder-gui`, which is how Wayland taskbars find its
  icon (`gui/icon.svg`); the tray icon is drawn in code to show the state.
- **RPM**: `packaging/build-rpm.sh` builds the wheel with uv and wraps it with
  `packaging/linux-wow-recorder.spec`. It installs into the system Python and requires
  `python3-pyside6`, `gpu-screen-recorder` and `/usr/bin/ffmpeg` from the distro, so it's tied to
  one Fedora release. Flatpak was ruled out: the sandbox can't see the WoW process, run
  gpu-screen-recorder with its root helper, or install the systemd user service.
- **Arch package**: `packaging/aur/PKGBUILD` builds from the GitHub release tarball of the tag
  `pkgver` (tags have no `v`), runs the tests in `check()`, and installs the wheel, menu entry and
  icon. Arch's `pyside6` doesn't include QtMultimedia, so `qt6-multimedia` is a separate
  dependency; gpu-screen-recorder is in Arch's `extra` repo. Tested in an `archlinux` container
  with `makepkg -si`.
- **Running commands**: settings and the tray run `python -m linux_wow_recorder <command>`
  (test-recorder, process) instead of calling the functions, so a slow command never blocks
  the window.

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
`Release` stays 1 unless only the packaging changed. After the GitHub release (tag `0.5.0`, no
`v`), set `pkgver` in `packaging/aur/PKGBUILD` (and `pkgrel=1`), then in that folder
`updpkgsums && makepkg --printsrcinfo > .SRCINFO`; on an Arch-less machine, run both in an
`archlinux` container.

## Not done yet

- Raids (`ENCOUNTER_START`/`END` per boss pull, one clip per pull or per raid night).
- Timed vs depleted for keys: the log only says completed. It needs each dungeon's time limit,
  which changes every season.
- Choosing the encoder GPU on machines with two GPUs.
- Checked against real logs: deaths (does Feign Death show up as `UNIT_DIED`?), lusts, Classic,
  WoW Forever.
- The gpu-screen-recorder Flatpak: stopping it relies on `flatpak run` passing SIGINT on.
- The output format of `--list-capture-options` that the settings window parses.
- "Only WoW's sound" assumes WoW's sound shows up in PipeWire under its .exe name (`Wow.exe`),
  which is how Wine names a program's sound. Check with `gpu-screen-recorder
  --list-application-audio` while WoW runs.
