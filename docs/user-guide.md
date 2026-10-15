# User guide

Linux WoW Recorder records your World of Warcraft sessions and cuts one clip per dungeon run,
and one per raid boss pull.
It runs in the background: start WoW and play, and when you close WoW, the clips are waiting.

- [Which edition](#which-edition)
- [Privacy](#privacy)
- [Before you install](#before-you-install)
- [Installing](#installing)
- [Setting it up](#setting-it-up)
- [Everyday use](#everyday-use)
- [Discord or Fluxer messages](#discord-or-fluxer-messages)
- [Sending clips somewhere](#sending-clips-somewhere)
- [Disk space and cleanup](#disk-space-and-cleanup)
- [WoW versions](#wow-versions)
- [Troubleshooting](#troubleshooting)
- [Uninstalling](#uninstalling)

## Which edition

There's one program with two ways to install it:

| | Command line | Desktop app |
|---|---|---|
| Install | `linux-wow-recorder` | `linux-wow-recorder[gui]` |
| Download | a few hundred KB, nothing else needed | about 400 MB (Qt, with video playback) |
| Setup | questions in a terminal (`setup`) | a settings window |
| While playing | nothing to see; Discord messages if you want them | a tray icon: grey waiting, red recording |
| Clips | a folder of .mp4 files | a clip browser with a player and a timeline |

Both record the same way, with the same background service and the same config file. You can
start with the command line and add the desktop app later, or the other way round.

## Privacy

Everything stays on your computer: recordings, clips, settings and logs. The app has no
telemetry or crash reporting, and no account. It connects to the internet only if you ask it to:
a message to the Discord or Fluxer webhooks you add yourself, which never includes log lines,
paths or anyone's character name, and, if you tick it at setup or in Settings > About, a daily
question to GitHub about the newest version, which sends nothing about you. The full details, and how to check them yourself, are
in the [README](../README.md#privacy).

## Before you install

You need:

- **Linux with systemd**: almost every desktop distro.
- **Python 3.11 or newer**: check with `python3 --version`.
- **[uv](https://docs.astral.sh/uv/)** or **pipx**, to install the app.
- **gpu-screen-recorder**, which does the recording:
  - CachyOS / Arch: `sudo pacman -S gpu-screen-recorder`
  - Fedora / Nobara: the RPM below brings it in (on Nobara it's in the distro's repos)
  - anywhere, including Bazzite and SteamOS: `flatpak install flathub com.dec05eba.gpu_screen_recorder`
- **ffmpeg** (with `ffprobe`), from your distro.

And in WoW:

- **Combat logging on in dungeons.** WoW doesn't log by itself. Type `/combatlog` after you
  zone in, or let an addon do it: Raider.IO's client, an "auto combat log" addon, or for WoW
  Forever, forever-logs-companion. Without a log, the session is still recorded but can't be
  cut into clips.
- **Advanced Combat Logging** on (Options > Network). It isn't needed to find runs, but it
  makes the log more complete.

## Installing

### Nobara or Fedora (RPM)

```bash
sudo dnf install ./linux-wow-recorder-0.8.0-1.fc44.noarch.rpm
```

dnf installs gpu-screen-recorder, ffmpeg and Qt from your distro's repos along with it. The app
appears in the menu as **Linux WoW Recorder**. To update, install the newer RPM the same way.
The service and the tray app restart into the new version by themselves within a few minutes, but only when it's safe: the service waits until WoW is closed, the clips are cut and any After clips command has finished, and the tray app waits until its windows are closed. (Updating from 0.5.0 or earlier needs one restart by hand: `systemctl --user restart linux-wow-recorder`, and quit and start the tray app.) The RPM is built for one Fedora release
(`fc44` is Fedora/Nobara 44); on another release, build it there with
`./packaging/build-rpm.sh` (needs `uv` and `rpm-build`).

### CachyOS or Arch (package)

The package in `packaging/aur/` installs the app system-wide, with every dependency from Arch's
own repos (gpu-screen-recorder, ffmpeg, PySide6). It'll be on the AUR (`paru -S
linux-wow-recorder`) once AUR sign-ups reopen; until then, build it from the repo:

```bash
git clone https://github.com/Visner-org/linux-wow-recorder
cd linux-wow-recorder/packaging/aur
makepkg -si
```

To update, `git pull` and run `makepkg -si` again; the app restarts into it as with the RPM
above. The uv route below works on Arch too.

### Other distros (CachyOS, Arch, Bazzite, SteamOS, Ubuntu, ...)

These install the app into your home folder (`~/.local`) with [uv](https://docs.astral.sh/uv/),
so they also work on read-only systems like Bazzite and SteamOS: no root, no reboot. Get uv from
your package manager (`sudo pacman -S uv` on CachyOS and Arch) or with its install script
(`curl -LsSf https://astral.sh/uv/install.sh | sh`).

With the desktop app:

```bash
uv tool install 'linux-wow-recorder[gui] @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder-gui --autostart on   # tray icon at login, and an app menu entry
linux-wow-recorder-gui
```

Command line only:

```bash
uv tool install 'linux-wow-recorder @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder setup
```

pipx works the same way: `pipx install 'linux-wow-recorder[gui] @ git+https://github.com/Visner-org/linux-wow-recorder'`.

To update later, run `uv tool upgrade linux-wow-recorder`; the app restarts into it as with the
RPM above.

## Setting it up

You'll be asked for two folders:

1. **Your combat log folder**: the `Logs` folder next to WoW's `_retail_` (or `_classic_`)
   folder, wherever your launcher put WoW. With Lutris, for example:
   `~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs`.
2. **An output folder** for recordings and clips. Pick a drive with room: a session takes a few
   GB per hour.

### Desktop app

Open **Linux WoW Recorder** from the app menu (or run `linux-wow-recorder-gui`). The first time,
a short setup opens by itself, in three steps:

1. **Folders**: the combat log folder and the output folder (the Browse buttons help). Under
   each, a line says what it found: the newest combat log, or the free space.
2. **What to record**: the capture, frame rate and quality, and the sound. "Record" starts on
   "A window or screen you pick": the test recording opens the screen-share dialog, where you
   choose the WoW window or its monitor.
3. **Try it**: **Save and record a 5-second test**. The result shows under the button, and the
   video is saved as `test-recording.mkv` in the output folder. Watch it to check the picture and
   sound. **Finish** installs the background service and shows the tray icon at every login.

Everything else (clips and storage, Discord messages, the recorder command, logs) is in
Settings later, on pages down the left. A line under a field says when something's wrong with
it, and a page with a problem gets a ⚠ beside its name.

### Command line

```bash
linux-wow-recorder setup
```

It asks for the two folders, what to record, which sound (only WoW's, the default; all desktop
sound; or none) and whether to add your microphone, and Discord or
Fluxer webhooks (optional). Then it offers a 5-second test recording and to install the background
service. Everything else is in the config file (see the [reference](reference.md#config-file)).

### What to record

- **A window or screen you pick** (`portal`, the default): Wayland's own screen-share dialog. Pick the WoW
  window to record only WoW: notifications and other windows stay out of the video, and
  alt-tabbing shows WoW's last frame instead of your desktop. A picked screen is remembered;
  whether a picked window is remembered from one WoW session to the next depends on your desktop,
  so the dialog may come back each time. It never needs root. Nothing is recorded until you pick:
  while the dialog waits (it can open behind WoW), the tray and the clips window say so and
  offer **Record monitor instead**, which records the monitor set under "Monitor instead" for
  the rest of that session. Picked the wrong window? **Pick another window** (tray menu, or under
  the clips window's preview) forgets the pick and opens the dialog again.
- **A monitor** (`DP-1`, `HDMI-A-1`, ...): just that monitor. With more than one screen, this
  is usually what you want.
- **Whole screen** (`screen`): the first monitor in `gpu-screen-recorder --list-capture-options`,
  which goes by how the monitors are plugged in (connector order). That isn't necessarily your
  main monitor, or the one your desktop's display settings call "1"; the settings window shows
  which monitor it is. With two or more screens, pick the monitor by name instead. On AMD and Intel under Wayland, it needs gpu-screen-recorder's root helper;
  the Flatpak asks for your password once.

## Everyday use

There's nothing to do: start WoW and play. The service notices WoW within a few seconds,
however it's installed (Lutris, Heroic, Bottles, Steam, plain Wine, Flatpak), and starts
recording. When WoW closes, it cuts the clips, which takes a few seconds per run.

Which runs become clips:

- **Mythic+ keys**, from the start to the end of the key. Abandoned keys count too if they
  lasted at least 5 minutes.
- **Dungeons without a key** (Normal, Heroic, Mythic 0, Timewalking, Follower), from entering to
  leaving, if a boss was pulled.

Clips are named like `2026-10-08 19-03 Kings' Rest +12 Completed (24m52s).mp4`. A completed
key's length is the in-game timer.

### The window and the tray icon (desktop app)

Opening the app shows its window, with its icon on the taskbar. Closing the window doesn't quit:
the app stays in the tray (it tells you the first time), and recording carries on either way.
Open it from the menu again, or left-click the tray icon, to get the window back. At login only
the tray icon starts. To close the app completely, use **Quit** in the tray menu.

| Icon | Means |
|---|---|
| grey dot | waiting for WoW |
| red dot | recording |
| amber dot | cutting clips |
| badge | something needs attention (hover for what) |
| empty ring | the service isn't running |

- **Left-click** opens the clip browser.
- **Right-click** opens the menu: the status and the last session, then Clips, **Pause
  recording this session** (while WoW runs: nothing more is recorded until WoW closes, and the
  next session records as usual; Resume undoes it), **Record monitor instead** (while a window is
  picked or awaited: records your "Monitor instead" for the rest of this session; **Record the
  window again** undoes it), **Pick another window** (while recording a picked window: opens the
  screen-share dialog again), Settings, and a Folders and logs submenu. With update checks on,
  **Version X is out...** appears when there's a newer version, and opens its release page.

You also get a desktop notification when clips are ready or something goes wrong.

Closing the tray icon doesn't stop the recording: the service does the recording, not the
icon: **Hide tray icon (keeps recording)** does just that. To stop both, use **Quit and stop
recording**. If the service is set
to start at login, it comes back at the next login; untick that in Settings to stop it for good.

On GNOME, tray icons need the
[AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/)
extension. KDE and most other desktops show them as they are.

### The clip browser (desktop app)

- **Recording now**, at the top left while WoW is being recorded: the newest frame of the
  recording, every 3 seconds, so you can check it records the right screen or window. It's a few
  seconds behind, because the recording is written to disk in chunks. While the window is minimized or
  in the background, the preview waits (to save the work of decoding frames nobody sees) and
  says so; it catches up as soon as you click the window.
- **Not ready to record yet**, at the top left only while something is missing: a checklist
  (gpu-screen-recorder, ffmpeg, the combat log folder, combat logging, the output folder, the
  service), with what to do about each unticked item.
- **The clips** on the left, as cards (a picture, the dungeon and level, the result, length,
  deaths and lusts, ★ when kept) or as a table you can sort by any column (Cards / Table).
  Filter with All / Completed / Abandoned, or search by dungeon.
- **Clips you move elsewhere**: add the folder in Settings > Clips and storage > "Also show
  clips from", and the list shows the clips there too, saying which folder each is in. If you
  leave a clip's `.json` (and `.jpg`) in `clips/` when you move the video, it keeps its picture,
  deaths, lusts and bosses, even if the copy was renamed with hyphens for spaces. The app never
  changes anything in those folders, and Keep and "Keep clips" don't apply there.
- **Keystones / Raids / Full sessions**, above the list: Keystones lists the clips, one per
  dungeon run; Raids lists one per raid boss pull ("#3" is the third pull of that boss), filtered
  with All / Kills / Wipes and searched by boss or raid; Full sessions lists the whole recordings they were cut from, one each time you played, with
  every run in them on the timeline and in the events (each run holding its boss pulls). Full
  recordings are kept for as long as Settings > Clips and storage > "Keep full recordings" says
  (3 days unless you change it; Forever keeps them all); ☆ Keep keeps one forever. A recording
  of the session still going is listed as "Not cut yet" once the recorder has started a new one
  (after WoW's window closed, for example).
- **The timeline** under the player shows the run, with a legend:
  - above the bar, each boss pull from pull to kill (blue) or wipe (grey)
  - below it, deaths (red ticks) and lusts (yellow brackets as long as the haste: Bloodlust,
    Heroism, Time Warp, Primal Rage, Fury of the Aspects, Harrier's Cry, drums)

  Hover to see what's there. Click a pull, death or lust to play from a few seconds before it;
  click anywhere else to go there.
- **The events** below, one row per boss pull ("killed in 1:44 · 2 deaths") holding the deaths
  and lusts during it; wipes and pulls with deaths are opened. Clicking a row plays from a few
  seconds before it.
- **Keys**: space plays and pauses; the left and right arrows skip 5 seconds.
- **Buttons**: **Play**; beside the title, ☆ Keep (keep it forever), open it in your own video
  player, show it in the file manager, upload it to YouTube (see
  [To YouTube](#to-youtube)), run your `after_clips` command on it again (only there
  when one is set), and the red trash can: move it to the trash (with its details and picture;
  restore them from the trash to undo).
- **Several at once**: Ctrl+click picks clips one by one, Shift+click a range. The trash can
  (or the Delete key) then moves them all to the trash, after one question.

New clips show up by themselves.

### The command line

```bash
linux-wow-recorder status            # is everything OK, and what is the service doing
linux-wow-recorder process           # cut what's waiting now (only when WoW isn't running)
linux-wow-recorder keep <clip>       # keep a clip forever; --undo to let it expire again
linux-wow-recorder logs              # where the logs are, and the last lines
```

The full list is in the [reference](reference.md#commands).

## Discord or Fluxer messages

Add webhook URLs in Settings (**Add webhook**, and **Delete** next to each) or in `setup`, up
to 25. Every message goes to each one, so you can post to several channels or servers. In
Discord: Server Settings > Integrations > Webhooks > New Webhook > Copy Webhook URL. In Fluxer:
the channel's or community's settings > Webhooks. Then choose which messages to send:

| Message | Sent when | On by default |
|---|---|---|
| WoW started | WoW started and recording began | no |
| WoW closed | WoW closed and the recording was saved | no |
| Clips ready | clips were cut, listing each run with bosses killed, deaths and lusts | yes |
| No runs | a session had no dungeon runs or raid pulls | no |
| No combat log | no combat log covers a session (logging was off?) | yes |
| Recording failed | the recorder didn't start, or stopped mid-session | yes |
| Low disk space | free space is low, or recording stopped because the drive is nearly full | yes |
| Clip failed | a clip couldn't be cut | yes |
| Hook failed | your `after_clips` command failed | yes |
| Cleanup | old recordings were deleted | no |

Treat webhook URLs like passwords: anyone who has one can post in that channel. The config
file is saved readable by you only.

## Sending clips somewhere

To copy or upload clips automatically, set **After clips** (`after_clips` in the config) to a
command. It runs after each session, with the new clips' paths added as arguments. For
example, `~/bin/upload-clips.sh` with:

```bash
#!/bin/sh
# Copies the new clips, and their .json details, to a server.
for clip in "$@"; do
    rsync -a "$clip" "${clip%.mp4}.json" myserver:/srv/clips/
done
```

The command runs in the background, so a long upload doesn't stop the next session from being
recorded. Its output goes to the service log. If it fails, you get a message (when
"Hook failed" is on).

### To Fireshare

[Fireshare](https://github.com/fireshare-app/fireshare) (1.9 or newer) takes uploads from scripts
with an upload token: in Fireshare, your account > Upload tokens > create one. The upload is a
`POST` to `/api/upload/token` with the token as `Authorization: Bearer <token>` and the clip as
the `file` form field (`title`, `folder` and `game` are optional); Fireshare answers 201 when it
has the clip, and 409 when it already had it. A script for `after_clips` only needs that:

```bash
#!/bin/sh
# Uploads the new clips to Fireshare, and deletes each video here once Fireshare has it. The
# .json and .jpg stay, so the clip browser still shows the details if you add the folder
# Fireshare keeps its videos in to "Also show clips from".
for clip in "$@"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $FIRESHARE_TOKEN" \
           -F "file=@$clip" -F "game=World of Warcraft" https://fireshare.example.com/api/upload/token)
    case "$code" in
        201|409) rm -f "$clip" ;;
        *) echo "$clip: Fireshare answered $code"; exit 1 ;;
    esac
done
```

Clips are large (a few GB for a key): if a reverse proxy in front of Fireshare limits upload
sizes, send them in pieces with `/api/upload/token/chunked` instead (each request: `blob`,
`chunkPart` from 1, `totalChunks`, a `checkSum` of your choice grouping the pieces, `fileName`,
`fileSize`; Fireshare answers 202 until the last piece). Keep the token out of the config file
and out of the script if others can read them: anyone with it can upload as you.

### To YouTube

The clip browser's upload button (beside "show in folder") makes a YouTube upload quick, but
the app doesn't upload anything itself: your browser does. One click:

1. copies a description: the run in a sentence, then chapters (the start, the run starting
   and each boss pull, kill or wipe), which YouTube turns into chapter marks on the video
2. opens YouTube's upload page in your browser
3. shows the clip in your file manager

Drag the clip onto the YouTube page and paste the description. YouTube takes the title from
the file name, for example "2026-10-14 20-05 Temple of Sethraliss +12 Completed (34m58s)".

- Chapters need at least three marks 10 seconds apart, so a raid pull or a run with one boss
  gets the sentence only.
- New YouTube accounts can only upload videos up to 15 minutes until they verify a phone
  number (YouTube Studio > Settings > Channel > Feature eligibility). Most dungeon clips are
  longer.
- A clip of 2 to 4 GB takes a while on a slow upload; YouTube keeps processing it after the
  upload finishes, and HD versions show up last.
- Choose Private, Unlisted or Public on YouTube's last page. Unlisted is handy for sharing a
  run with your group.
- If you also use an `after_clips` command that moves clips off this computer (like
  `fireshare-upload`), upload from where the clip is now: add that folder to "Also show
  clips from" and the button finds it there.

### Keeping clips somewhere else

The app records, cuts and keeps everything on this computer, and it doesn't move or upload
anything itself: that's your After clips command's job, and getting the files somewhere (another
drive, a mounted share, a sync folder) is up to you. Once they're there, the clip browser can
still show them:

1. **Move the video, leave its details.** Have your command move or delete only the `.mp4`, and
   leave the clip's `.json` and `.jpg` in `clips/` (a few KB each).
2. **Add the folder the videos end up in** to Settings > Clips and storage > "Also show clips
   from". It has to be a folder on this computer: a second drive, or a share you've mounted.

Then the clips there are listed with your local ones (the details line says which folder each
is in), play from there, and still have their picture, timeline, bosses, deaths and lusts. The
details are matched by name, ignoring spaces, hyphens and punctuation, so a copy that was renamed
on the way (Fireshare turns spaces into hyphens) still finds them.

| What you do | What the clip browser shows |
|---|---|
| Move the `.mp4`, leave the `.json` and `.jpg` in `clips/` | everything: picture, timeline, bosses, deaths, lusts |
| Move the `.json` (and `.jpg`) along with the `.mp4`, keeping the name | everything; the picture only if the `.jpg` went too |
| Move the `.mp4` and delete its details | the dungeon, level, result and length, read from the name |
| Copy the clip and let `clips/` expire it ("Keep clips") | the copy, but from its name only once the original expires: cleanup deletes the details with the clip. Move instead of copying to keep them |
| Nothing in "Also show clips from" | only `clips/` |

Clips in those folders are yours to manage:

- The app never changes or deletes anything there on its own: no pictures are made there, Keep
  and "Keep clips" don't apply, and only **Move to trash** (which you click) touches them. On a
  share without a trash, it asks before deleting for good; in a folder that's read only for you
  (a read-only share, say), it says so and deletes nothing.
- If the folder isn't there (a drive or share not mounted), its clips just aren't listed, and
  Settings shows a ⚠ beside it.
- New clips show up by themselves when the folder is on this computer's own drives. On a network
  share, changes made by another computer may not show until the list reloads (closing and
  reopening the window does it).
- Full sessions stay in the output folder. A full recording's runs come from its own `.json`
  in `processed/`, so moving its clips doesn't change it.

## Disk space and cleanup

The app writes only inside three folders in your output folder:

```
<output>/sessions/    the recording being made, and recordings not cut yet
<output>/processed/   full recordings already cut, each with a .json of details
<output>/clips/       the clips, each with a .json of details and a .jpg picture beside it
```

- **Full recordings** are deleted 3 days after they're made. You can change this in "Keep full
  recordings" or `session_retention_days`; 0 means never. One marked ☆ Keep in the clip
  browser's Full sessions is never deleted. A recording whose clips couldn't be
  cut stays in `sessions/` instead, and you get a warning (in the log, and in Discord if "clip
  failed" messages are on).
- **Clips** are kept until you delete them. To expire them, set "Keep clips" or
  `clip_retention_days`. A clip you've marked **Keep forever** is never deleted.
- **Below 20 GB free** (`min_free_gb`) you get a warning. **Below 5 GB** (`stop_below_gb`)
  recording stops, so the drive never fills up completely; what was recorded so far is kept.

## WoW versions

- **Retail**: tested. Its PTR and beta are found too.
- **Classic**: should work for dungeons; not tested yet.
- **WoW Forever** (from 4 November 2026): its client is found as "Forever" if its game folder
  is named like the others (`_forever_`). Its beta runs from `_classic_beta_` and is found as
  "Classic beta". Its dungeons are clipped if they use a difficulty the app knows. If they
  don't, the service log shows `Not a dungeon this app knows: <name> (difficulty N)`; please
  report that line so it can be added.
- **Raids** aren't clipped yet.

## Troubleshooting

Start with `linux-wow-recorder status`, then the log: Show log in the tray menu, or
`linux-wow-recorder logs`. The log files are in `~/.local/state/linux-wow-recorder/logs/`
(tray menu: Folders and logs > Open log folder). To track a problem down, tick Detailed logging
in Settings > Advanced.

To ask someone for help, make a report file (Settings > Advanced > Make a report file, or
`linux-wow-recorder report`): a text file with your versions, settings and the end of the logs,
with webhook links, your home folder, user name, computer name and IP addresses replaced. Nothing
is sent; read it, then share it however you like.

**WoW is running but nothing is recorded.**
Is the service running? `status` shows it, and the tray icon is an empty ring when it isn't.
If the log says the recorder failed, run `linux-wow-recorder test-recorder` (while WoW is
closed) to see gpu-screen-recorder's own error.

**"No combat log covers this session."**
Combat logging was off. Type `/combatlog` in the dungeon, or install an addon that does it.
The full recording is still kept in `processed/` for 3 days.

**A session was recorded, but no clips.**
The log says how many runs it found. Runs shorter than 5 minutes are skipped (except completed
keys), and so are dungeons without a key where no boss was pulled.

**The tray icon doesn't show.**
On GNOME, install the AppIndicator extension mentioned above. Elsewhere, check that the desktop
app is running: run `linux-wow-recorder-gui` in a terminal and read what it prints.

**The settings didn't take effect.**
Saving in the settings window restarts the service when the config changed: the button says
**Save and restart service** then. After editing the config file by hand, restart it yourself:
`systemctl --user restart linux-wow-recorder`.

**Deaths or lusts look wrong.**
They come straight from the combat log, and haven't been checked against every class yet
(Feign Death, for example). Please report it, with the clip's `.json` if you can.

## Uninstalling

In the desktop app: **Settings > Advanced > Uninstall...**. Or in a terminal:

```bash
linux-wow-recorder uninstall
```

Either one stops the recorder and removes its service, login and menu entries, settings and logs.
It asks whether to delete your recordings and clips too (no, unless you tick it); if you do, only
its own `sessions/`, `processed/` and `clips/` folders go, never anything else in your output
folder, nor clips in your other folders. Installed with uv or pipx, the program is removed too.
Installed as an RPM or Arch package, it tells you the command that finishes the job, since that
needs your password:

```bash
sudo dnf remove linux-wow-recorder          # RPM
sudo pacman -R linux-wow-recorder           # Arch package
```
