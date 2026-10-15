# Built by packaging/build-rpm.sh from the wheel it makes; not for building from a source RPM.
# For Fedora-based distros (Nobara, Fedora); every dependency comes from the distro's repos.
%global debug_package %{nil}

Name:           linux-wow-recorder
Version:        %{app_version}
Release:        1%{?dist}
Summary:        Records World of Warcraft sessions and cuts one clip per dungeon run
License:        GPL-3.0-or-later
BuildArch:      noarch
Source0:        %{wheel_name}
Source1:        linux-wow-recorder-gui.desktop
Source2:        LICENSE

Requires:       python(abi) = %{python_abi}
Requires:       python3-pyside6
Requires:       gpu-screen-recorder
# Either ffmpeg (RPM Fusion) or ffmpeg-free provides these.
Requires:       /usr/bin/ffmpeg
Requires:       /usr/bin/ffprobe
Requires:       grep
Requires:       systemd

%description
Watches for World of Warcraft, records the whole session with gpu-screen-recorder, and afterwards
cuts one clip per Mythic+ run from the combat log. A tray icon shows when it's recording; the
window lists the clips with their bosses, deaths and lusts.

%prep
cp %{SOURCE2} .

%build

%install
pip3 install --root %{buildroot} --prefix /usr --no-deps --no-index --no-compile \
    --no-warn-script-location --disable-pip-version-check %{SOURCE0}
rm -f %{buildroot}%{python_sitelib}/*.dist-info/direct_url.json
sed -i '/direct_url.json/d' %{buildroot}%{python_sitelib}/*.dist-info/RECORD
echo rpm > %{buildroot}%{python_sitelib}/linux_wow_recorder-%{app_version}.dist-info/INSTALLER
install -Dm644 %{SOURCE1} %{buildroot}%{_datadir}/applications/linux-wow-recorder-gui.desktop
install -Dm644 %{buildroot}%{python_sitelib}/linux_wow_recorder/gui/icon.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/linux-wow-recorder.svg

%files
%license LICENSE
%{_bindir}/linux-wow-recorder
%{_bindir}/linux-wow-recorder-gui
%{python_sitelib}/linux_wow_recorder/
%{python_sitelib}/linux_wow_recorder-%{app_version}.dist-info/
%{_datadir}/applications/linux-wow-recorder-gui.desktop
%{_datadir}/icons/hicolor/scalable/apps/linux-wow-recorder.svg

%preun
if [ "$1" -eq 0 ]; then
    echo "Before removing: each user who set it up should run 'linux-wow-recorder uninstall-service'"
    echo "(the service lives in their ~/.config/systemd/user)."
fi

%changelog
* Thu Oct 15 2026 Linux WoW Recorder - 0.8.2-1
- Completed keys say Timed or Over time, by the dungeon's time limit; the clips window shows the
  keystone upgrade and the time left or over

* Thu Oct 15 2026 Linux WoW Recorder - 0.8.1-1
- Settings > Clips and storage > Clips window: turn off the Raids and Full sessions views and the
  Hide chat and YouTube buttons

* Thu Oct 15 2026 Linux WoW Recorder - 0.8.0-1
- Cutting clips is much faster
- YouTube button in the clips window: copies a description with chapters, opens the upload page
  and shows the clip to drag onto it
- Hide chat: a copy of a clip with a black box over the chat, made when you ask
- Full screen in the clips window, with the timeline kept; a mute button by the volume

* Wed Oct 14 2026 Linux WoW Recorder - 0.7.0-1
- Several clips at once: Ctrl+click or Shift+click in the clips window, then the trash can
  (or Delete) moves them all to the trash

* Tue Oct 13 2026 Linux WoW Recorder - 0.6.0-1
- After an update, the service and the tray app restart into the new version by themselves:
  the service once WoW is closed and the clips are cut, the tray app once its windows are closed
- A clean uninstall: Settings > Advanced > Uninstall..., or the uninstall command
- While the screen-share dialog waits for a pick, nothing is recorded and the app now says so
  (tray, clips window); closing WoW then no longer waits 30 s
- Record monitor instead: for a session, from the tray or the clips window (Settings: Monitor
  instead)
- Pick another window: forgets the screen-share pick and opens the dialog again (tray, clips
  window)
- The player is black with the app's icon until a clip plays (was an empty grey frame)
- Raids: a clip per boss pull (kills and wipes), in the clips window's new Raids view, and one
  Discord line per raid; Settings > Clips and storage > Raid boss clips (every pull, kills
  only, or none)
- New versions: if you turn it on (setup asks; Settings > About), the tray app asks GitHub once a
  day for the newest version and says when one is out. Nothing about you is sent; installing
  is up to you. "Check now" asks once.

* Mon Oct 12 2026 Linux WoW Recorder - 0.5.0-1
- Sound: "Only WoW's sound" recorded silence; the game's sound is "World of Warcraft" in PipeWire
- A key WoW announces again after a crash stays one run (was split into Abandoned + Completed)
- When WoW's window goes away mid-session, the recording stops and a new one starts when WoW is
  back (it used to record the crashed window's last frame)
- A cut that comes out short (a disk read error) fails and is retried instead of being saved
- Lusts found by their debuff: no more double counts; Harrier's Cry and current drums added
- Clips are cut in the background, so a relaunched WoW is recorded meanwhile
- Pause recording for the rest of a session (tray menu, or the pause/resume commands)
- Clip browser: cards with pictures, a timeline of pulls, deaths and lusts, events by boss, a
  setup checklist; the live preview waits while the window is in the background
- Settings: pages in a sidebar, a three-step first-run setup, a check under each field; saving
  restarts the service only when needed
- Webhooks are sent in the background; clips go to the trash instead of being deleted
- Clip browser: Keystones / Full sessions; full recordings get a details file, can be played
  with their runs on the timeline, and kept forever with Keep; the "No key" filter is gone
- The version is in each window's title; Settings has an About page with the license
- Clip browser: "Also show clips from" other folders on this computer; a moved clip whose .json
  stayed behind keeps its details, even renamed with hyphens; deleting one in a read-only folder
  says why it can't instead of failing
* Sun Oct 11 2026 Linux WoW Recorder - 0.4.1-1
- Clips window: no black bars in the player when no clip is loaded; it says where clips will
  appear instead

* Sun Oct 11 2026 Linux WoW Recorder - 0.4.0-1
- Records "A window or screen you pick" (the screen-share dialog) by default; Settings explains
  it in a popup and a ? button
- "Whole screen" names the monitor it records: the first one gpu-screen-recorder lists, which
  may not be the desktop's main monitor
- Picking a monitor in Settings shows a big red "THIS ONE" on it
- Clips window: "Recording now" shows the newest frame of the recording in progress

* Sun Oct 11 2026 Linux WoW Recorder - 0.3.0-1
- Log files in ~/.local/state/linux-wow-recorder/logs/, kept on this computer and cleaned of
  webhook links, home folder, user and computer names and IP addresses
- Settings: Detailed logging, Make a report file; tray: Open log folder; logs and report commands
- Webhook messages no longer include log lines or folder paths
- Each log starts with the app version, distro, kernel and desktop

* Sun Oct 11 2026 Linux WoW Recorder - 0.2.0-1
- Records only WoW's sound by default; all desktop sound or none are options
- Licensed GPL-3.0-or-later
- Package carries no build machine name or private addresses

* Sun Oct 11 2026 Linux WoW Recorder - 0.1.0-1
- First package
