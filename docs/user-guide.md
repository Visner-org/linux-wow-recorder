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
- **[uv](https://docs.astral.sh/uv/)** or **pipx**, to install the app (not needed for the RPM,
  the Arch package or the `.deb`).
- **gpu-screen-recorder**, which does the recording:
  - CachyOS / Arch: `sudo pacman -S gpu-screen-recorder`
  - Nobara: the RPM below brings it in from Nobara's repos
  - Fedora: not in its repos; use the Flatpak (next line)
  - anywhere, including Bazzite and SteamOS: `flatpak install flathub com.dec05eba.gpu_screen_recorder`
  - Ubuntu and Debian: the Flatpak too (they don't have it in their repos). Ubuntu doesn't come
    with Flatpak or Flathub; add both first:
    `sudo apt install flatpak` and
    `flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`
- **ffmpeg** (with `ffprobe`), from your distro.

And in WoW:

- **Combat logging on in dungeons.** WoW doesn't log by itself. Type `/combatlog` after you
  zone in, or let an addon do it: Raider.IO's client, an "auto combat log" addon, or for WoW
  Forever, forever-logs-companion. Without a log, the session is still recorded but can't be
  cut into clips. If logging starts after the key does (an addon can be late), a completed key
  is still found: its start is worked out from its in-game time. An abandoned key needs logging
  on from the start.
- **Advanced Combat Logging** on (Options > Network). It isn't needed to find runs, but it
  makes the log more complete.

## Installing

### Nobara or Fedora (RPM)

```bash
sudo dnf install ./linux-wow-recorder-1.0.0-1.fc44.noarch.rpm
```

dnf installs ffmpeg and Qt from your distro's repos along with it, and on Nobara
gpu-screen-recorder too. The app appears in the menu as **Linux WoW Recorder**.

On **plain Fedora**, gpu-screen-recorder isn't in the repos (dnf skips it without an error), so
install its Flatpak; the settings window finds it by itself:

```bash
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install flathub com.dec05eba.gpu_screen_recorder
```

Fedora's own ffmpeg (`ffmpeg-free`) is enough: it has the H.264 support the app needs. On
**Fedora Atomic** (Silverblue, Kinoite), which is read-only, use the uv steps under
[Other distros (uv)](#other-distros-uv) instead.

To update, install the newer RPM the same way.
The service and the tray app restart into the new version by themselves within a few minutes, but only when it's safe: the service waits until WoW is closed, the clips are cut and any After clips command has finished, and the tray app waits until its windows are closed. (Updating from 0.5.0 or earlier needs one restart by hand: `systemctl --user restart linux-wow-recorder`, and quit and start the tray app.) The RPM is built for one Fedora release
(`fc44` is Fedora/Nobara 44); on another release, build it there with
`./packaging/build-rpm.sh` (needs `uv` and `rpm-build`).

### CachyOS or Arch (package)

The package in `packaging/aur/` installs the app system-wide, with every dependency from Arch's
own repos (gpu-screen-recorder, ffmpeg, PySide6). It'll be on the AUR (`paru -S
linux-wow-recorder`) once AUR sign-ups reopen; until then, build it from the repo:

```bash
sudo pacman -S --needed git base-devel
git clone https://github.com/Visner-org/linux-wow-recorder
cd linux-wow-recorder/packaging/aur
makepkg -si
```

To update, `git pull` and run `makepkg -si` again; the app restarts into it as with the RPM
above. The uv route below works on Arch too.

### Ubuntu or Debian (.deb)

For Ubuntu 25.10 and newer (26.04 is tested) and Debian 13. Download the `.deb` from the
[Releases](https://github.com/Visner-org/linux-wow-recorder/releases) page, then:

```bash
sudo apt install ./linux-wow-recorder_1.0.0-1_all.deb
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub com.dec05eba.gpu_screen_recorder
```

apt installs ffmpeg, Qt (PySide6) and Flatpak from your distro's repos along with it.
gpu-screen-recorder isn't in Ubuntu's or Debian's repos, so it comes from Flathub; the settings
window finds the Flatpak by itself. The app appears in the menu as **Linux WoW Recorder**.

- **To update**, download the newer `.deb` and install it the same way; the app restarts into it
  as with the RPM above. There's no apt repository yet, so `apt upgrade` doesn't update it.
  Turn on **Check for new versions once a day** (Settings > About) to hear about new releases.
- **Ubuntu 24.04** and the distros built on it (Mint 22, Pop!_OS 24.04) don't have the Qt
  packages it needs, and apt says so. Use uv instead (next section).
- **Without the desktop app**: the `.deb` always includes it. For the command line only, use uv.

### Other distros (uv)

For Bazzite, Fedora Atomic, SteamOS, Ubuntu 24.04, Mint 22, Pop!_OS, openSUSE and any other
distro; CachyOS and Arch can use it too.

These install the app into your home folder (`~/.local`) with [uv](https://docs.astral.sh/uv/),
so they also work on read-only systems like Bazzite and SteamOS: no root, no reboot. Get uv from
your package manager (`sudo pacman -S uv` on CachyOS and Arch) or with its install script
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). After the install script, open a new
terminal so `~/.local/bin` is on your PATH; otherwise `linux-wow-recorder-gui` isn't found.

First, gpu-screen-recorder and ffmpeg, depending on your distro:

| Distro | Before installing the app |
|---|---|
| Bazzite, Fedora Atomic (Silverblue, Kinoite), Aurora | ffmpeg is built in. `flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`, then `flatpak install flathub com.dec05eba.gpu_screen_recorder` |
| SteamOS (Steam Deck, Desktop Mode) | ffmpeg and Flathub are built in. `flatpak install flathub com.dec05eba.gpu_screen_recorder` |
| openSUSE Tumbleweed | `sudo zypper install python313 ffmpeg gpu-screen-recorder git` (no Flatpak needed) |
| Ubuntu 24.04, Mint 22, Pop!_OS 24.04 | `sudo apt install python3 ffmpeg flatpak git`, the Flathub `remote-add` line above, then the Flatpak |

**Steam Deck:** "a window or screen you pick" works as is. Recording the whole screen needs
gpu-screen-recorder's helper, which asks for your password, and the `deck` user has none until
you set one with `passwd`. Only Desktop Mode is covered here; Game Mode isn't tried.

With the desktop app:

```bash
uv tool install 'linux-wow-recorder[gui] @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder-gui
```

Command line only:

```bash
uv tool install 'linux-wow-recorder @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder setup
```

> **Command line only means it starts at every login.** There's no app to open, so setup ends
> by offering to install the recorder as a background service that starts every time you log
> in (see [Command line](#command-line) below).

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
   sound. **Finish** sets up the recorder service; it records while the app is open.

Everything else (clips and storage, Discord messages, the recorder command, logs) is in
Settings later, on pages down the left. A line under a field says when something's wrong with
it, and a page with a problem gets a ⚠ beside its name.

### Command line

```bash
linux-wow-recorder setup
```

It asks for the two folders, what to record, which sound (only WoW's, the default; all desktop
sound; or none) and whether to add your microphone, and Discord or
Fluxer webhooks (optional). Then it offers a 5-second test recording. Everything else is in the
config file (see the [reference](reference.md#config-file)).

> **Its last question starts the recorder at every login.** Without the desktop app, the
> recorder is a background service (a systemd user service: it runs as you, never as root) that
> starts every time you log in and records whenever WoW runs, until you remove it with
> `linux-wow-recorder uninstall-service`. Answer no to start it yourself instead:
> `linux-wow-recorder run` in a terminal while you play (Ctrl+C stops it and saves the
> recording).

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

Clips are named like `2026-10-08 19-03 Kings' Rest +12 Timed (24m52s).mp4`. A completed
key's length is the in-game timer.

A completed key is **Timed** or **Over time**, by the dungeon's time limit. The combat log
doesn't have the limits, so the app keeps a list of the current and last season's dungeons and
updates it each season. A dungeon not on it yet says "Completed". The clip browser also shows the
keystone upgrade ("Timed +2") and the time left or over.

### The window and the tray icon (desktop app)

Opening the app shows its window, with its icon on the taskbar, and starts the recorder.
Closing the window doesn't quit: the app stays in the tray (it tells you the first time), and
keeps recording. Open it from the menu again, or left-click the tray icon, to get the window
back. To stop, use **Quit and stop recording** in the tray menu.

**Nothing starts at login** unless you choose it, in Settings > Advanced:

- **Record from every login, without opening this app**: the recorder service starts at login
  by itself, and keeps recording after the app is closed. It runs as you (a systemd user
  service in `~/.config/systemd/user/`), never as root, and stops when you log out.
- **Show the tray icon at login**: the tray icon starts at login (only the icon, not the
  window), and with it the recorder.

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

**Quit and stop recording** stops both (it asks first while WoW is being recorded; the recording
so far is saved). When the recorder is set to start at login, the menu also has **Hide tray
icon (keeps recording)**, and the recorder comes back at the next login.

On stock GNOME (Fedora Workstation, Debian), tray icons need the
[AppIndicator and KStatusNotifierItem Support](https://extensions.gnome.org/extension/615/appindicator-support/)
extension. KDE, COSMIC (Pop!_OS 24.04), Ubuntu's GNOME and most other desktops show them as they
are. Without a tray, closing the window quits the app (and stops the recorder, unless it starts
at login).

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
  deaths and lusts, ★ when kept) or as a table you can sort by any column (the Table button switches, and then Cards switches back).
  The Showing button steps through All, Completed and Abandoned; or search by dungeon.
- **Clips you move elsewhere**: add the folder in Settings > Clips and storage > "Also show
  clips from", and the list shows the clips there too, saying which folder each is in. If you
  leave a clip's `.json` (and `.jpg`) in `clips/` when you move the video, it keeps its picture,
  deaths, lusts and bosses, even if the copy was renamed with hyphens for spaces. The app never
  changes anything in those folders, and Keep and "Keep clips" don't apply there.
- **Keystones / Raids / Full sessions**, above the list: Keystones lists the clips, one per
  dungeon run; Raids lists one per raid boss pull ("#3" is the third pull of that boss), filtered
  with Showing (All, Kills, Wipes) and searched by boss or raid; Full sessions lists the whole recordings they were cut from, one each time you played, with
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
- **Highlights**: save part of a clip as a clip of its own, to share one boss kill or a wipe.
  The scissors button beside the title marks 30 seconds from where the video is; drag either
  end on the timeline, or play to a moment and press I (start here) or O (end here). Right-click
  a boss pull, death or lust in the events for "Make a highlight of this", which marks the pull
  from just before it to just after the kill or wipe, or the moments around a death or lust.
  **Save highlight** puts it in your clips folder in seconds, named after the clip with the times
  ("... highlight 12m03s-12m49s"), with only the bosses, deaths and lusts inside it. The video
  isn't re-encoded, so it starts on the nearest keyframe before the start you picked, at most a
  second or two early. Esc stops. A highlight is a clip like the others: Keep, the trash,
  YouTube and `after_clips` work on it.
- **Keys**: space plays and pauses; the left and right arrows skip 5 seconds; I and O mark a
  highlight's start and end; F goes full screen and back (Esc leaves it too).
- **Full screen**: the button at the end of the timeline, F, or a double-click on the video.
  The video fills the screen with Play and the timeline under it.
- **Volume**: the slider after the time; the speaker before it mutes and unmutes.
- **Buttons**: **Play**; beside the title, ☆ Keep (keep it forever), open it in your own video
  player, show it in the file manager, hide the chat (see [Hiding the chat](#hiding-the-chat)),
  upload it to YouTube (see
  [To YouTube](#to-youtube)), run your `after_clips` command on it again (only there
  when one is set), and the red trash can: move it to the trash (with its details and picture;
  restore them from the trash to undo).
- **Several at once**: Ctrl+click picks clips one by one, Shift+click a range. The trash can
  (or the Delete key) then moves them all to the trash, after one question.
- **Less on screen**: Settings > Clips and storage > Clips window turns off the Raids view, the
  Full sessions view, the Highlight button, the Hide chat button and the YouTube button. All are
  on by default.

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
the file name, for example "2026-10-14 20-05 Temple of Sethraliss +12 Timed (20m58s)".

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

### Hiding the chat

To share a clip without your chat (whispers, guild chat), the clip browser's hide button (an
eye, beside "show in folder") makes a copy with a black box over it. It's only done when you
ask; normal clips are never changed.

1. Pick the clip (pause it on a moment where the chat shows, if you like) and press the button.
2. Drag a box over your chat on the picture: drag inside the box to move it, its red corner to
   resize it, or anywhere else to draw a new one. The box is remembered for next time.
3. **Make copy.** A progress bar shows how far it is; you can keep using the window.

The copy is saved beside the clip as "... (chat hidden).mp4" (in `clips/` if the clip's folder
is read only) and shows up in the list like any clip, so the YouTube button works on it too.

- The box is in the same spot for the whole clip. If you move your chat frame in WoW, move
  the box too.
- The video has to be encoded again, unlike cutting. With an NVIDIA card that takes about a
  seventh of the clip's length (a 20-minute clip: about 3 minutes); on AMD or Intel graphics
  (VA-API) or the CPU it's slower.
- The copy is deleted after "Keep clips" days like other clips; ☆ Keep it to keep it.

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
sudo apt remove linux-wow-recorder          # .deb
```
