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
