# User guide

Linux WoW Recorder records your World of Warcraft sessions and cuts one clip per dungeon run
and per raid boss pull. Start WoW and play; when you close it, the clips are waiting.

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

| | Command line | Desktop app |
|---|---|---|
| Install | `linux-wow-recorder` | `linux-wow-recorder[gui]` |
| Download | a few hundred KB | about 400 MB (Qt) |
| Setup | questions in a terminal | a settings window |
| While playing | nothing to see | a tray icon |
| Clips | a folder of .mp4 files | a clip browser with a player and timeline |

Both record the same way, with the same service and config file. You can switch later.

## Privacy

Everything stays on your computer. No telemetry, no account. It connects only to webhooks you
add (messages never include logs, paths or character names) and, if you turn it on, GitHub
once a day for the newest version number. Details: [README](../README.md#privacy).

## Before you install

You need:

- **Linux with systemd** and **Python 3.11+** (`python3 --version`).
- **[uv](https://docs.astral.sh/uv/)** or **pipx**, unless you use the RPM, Arch package or
  `.deb`.
- **gpu-screen-recorder**:
  - CachyOS / Arch: `sudo pacman -S gpu-screen-recorder`
  - Nobara: comes with the RPM
  - Fedora, Ubuntu, Debian, Bazzite, SteamOS: the Flatpak,
    `flatpak install flathub com.dec05eba.gpu_screen_recorder`. Ubuntu needs Flatpak and
    Flathub first: `sudo apt install flatpak`, then
    `flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`
- **ffmpeg** (with `ffprobe`) from your distro.

And in WoW:

- **Combat logging on in dungeons.** Type `/combatlog` after you zone in, or use an addon
  (Raider.IO's client, an "auto combat log" addon, forever-logs-companion for WoW Forever). If
  keys still go missing, try [the WoW addon](#the-wow-addon). Without a log the session is
  recorded but not cut. A completed key is found even if logging started late; an abandoned
  key needs logging on from the start.
- **Advanced Combat Logging** on (Options > Network). Not required, but the log is more
  complete.

## Installing

### Nobara or Fedora (RPM)

```bash
sudo dnf install ./linux-wow-recorder-1.2.7-1.fc44.noarch.rpm
```

This brings in ffmpeg and Qt, and on Nobara gpu-screen-recorder. Open **Linux WoW Recorder**
from the menu.

**Plain Fedora** has no gpu-screen-recorder (dnf skips it quietly), so install the Flatpak; the
app finds it:

```bash
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install flathub com.dec05eba.gpu_screen_recorder
```

Fedora's `ffmpeg-free` is enough. **Fedora Atomic** (Silverblue, Kinoite) is read-only: use
[uv](#other-distros-uv).

**To update**, install the newer RPM. The service and tray restart into it by themselves within
a few minutes, once WoW is closed, clips are cut and the windows are closed. The RPM is built
for Fedora/Nobara 44 (`fc44`); for another release, build it there with
`./packaging/build-rpm.sh` (needs `uv` and `rpm-build`).

### CachyOS or Arch (package)

Installs system-wide, with everything from Arch's repos. It'll be on the AUR once sign-ups
reopen; until then:

```bash
sudo pacman -S --needed git base-devel
git clone https://github.com/Visner-org/linux-wow-recorder
cd linux-wow-recorder/packaging/aur
makepkg -si
```

**To update**, `git pull` and `makepkg -si` again. The uv route works on Arch too.

### Ubuntu or Debian (.deb)

For Ubuntu 25.10+ (26.04 tested) and Debian 13. Download the `.deb` from
[Releases](https://github.com/Visner-org/linux-wow-recorder/releases), then:

```bash
sudo apt install ./linux-wow-recorder_1.2.7-1_all.deb
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub com.dec05eba.gpu_screen_recorder
```

apt brings in ffmpeg, Qt and Flatpak; gpu-screen-recorder comes from Flathub.

- **To update**, install the newer `.deb`. There's no apt repository, so `apt upgrade` won't do
  it; turn on **Check for new versions once a day** (Settings > About) to hear about releases.
- **Ubuntu 24.04**, Mint 22 and Pop!_OS 24.04 lack the Qt packages: use uv.
- The `.deb` always includes the desktop app. For command line only, use uv.

### Other distros (uv)

For Bazzite, Fedora Atomic, SteamOS, Ubuntu 24.04, Mint, Pop!_OS, openSUSE and the rest. It
installs into `~/.local`: no root, no reboot. Get uv from your package manager or with
`curl -LsSf https://astral.sh/uv/install.sh | sh`, then open a new terminal so `~/.local/bin` is
on your PATH.

First, gpu-screen-recorder and ffmpeg:

| Distro | Before installing the app |
|---|---|
| Bazzite, Fedora Atomic, Aurora | ffmpeg is built in. Add Flathub (`flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo`), then the gpu-screen-recorder Flatpak |
| SteamOS (Desktop Mode) | ffmpeg and Flathub are built in. Install the Flatpak |
| openSUSE Tumbleweed | `sudo zypper install python313 ffmpeg gpu-screen-recorder git` |
| Ubuntu 24.04, Mint 22, Pop!_OS 24.04 | `sudo apt install python3 ffmpeg flatpak git`, add Flathub, then the Flatpak |

**Steam Deck:** use Desktop Mode (Game Mode isn't tried). Recording a picked window works as
is. Recording the whole screen needs gpu-screen-recorder's helper, which asks for a password:
set one for `deck` with `passwd`.

With the desktop app:

```bash
uv tool install 'linux-wow-recorder[gui] @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder-gui
```

Command line only (it then starts at every login, see [Command line](#command-line)):

```bash
uv tool install 'linux-wow-recorder @ git+https://github.com/Visner-org/linux-wow-recorder'
linux-wow-recorder setup
```

pipx works the same: `pipx install 'linux-wow-recorder[gui] @ git+https://github.com/Visner-org/linux-wow-recorder'`.
**To update**, `uv tool upgrade linux-wow-recorder`.

## Setting it up

You'll be asked for two folders:

1. **The combat log folder**: `Logs` next to WoW's `_retail_` (or `_classic_`) folder. With
   Lutris, for example:
   `~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs`.
2. **An output folder** for recordings and clips, on a drive with room (a few GB per hour).

### Desktop app

Open **Linux WoW Recorder** (or run `linux-wow-recorder-gui`). The first time, setup has three
steps:

1. **Folders.** A line under each says what it found: the newest log, or the free space.
2. **What to record**: capture, frame rate, quality and sound.
3. **Try it**: **Save and record a 5-second test**, then watch `test-recording.mkv` in the
   output folder. **Finish** sets up the recorder; it records while the app is open.

Everything else is in Settings. A ⚠ marks a page with a problem.

### Command line

```bash
linux-wow-recorder setup
```

It asks for the folders, what to record, sound, microphone and webhooks, then offers a test
recording. Everything else is in the [config file](reference.md#config-file).

> **Its last question starts the recorder at every login**, as a background service (runs as
> you, never root) that records whenever WoW runs. `linux-wow-recorder uninstall-service`
> removes it. Or answer no and run `linux-wow-recorder run` while you play (Ctrl+C stops and
> saves).

### What to record

- **A window or screen you pick** (`portal`, the default): your desktop's screen-share dialog.
  Pick the WoW window to record only WoW; alt-tabbing then shows WoW's last frame, not your
  desktop.
  - The dialog opens a few seconds after WoW's window appears, so it's on top of WoW. That needs
    `xprop` (most desktops have it); without it the dialog opens right away. With no window
    after a minute, it opens anyway.
  - A picked screen is remembered. A picked window is remembered on KDE Plasma; GNOME asks each
    time you start WoW.
  - Nothing is recorded until you pick. Meanwhile the tray and clips window offer **Record
    monitor instead** (your "Monitor instead" setting, for that session).
  - Wrong window? **Pick another window** (tray menu, or under the preview) asks again.
- **A monitor** (`DP-1`, `HDMI-A-1`, ...): just that monitor. Best with more than one screen.
- **Whole screen** (`screen`): the first monitor by connector order, which may not be your main
  one; the settings window shows which. On AMD and Intel under Wayland it needs
  gpu-screen-recorder's helper, which asks for your password once.

### The WoW addon

**Optional.** Install it if your logs miss keys or their start (clips never cut, "No combat
log covers this session", or details saying "combat log began 1:04 late"), or for level-up
and elite kill [moments](#moments).

Why it happens: logging addons turn logging on when you change zones. If you zone in before the
key is in, the dungeon has no difficulty yet; putting the key in makes it Mythic+ with no zone
change, so logging starts only in the next area.

The app's own small addon, **Linux WoW Recorder**, turns combat logging (and Advanced Combat
Logging) on in dungeons and raids:

- when the keystone slot opens, before the key starts
- when a key starts, for the others in the group
- when the dungeon's difficulty changes
- at each boss pull and after each loading screen

It also notes when you level up, and which mobs are elite or rare (from their nameplates, out
in the world), in its saved variables. WoW writes them at logout or `/reload`.

It never turns logging off and shows nothing. It works alongside other logging addons.

Before you install it:

- **It logs more.** Every dungeon and raid, Normal and LFR too, until you log out or type
  `/combatlog`. WoW never deletes logs; a night of keys can be a few hundred MB. Settings > WoW
  addon shows their size, and **Clear old logs...** moves unneeded ones to the trash (it keeps
  ones a recording still needs, and the one being written).
- **Advanced Combat Logging stays on** after you remove it; turn it off in Options > Network if
  you like.
- **It's not on CurseForge or Wago**; the app updates it. It's two small text files in
  `_retail_/Interface/AddOns/LinuxWoWRecorder` (and `_forever_`'s).
- **Retail and WoW Forever.** In Classic it shows as out of date.
- **After a big WoW patch** it shows as out of date until the app's next version: tick **Load
  out of date AddOns** meanwhile.
- It can't recover keys from before you installed it.

**Install:** Settings > **WoW addon** > **Install**, or `linux-wow-recorder install-addon`, then
`/reload` in WoW. It goes into the WoW your combat log folder belongs to, and the other games
beside it (WoW Forever). **Remove:** the same
page, or untick it in WoW's AddOns list. Uninstalling the app removes it too.

## Everyday use

Start WoW and play. The service notices WoW within seconds, however it's launched, and records.
It cuts a key's clip a minute or two after the key is done, while you play on; the rest when
WoW closes, a few seconds per run:

- **Mythic+ keys**, start to end. Abandoned keys too, if they lasted 5 minutes.
- **Dungeons without a key** (Normal, Heroic, Mythic 0, Timewalking, Follower), entering to
  leaving, if a boss was pulled.
- **Raid boss pulls**, pull to kill or wipe.

Settings > Clips and storage > **Mythic+ keys** picks which keys get a clip: the lowest key
level, and the least and most time left on the timer (below 0 is over time; abandoned keys
have no time left, so only the level applies to them).

Clips are named like `2026-10-08 19-03 Kings' Rest +12 Timed (24m52s).mp4`. A completed key is
**Timed** or **Over time** by the dungeon's time limit, which the app keeps a list of for the
current and last season (others say "Completed"). The clip browser also shows the upgrade
("Timed +2") and the time left or over.

### Moments

Short clips outside dungeons and raid boss pulls, 30 seconds either side (Settings > Clips and
storage > **Moments**):

- **Your deaths.**
- **Level-ups** (needs [the addon](#the-wow-addon)).
- **Elite and rare kills** in the open world, by you or your group (needs the addon; a mob
  counts once its nameplate was seen).

They're cut when WoW closes, since that's when WoW saves the addon's notes. Moments close
together are one clip, like `2026-11-06 16-31-29 Death + Level 12 - Mulgore.mp4`. Deaths and
kills need combat logging on in the open world too, not only in dungeons. The clip browser
lists them under **Moments**.

### The window and the tray icon (desktop app)

Opening the app starts the recorder. Closing the window keeps it in the tray, still recording;
left-click the icon to get it back. **Quit and stop recording** in the tray menu stops it (it
asks first while recording; what's recorded is saved).

**Nothing starts at login** unless you choose it in Settings > Advanced:

- **Record from every login, without opening this app**: the service starts at login and keeps
  recording when the app is closed. Then the tray menu also has **Hide tray icon (keeps
  recording)**.
- **Show the tray icon at login**: the icon (not the window) and the recorder start at login.

| Icon | Means |
|---|---|
| grey dot | waiting for WoW |
| red dot | recording |
| amber dot | cutting clips |
| badge | something needs attention (hover) |
| empty ring | the service isn't running |

The tray menu (right-click) has:

- **Pause recording this session**: nothing more until WoW closes. Resume undoes it.
- **Cut the clips so far**: for a break; recording goes on in a new file. Inside a key or pull
  it waits for the end.
- **Record monitor instead** / **Record the window again**, and **Pick another window**.
- Settings, Folders and logs, and **Version X is out...** when update checks are on.

You also get desktop notifications when clips are ready or something goes wrong.

Stock GNOME (Fedora Workstation, Debian) needs the
[AppIndicator](https://extensions.gnome.org/extension/615/appindicator-support/) extension for
tray icons. Without a tray, closing the window quits the app.

### The clip browser (desktop app)

- **Recording now**: the newest frame every 3 seconds while WoW records, to check it's the
  right window. It's a few seconds behind, and waits while the window is in the background.
- **Not ready to record yet**: a checklist of what's missing, with what to do.
- **The clips** as cards or a sortable table (the Table / Cards button). **Showing** filters
  All, Completed or Abandoned; or search by dungeon.
- **Keystones / Raids / Full sessions**: one clip per run, one per raid boss pull ("#3" is the
  third pull), or the whole recordings with every run on the timeline. Full recordings are kept
  3 days (Settings > Clips and storage > "Keep full recordings"); ☆ Keep keeps one forever. A
  recording still going shows as "Not cut yet".
- **The timeline**: boss pulls above (kill blue, wipe grey), deaths (red) and lusts (yellow)
  below. Click to play from there; hold and drag to scrub.
- **Casts**: a strip of the spells cast, scrolling with the video; drag it to scrub. **Casts**
  (or C) shows or hides it, and picks whose casts get a lane. **Major cooldowns only** shows the
  big ones: damage red, healing green, defensive blue. Casts are yours and your group's. Clips
  from before 1.2.0 can get them with `linux-wow-recorder add-casts` while their logs exist.
- **The events**: one row per boss pull ("killed in 1:44 · 41.80% forces · 2 deaths") with its
  deaths and lusts. Click to play from there. Enemy forces are counted from the log, so they
  can be a little off from the game's bar; a key whose log began late doesn't have them.
- **Highlights** save part of a clip as its own clip. The scissors button marks 30 seconds;
  drag the ends, or press I and O. Right-click a pull, death or lust for "Make a highlight of
  this". **Save highlight** takes seconds; it may start a second or two early (nearest
  keyframe).
- **Drawing**: the pencil (or D) pauses and lets you draw: pen, arrow, box, circle, text (1 to
  5), Ctrl+Z undoes. **Done** saves the picture beside the clip ("... 12m34s.png"); **Copy**
  puts it on the clipboard. "Keep clips" deletes pictures with their clip.
- **Keys**: space play/pause, arrows skip 5 s, I/O highlight start/end, C casts, D draw, F full
  screen.
- **Player**: click the video to play or pause, double-click for full screen. The speed button
  steps 1x to 16x. Drag the bar under the timeline to resize the video.
- **Buttons** beside the title: ☆ Keep (forever), open in your player, show in folder,
  [hide chat](#hiding-the-chat), [YouTube](#to-youtube), run `after_clips` again, and the trash
  (with its details and picture; restore from the trash to undo).
- **Several at once**: Ctrl+click or Shift+click, then the trash or Delete.
- **Less on screen**: Settings > Clips and storage > Clips window turns off Raids, Full
  sessions, Highlight, Hide chat and YouTube.

### The command line

```bash
linux-wow-recorder status            # is everything OK, and what is the service doing
linux-wow-recorder process           # cut what's waiting now (only when WoW isn't running)
linux-wow-recorder cut-now           # while WoW runs: cut the clips so far, keep recording
linux-wow-recorder keep <clip>       # keep a clip forever; --undo to let it expire again
linux-wow-recorder logs              # where the logs are, and the last lines
```

All commands: [reference](reference.md#commands).

## Discord or Fluxer messages

Add up to 25 webhook URLs in Settings or `setup`; every message goes to each. Discord: Server
Settings > Integrations > Webhooks > New Webhook > Copy Webhook URL. Fluxer: the channel's or
community's settings > Webhooks. Then choose the messages:

| Message | Sent when | On by default |
|---|---|---|
| WoW started | WoW started and recording began | no |
| Combat logging started | WoW started writing a combat log | no |
| WoW closed | WoW closed and the recording was saved | no |
| Clips ready | clips were cut, with bosses killed, deaths and lusts | yes |
| No runs | a session had no dungeon runs or raid pulls | no |
| No combat log | no combat log covers a session | yes |
| Combat log off | WoW has run 10 minutes with no combat log | yes |
| Recording failed | the recorder didn't start, or stopped | yes |
| Low disk space | space is low, or recording stopped for it | yes |
| Clip failed | a clip couldn't be cut | yes |
| Hook failed | your `after_clips` command failed | yes |
| Cleanup | old recordings were deleted | no |

Treat webhook URLs like passwords: anyone with one can post in that channel.

## Sending clips somewhere

Set **After clips** (`after_clips`) to a command; it runs after each session with the new
clips' paths as arguments, in the background. Its output goes to the service log. For example:

```bash
#!/bin/sh
# Copies the new clips, and their .json details, to a server.
for clip in "$@"; do
    rsync -a "$clip" "${clip%.mp4}.json" myserver:/srv/clips/
done
```

### To Fireshare

[Fireshare](https://github.com/fireshare-app/fireshare) 1.9+ takes uploads with a token (your
account > Upload tokens):

```bash
#!/bin/sh
# Uploads the new clips to Fireshare and deletes each video here once Fireshare has it.
# The .json and .jpg stay, for "Also show clips from".
for clip in "$@"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $FIRESHARE_TOKEN" \
           -F "file=@$clip" -F "game=World of Warcraft" https://fireshare.example.com/api/upload/token)
    case "$code" in
        201|409) rm -f "$clip" ;;
        *) echo "$clip: Fireshare answered $code"; exit 1 ;;
    esac
done
```

If a proxy limits upload size, use `/api/upload/token/chunked` (fields `blob`, `chunkPart` from
1, `totalChunks`, `checkSum`, `fileName`, `fileSize`). Keep the token out of files others can
read.

### To YouTube

The YouTube button doesn't upload anything; your browser does. It:

1. copies a description with chapters (each boss pull, kill or wipe)
2. opens YouTube's upload page
3. shows the clip in your file manager

Drag the clip onto the page and paste the description. The title comes from the file name.

- Chapters need three marks 10 seconds apart, so a single pull gets no chapters.
- New YouTube accounts are limited to 15 minutes until phone-verified (YouTube Studio >
  Settings > Channel > Feature eligibility).
- If your `after_clips` moves clips away, add that folder to "Also show clips from" and the
  button finds them there.

### Hiding the chat

The eye button makes a copy of a clip with a black box over your chat. Your clips are never
changed.

1. Pick the clip (pause where the chat shows, if you like) and press the button.
2. Drag a box over the chat; it's remembered for next time.
3. **Make copy.**

The copy, "... (chat hidden).mp4", appears in the list like any clip. The box stays in one spot
for the whole clip. It's re-encoded: with NVIDIA about a seventh of the clip's length, slower on
AMD, Intel or the CPU. It expires like other clips unless you ☆ Keep it.

### Keeping clips somewhere else

The app never moves or uploads clips itself; that's your After clips command. To still see
moved clips in the clip browser:

1. **Move only the `.mp4`**; leave its `.json` and `.jpg` in `clips/` (a few KB).
2. **Add the folder** to Settings > Clips and storage > "Also show clips from". It must be on
   this computer: a second drive, or a share you've mounted.

They're then listed and play with their picture and timeline. Details are matched by name, so a
copy renamed with hyphens for spaces (Fireshare does this) still finds them.

| What you do | What the clip browser shows |
|---|---|
| Move the `.mp4`, leave the `.json` and `.jpg` | everything |
| Move all three, same name | everything |
| Move the `.mp4`, delete its details | dungeon, level, result and length, from the name |
| Copy it and let `clips/` expire it | the copy, from its name only: cleanup deletes the details. Move instead |

- The app never changes anything in those folders: Keep and "Keep clips" don't apply; only
  **Move to trash** touches them (it asks first where there's no trash).
- A folder that isn't mounted is skipped, with a ⚠ in Settings.
- On a network share, changes from another computer show after reopening the window.

## Disk space and cleanup

The app writes only inside your output folder:

```
<output>/sessions/    the recording being made, and recordings not cut yet
<output>/processed/   full recordings already cut, each with a .json of details
<output>/clips/       the clips, each with a .json of details and a .jpg picture
```

- **Full recordings** are deleted after 3 days ("Keep full recordings",
  `session_retention_days`; 0 means never), unless ☆ Kept. One whose clips couldn't be cut
  stays in `sessions/`, with a warning.
- **Clips** stay until you delete them, unless you set "Keep clips" (`clip_retention_days`).
  Kept clips are never deleted.
- **Below 20 GB free** (`min_free_gb`) you get a warning; **below 5 GB** (`stop_below_gb`)
  recording stops and what was recorded is kept.

## WoW versions

- **Retail**: tested, with its PTR and beta.
- **Classic**: should work; not tested yet.
- **WoW Forever** (`_forever_`): found as "Forever"; its combat logs are read from
  `_forever_/Logs` beside your retail Logs folder. Its beta (`_classic_beta_`) as "Classic
  beta". If a dungeon isn't clipped, the log says `Not a dungeon this app knows: <name>
  (difficulty N)`; please report that line.

## Troubleshooting

Start with `linux-wow-recorder status`, then the log (Show log in the tray menu, or
`linux-wow-recorder logs`; files in `~/.local/state/linux-wow-recorder/logs/`). For more detail,
tick Detailed logging in Settings > Advanced.

To ask for help, make a report file (Settings > Advanced > Make a report file, or
`linux-wow-recorder report`). Your webhook links, home folder, names and IPs are blanked out.
Nothing is sent: read it, then share it.

**WoW runs but nothing is recorded.** Check the service is running (`status`; an empty ring in
the tray). Run `linux-wow-recorder test-recorder` with WoW closed to see the recorder's error.

**"No combat log covers this session."** Logging was off: `/combatlog`, a logging addon, or
[the WoW addon](#the-wow-addon). The full recording stays in `processed/` for 3 days.

**Recorded, but no clips.** The log says how many runs it found. Runs under 5 minutes (except
completed keys) and keyless dungeons with no boss pulled are skipped.

**No tray icon.** On GNOME, install the AppIndicator extension. Elsewhere, run
`linux-wow-recorder-gui` in a terminal and read what it prints.

**Settings didn't take effect.** **Save and restart service** applies them. After editing the
config file by hand: `systemctl --user restart linux-wow-recorder`.

**Deaths or lusts look wrong.** They come straight from the combat log and aren't checked for
every class yet. Please report it, with the clip's `.json` if you can.

## Uninstalling

**Settings > Advanced > Uninstall...**, or:

```bash
linux-wow-recorder uninstall
```

It removes the service, login and menu entries, settings and logs. It asks whether to delete
your recordings and clips too (only its own `sessions/`, `processed/` and `clips/` folders).
With uv or pipx the program goes too; for a package, finish with:

```bash
sudo dnf remove linux-wow-recorder          # RPM
sudo pacman -R linux-wow-recorder           # Arch package
sudo apt remove linux-wow-recorder          # .deb
```
