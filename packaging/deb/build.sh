#!/bin/bash
# Runs inside the Ubuntu container started by packaging/build-deb.sh: installs the wheel into
# a package tree the Debian way and wraps it with dpkg-deb. /in holds the files, /out gets the .deb.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3-installer dpkg-dev >/dev/null

pkg=/tmp/pkg
python3 /in/install_wheel.py /in/linux_wow_recorder-"$VERSION"-py3-none-any.whl "$pkg"
install -Dm644 /in/linux-wow-recorder-gui.desktop "$pkg/usr/share/applications/linux-wow-recorder-gui.desktop"
install -Dm644 "$pkg/usr/lib/python3/dist-packages/linux_wow_recorder/gui/icon.svg" \
    "$pkg/usr/share/icons/hicolor/scalable/apps/linux-wow-recorder.svg"
install -Dm644 /in/copyright "$pkg/usr/share/doc/linux-wow-recorder/copyright"
install -Dm755 /in/postinst "$pkg/DEBIAN/postinst"
install -Dm755 /in/prerm "$pkg/DEBIAN/prerm"

size=$(du -sk --exclude=DEBIAN "$pkg" | cut -f1)
sed -e "s/@VERSION@/$VERSION-$REVISION/" -e "s/@SIZE@/$size/" /in/control > "$pkg/DEBIAN/control"

deb=/out/linux-wow-recorder_"$VERSION-$REVISION"_all.deb
dpkg-deb --root-owner-group -Zxz --build "$pkg" "$deb"
[ -z "$OWNER" ] || chown "$OWNER" "$deb"
dpkg-deb --info "$deb" | sed -n '/Package:/,$p'
