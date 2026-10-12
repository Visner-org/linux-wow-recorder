# User guide

Linux WoW Recorder records your World of Warcraft sessions and cuts one clip per dungeon run.
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
telemetry, update checks or crash reporting, and no account. The only connection it can make is a
message to the Discord or Fluxer webhooks you add yourself, and those messages never include log
lines, paths or anyone's character name. The full details, and how to check them yourself, are
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
sudo dnf install ./linux-wow-recorder-0.4.0-1.fc44.noarch.rpm
```

dnf installs gpu-screen-recorder, ffmpeg and Qt from your distro's repos along with it. The app
appears in the menu as **Linux WoW Recorder**. To update, install the newer RPM the same way,
then `systemctl --user restart linux-wow-recorder`. The RPM is built for one Fedora release
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

To update, `git pull` and run `makepkg -si` again, then
`systemctl --user restart linux-wow-recorder`. The uv route below works on Arch too.

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

To update later, run `uv tool upgrade linux-wow-recorder`, then
`systemctl --user restart linux-wow-recorder`.

## Setting it up

You'll be asked for two folders:

1. **Your combat log folder**: the `Logs` folder next to WoW's `_retail_` (or `_classic_`)
   folder, wherever your launcher put WoW. With Lutris, for example:
   `~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs`.
2. **An output folder** for recordings and clips. Pick a drive with room: a session takes a few
   GB per hour.

### Desktop app

Open **Linux WoW Recorder** from the app menu (or run `linux-wow-recorder-gui`). The first time, the settings window opens by itself:

1. Fill in the two folders (the Browse buttons help). Under the combat log folder, the newest
   log found is shown, so you know you picked the right one.
2. Check the capture settings. "What to record" starts on "A window or screen you pick": the
   test recording opens the screen-share dialog, where you choose the WoW window or its monitor.
3. Press **Save and record a 5-second test**. The result shows under the button, and the video is
   saved as `test-recording.mkv` in the output folder. Watch it to check the picture and sound.
4. Press **Save and start**. This installs the background service and shows the tray icon at
   every login.

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
  so the dialog may come back each time. It never needs root.
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
- **Right-click** opens the menu: the status, the last session, the clips folder, start and stop
  for the service, the log, and Settings.

You also get a desktop notification when clips are ready or something goes wrong.

Closing the tray icon doesn't stop the recording: the service does the recording, not the
icon. To stop both, use **Quit (stop recording and close)** in the menu. If the service is set
to start at login, it comes back at the next login; untick that in Settings to stop it for good.

On GNOME, tray icons need the
[AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/)
extension. KDE and most other desktops show them as they are.

### The clip browser (desktop app)

- **Recording now**, at the top left while WoW is being recorded: the newest frame of the
  recording, every 3 seconds, so you can check it records the right screen or window. It's a few
  seconds behind, because the recording is written to disk in chunks.
- **The list** on the left: every clip with its date, dungeon, level, result, length and
  deaths. Sort by any column, filter with All / Completed / Abandoned / No key, or search by
  dungeon.
- **The timeline** under the player shows the run:
  - tall blue ticks: boss kills
  - grey ticks: pulls and wipes
  - red ticks: deaths
  - amber ticks: Bloodlust and other lusts (Heroism, Time Warp, Primal Rage, Fury of the
    Aspects, drums)

  Hover a tick to see what it is, and click it to jump there.
- **The event list** below has the same events with their times. Clicking one plays from a few
  seconds before it.
- **Keys**: space plays and pauses; the left and right arrows skip 5 seconds.
- **Buttons**: open the clip in your own video player, show it in the file manager, keep it
  forever, run your `after_clips` command on it again, or delete it.

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
| No runs | a session had no dungeon runs | no |
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

## Disk space and cleanup

The app writes only inside three folders in your output folder:

```
<output>/sessions/    the recording being made, and recordings not cut yet
<output>/processed/   full recordings already cut
<output>/clips/       the clips, each with a .json of details beside it
```

- **Full recordings** are deleted 3 days after they're made. You can change this in "Keep full
  recordings" or `session_retention_days`; 0 means never.
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
(tray menu: Open log folder). To track a problem down, tick Detailed logging in Settings.

To ask someone for help, make a report file (Settings > Make a report file, or
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
Saving in the settings window restarts the service. After editing the config file by hand,
restart it yourself: `systemctl --user restart linux-wow-recorder`.

**Deaths or lusts look wrong.**
They come straight from the combat log, and haven't been checked against every class yet
(Feign Death, for example). Please report it, with the clip's `.json` if you can.

## Uninstalling

```bash
linux-wow-recorder uninstall-service
linux-wow-recorder-gui --autostart off      # desktop app only
sudo dnf remove linux-wow-recorder          # RPM install
uv tool uninstall linux-wow-recorder        # or, for uv: (pipx uninstall linux-wow-recorder)
```

Your config (`~/.config/linux-wow-recorder/`) and your recordings and clips are left alone;
delete them by hand if you want them gone.
