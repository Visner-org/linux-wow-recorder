# Reference

- [Commands](#commands)
- [Config file](#config-file)
- [Files and folders](#files-and-folders)
- [Clip details (.json)](#clip-details-json)
- [Full recording details (.json)](#full-recording-details-json)
- [Service status (status.json)](#service-status-statusjson)

## Commands

`linux-wow-recorder`:

| Command | Does |
|---|---|
| `setup` | asks for the folders and options and writes the config; offers a test recording, then to start the recorder **at every login** (the service) |
| `run` | watches for WoW and records, in the foreground (what the service runs) |
| `status` | the config, the service and what it's doing, and any problem with the setup; exit code 1 if there is one |
| `process` | cuts the recordings waiting in `sessions/` now; refuses while WoW is running |
| `keep <clip>...` | keeps clips forever, whatever `clip_retention_days` says; `--undo` lets them expire again. A clip is a path, or a file name in `clips/` |
| `test-recorder` | records 5 seconds to `<output>/test-recording.mkv`; refuses while the service is recording |
| `pause`, `resume` | stops recording for the rest of this WoW session (what was recorded so far is kept), or records again; the next session always records |
| `test-webhooks` | sends a test message to every webhook and names any that failed (`test-discord` is the old name) |
| `install-service` | installs and starts the systemd user service, which then **starts at every login** until `uninstall-service` (the desktop app's Settings > Advanced does the same with its "Record from every login" tick) |
| `uninstall-service` | stops and removes the service; the config and recordings stay |
| `uninstall` | a clean uninstall (Settings > Advanced > Uninstall... runs it): stops and removes the service, the autostart and menu entries the app wrote, the config, logs and runtime files, and for a uv or pipx install the program too; an RPM or Arch package is removed with the `dnf`/`pacman` command it prints. Recordings and clips stay unless asked: `--delete-recordings` removes `sessions/`, `processed/` and `clips/` (nothing else in the output folder, nor other folders' clips). `--yes` asks nothing; `--keep-program` leaves the program installed |
| `logs [-n LINES]` | where the logs are, and the service log's last lines (40) |
| `report [FOLDER]` | writes `linux-wow-recorder-report-<date>.txt` to FOLDER (default: the Desktop) for sharing by hand; nothing is sent ([Logs](#logs)) |

`linux-wow-recorder-gui` (desktop app):

| Command | Does |
|---|---|
| (none) | starts the tray icon; opens Settings if nothing is set up |
| `--autostart on` / `off` | shows the tray icon at every login (and lists it in the app menu), or stops doing so |

## Config file

`~/.config/linux-wow-recorder/config.toml` (or under `$XDG_CONFIG_HOME`). `setup` and the
settings window write it, keeping a comment above each setting; you can also edit it by hand.
After a hand edit, run `systemctl --user restart linux-wow-recorder`. Settings missing from the
file take their defaults, and unknown ones are ignored.

### `[paths]`

| Key | Default | |
|---|---|---|
| `combat_log_dir` | (none) | WoW's `Logs` folder. Subfolders are searched too (e.g. an addon's archive). |
| `output_dir` | (none) | where `sessions/`, `processed/` and `clips/` go |

### `[recorder]`

| Key | Default | |
|---|---|---|
| `command` | `"gpu-screen-recorder"` | the recorder. For the Flatpak: `"flatpak run --command=gpu-screen-recorder com.dec05eba.gpu_screen_recorder"` |
| `capture` | `"portal"` | `"portal"`, a monitor name from `gpu-screen-recorder --list-capture-options`, or `"screen"`: the first monitor in that list, not necessarily your main one |
| `monitor` | `""` | what **Record monitor instead** switches a session to while `capture` is `"portal"`: a monitor name, or `""` for `"screen"` |
| `fps` | `30` | |
| `quality` | `""` | `"medium"`, `"high"`, `"very_high"` or `"ultra"`; empty uses gpu-screen-recorder's default |
| `audio` | `["wow"]` | one track per entry: `"wow"` is only WoW's own sound, `"default_output"` all desktop sound (WoW, Discord, music...), `"default_input"` the microphone; `"wow\|default_input"` mixes two into one track. `[]` records no sound. Anything else goes to gpu-screen-recorder's `-a` as it is, e.g. `"app:discord"`. |
| `extra_args` | `[]` | passed to gpu-screen-recorder as they are, e.g. `["-k", "hevc"]` |

### `[clips]`

| Key | Default | |
|---|---|---|
| `pad_before` | `10` | seconds of video kept before a run |
| `pad_after` | `15` | seconds of video kept after a run |
| `min_run_minutes` | `5` | shorter runs are skipped, except completed keys |
| `raids` | `"all"` | raid boss pulls: `"all"` (a clip per pull, kills and wipes; pulls under 30 s that aren't kills are skipped), `"kills"`, or `"off"` |
| `other_folders` | `[]` | folders on this computer the clip browser also lists clips from (only the desktop app reads it). A clip there whose `.json` stayed in `clips/` is matched to it by name, ignoring spaces, hyphens and punctuation, so a renamed copy still finds its details |

### `[storage]`

| Key | Default | |
|---|---|---|
| `session_retention_days` | `3` | full recordings are deleted this many days after they're made; 0 = never. One whose clips couldn't be cut is kept, with a warning |
| `clip_retention_days` | `0` | clips are deleted this many days after they're cut; 0 = never. Kept clips never are. |
| `min_free_gb` | `20` | warn below this much free space on the output folder's drive |
| `stop_below_gb` | `5` | stop recording below this |

### `[discord]`

| Key | Default | |
|---|---|---|
| `webhook_urls` | `[]` | Discord or Fluxer webhook URLs, at most 25; every message goes to each one. `[]` = no messages. An older config's single `webhook_url` is read as the first entry |

`[discord.notify]`, one true/false per message:

| Key | Default | Sent when |
|---|---|---|
| `session_started` | false | WoW started and recording began |
| `session_ended` | false | WoW closed and the recording was saved |
| `clips_ready` | true | clips are ready after a session |
| `no_runs` | false | a session ended with no dungeon runs or raid pulls in it |
| `no_combat_log` | true | no combat log covers a session |
| `recording_failed` | true | the recorder didn't start, or stopped mid-session |
| `low_disk_space` | true | free space is under `min_free_gb`, or recording stopped under `stop_below_gb` |
| `clip_failed` | true | a clip couldn't be cut |
| `hook_failed` | true | the `after_clips` command failed |
| `cleanup` | false | old recordings were deleted |

### `[hooks]`

| Key | Default | |
|---|---|---|
| `after_clips` | `""` | a command run after each session (in the background), with the new clips' paths added as arguments. `~` is expanded in the program's path. It's split like a shell would, but not run through one, so pipes and `&&` need a script. |

### `[clips_window]`

What the clips window shows. Only the desktop app reads these (Settings > Clips and storage >
Clips window); `false` hides it.

| Key | Default | |
|---|---|---|
| `raids` | `true` | the Raids view (raid boss pull clips) |
| `full_sessions` | `true` | the Full sessions view (the whole recordings) |
| `hide_chat` | `true` | the Hide chat button |
| `youtube` | `true` | the Upload to YouTube button |

With both views off, the Keystones button is hidden too.

### `[updates]`

| Key | Default | |
|---|---|---|
| `check` | `false` | the tray app asks GitHub once a day for the newest release's version (`api.github.com/repos/Visner-org/linux-wow-recorder/releases/latest`; the request carries nothing about you) and, if it's newer, says so in a popup once and in the menu, with a link. It never downloads or installs anything. Setup asks; configs from before 0.6.0 stay `false` until you tick it in Settings > About. |

### `[daemon]`

| Key | Default | |
|---|---|---|
| `poll_interval` | `5` | seconds between checks for WoW |

### `[logging]`

| Key | Default | |
|---|---|---|
| `detailed` | `false` | adds detailed (debug) lines to the logs, for tracking down a problem |

## Files and folders

| Path | |
|---|---|
| `~/.config/linux-wow-recorder/config.toml` | the config |
| `~/.config/linux-wow-recorder/gui.ini` | the desktop app's own choices: the clips window's view, and when GitHub was last asked about a new version and what it said |
| `~/.config/systemd/user/linux-wow-recorder.service` | the service, written by `install-service` or the desktop app's setup; it starts at login only when linked into `default.target.wants/` (enabled) |
| `~/.config/autostart/linux-wow-recorder-gui.desktop` | starts the tray icon at login |
| `~/.local/share/applications/linux-wow-recorder-gui.desktop` | the app menu entry |
| `~/.local/state/linux-wow-recorder/recorder.json` | the recorder's process, so a restarted service can stop one left running |
| `~/.local/state/linux-wow-recorder/logs/` | the logs ([below](#logs)) |
| `$XDG_RUNTIME_DIR/linux-wow-recorder/status.json` | what the service is doing ([below](#service-status-statusjson)) |
| `$XDG_RUNTIME_DIR/linux-wow-recorder/control.json` | a pause (`pause` or the tray), a switch to the monitor or another pick (tray or clips window), for one session |
| `<output>/sessions/` | the recording being made, and recordings not cut yet (`2026-10-08_19-03-12.mkv`) |
| `<output>/processed/` | full recordings already cut, each with its details (`.json`) and, once the clip browser has shown it, a picture (`.jpg`) |
| `<output>/clips/` | the clips (`.mp4`), their details (`.json`) and pictures (`.jpg`) |

Recordings are `.mkv`, which stays playable even if the recorder is killed. Clips are `.mp4`
with the index at the front (written once, in space kept for it), so they play in browsers and
web galleries before they're fully downloaded.

## Logs

Everything is in `~/.local/state/linux-wow-recorder/logs/` and stays on this computer. The app
never sends logs anywhere: webhook messages say that something went wrong and point here, without
log lines, error output or file paths.

| File | |
|---|---|
| `recorder.log` | the background service; each start is logged with the app version, distro, kernel and desktop (also in its journal, `journalctl --user -u linux-wow-recorder`) |
| `app.log` | the desktop app and the other commands |
| `sessions/<date time>.log` | gpu-screen-recorder's own output, one file per recording; the last 10 are kept |
| `crash.log` | a hard crash's stack trace, if one ever happens |

`recorder.log` and `app.log` start a new file at 1 MB and keep 4 old ones (`.log.1` ...), and any
log untouched for 30 days is deleted. Lines are cleaned before they're written: webhook links
become `webhook 2 (...a1b2)`, the home folder `~`, and the computer's name, the user name and IP
addresses are replaced. gpu-screen-recorder's own output is saved as it is.

**Report file:** `linux-wow-recorder report`, or Settings > Make a report file, writes a plain
text file with the app, system, gpu-screen-recorder and ffmpeg versions, the GPU, the config
(webhook links and the `after_clips` command removed) and the end of each log, all cleaned the
same way. It's for sharing by hand when asking for help; read it first.

## Clip details (.json)

Each clip has a `.json` file of the same name beside it, written when the clip is cut. Other
programs are welcome to read it, e.g. a web gallery that shows the deaths on a timeline. Times
called "at", "start" or "end" are **seconds from the clip's start**. Times called `run_*` and
`clip_start` are Unix times. Anything that happened outside the clip is left out (a run split
over two recordings gets one clip from each, each with its own part).

```json
{
  "version": 1,
  "dungeon": "Ara-Kara, City of Echoes",
  "label": "+11 Timed",
  "level": 11,
  "result": "completed",
  "run_start": 1791694680.0,
  "run_end": 1791696281.0,
  "run_length": "26m41s",
  "timer": 1601.0,
  "time_limit": 1800,
  "clip_start": 1791694670.0,
  "clip_length": 1626.0,
  "run_offset": 10.0,
  "bosses_killed": 3,
  "deaths_total": 2,
  "encounters": [
    {"name": "Avanoxx", "start": 205.3, "end": 291.0, "killed": true}
  ],
  "deaths": [
    {"name": "Tank-Realm-US", "at": 742.1}
  ],
  "lusts": [
    {"name": "Bloodlust", "caster": "Shammy-Realm-US", "at": 206.0}
  ],
  "recording": "2026-10-10_19-03-12.mkv",
  "thumbnail": "2026-10-10 19-03 Ara-Kara, City of Echoes +11 Completed (26m41s).jpg",
  "keep": false
}
```

| Field | |
|---|---|
| `version` | the format's version; it goes up if fields change meaning |
| `dungeon` | the dungeon's name as WoW logs it; a raid pull's boss |
| `label` | `"+11 Timed"`, `"+11 Over time"`, `"+11 Completed"` (a dungeon whose time limit the app doesn't know), `"+10 Abandoned"`, or a difficulty: `"Heroic"`, `"Mythic"`, `"Normal"`, `"Timewalking"`, `"Follower"`; a raid pull's difficulty and result: `"Heroic Kill"`, `"LFR Wipe"` |
| `level` | the keystone level, or `null` without a key |
| `result` | `"completed"`, `"abandoned"`, or the difficulty in lower case; a raid pull's `"kill"` or `"wipe"` |
| `kind`, `raid`, `difficulty`, `pull` | a raid pull's only: `"raid"`, the raid's name, its difficulty (`"LFR"`, `"Normal"`, `"Heroic"`, `"Mythic"`, ...), and the pull's number on that boss and difficulty in the recording, from 1 |
| `run_start`, `run_end` | when the run started and ended |
| `run_length` | the in-game timer for a completed key, the run's length otherwise |
| `timer` | a completed key's in-game time in seconds, death penalties included; `null` otherwise |
| `time_limit` | a completed key's dungeon's time limit in seconds (`keytimers.py`); `null` if not known. Clips cut before 0.8.1 don't have it; the clip browser looks the dungeon up by name |
| `clip_start`, `clip_length` | where the clip starts, and how long it is |
| `run_offset` | how far into the clip the run starts (`pad_before`, unless the recording started later) |
| `bosses_killed`, `deaths_total` | over the whole run |
| `encounters` | each boss pull: its name, start, end (`null` if the log ended mid-pull) and whether it was a kill. A wipe and the kill after it are separate pulls of the same boss. |
| `deaths` | each player death: the name as logged (`Name-Realm-Region`) and when |
| `lusts` | each Bloodlust-type effect: `"Bloodlust"`, `"Heroism"`, `"Time Warp"`, `"Primal Rage"`, `"Fury of the Aspects"`, `"Harrier's Cry"` or `"Drums"`; the caster (the drummer for drums), or `null` when the log doesn't say; and when |
| `recording` | the full recording it was cut from |
| `thumbnail` | a 480 px wide picture from the clip, 20 s into the first boss pull (a minute into the run without one), beside it; `null` if ffmpeg couldn't make it. Clips cut before pictures existed get one from the clip browser, as `<clip>.jpg`. |
| `keep` | `true` keeps the clip forever, whatever `clip_retention_days` says |

Clips cut by versions from before the `.json` existed don't have one. The clip browser falls
back on the clip's name for those.

## Full recording details (.json)

Each full recording in `processed/` gets a `.json` of the same name when it's cut, for the clip
browser's **Full sessions**. Times in it are **seconds from the recording's start**, except
`recording_start` (a Unix time); `runs` holds each dungeon run and raid pull in the recording, shaped like a
clip's details above, including runs too short to get a clip. `keep` works as it does for clips:
a kept recording is never deleted, whatever `session_retention_days` says. Recordings cut by
0.5.0 or earlier have no `.json`; the clip browser finds their runs from the clips cut from them
(while those clips exist).

```json
{
  "version": 1,
  "recording_start": 1791692400.0,
  "length": 8040.3,
  "runs": [{"dungeon": "Ara-Kara, City of Echoes", "label": "+11 Completed", "run_offset": 2280.0, "...": "..."}],
  "keep": false
}
```

## Service status (status.json)

The service writes what it's doing to `$XDG_RUNTIME_DIR/linux-wow-recorder/status.json`, whole,
on every change. The tray icon and `status` read it, and so can your own scripts. If the file
is missing, its `pid` isn't running, or `state` is `"stopped"`, the service isn't running.

```json
{
  "pid": 12345,
  "state": "recording",
  "flavor": "Retail",
  "session_start": 1791694600.0,
  "recording_file": "/media/games/wow-recordings/sessions/2026-10-10_19-03-12.mkv",
  "free_gb": 412.3,
  "problem": null,
  "picking": false,
  "capture": "portal",
  "monitor_switch": false,
  "last_session": {"end": 1791610000.0, "clips": ["/media/.../clips/....mp4"], "problems": []},
  "updated": 1791694660.2
}
```

| `state` | |
|---|---|
| `waiting` | watching for WoW |
| `recording` | WoW is running and being recorded |
| `not_recording` | WoW is running but isn't being recorded: the screen-share dialog is waiting for a pick (`picking`), the recorder failed, or the disk is nearly full (`problem` says which) |
| `paused` | WoW is running; recording was paused for this session (`pause`, or the tray) |
| `processing` | cutting clips after a session (while WoW isn't running) |
| `stopped` | the service has exited |

`problem` is a sentence while something is wrong, `null` otherwise. `capture` is what this
session records (`-w`), and `monitor_switch` is true once it was switched to the monitor. `last_session` is the last
time recordings were cut: when, the new clips, and any problems (as Discord message text).
