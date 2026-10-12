#!/bin/bash
# Builds dist/linux-wow-recorder-<version>-1.<dist>.noarch.rpm for Fedora-based distros.
# Needs uv and rpm-build. Run from anywhere: ./packaging/build-rpm.sh
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/linux_wow_recorder/__init__.py)
rm -rf build/rpm dist/*.whl
uv build --wheel --out-dir dist
wheel=$(basename dist/linux_wow_recorder-"$version"-*.whl)

top="$PWD/build/rpm"
mkdir -p "$top/SOURCES"
cp "dist/$wheel" packaging/linux-wow-recorder-gui.desktop LICENSE "$top/SOURCES/"
# The RPM installs into the system Python's site-packages, so it's tied to that Python version.
abi=$(/usr/bin/python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
sitelib=/usr/lib/python$abi/site-packages
# _buildhost: otherwise the package records this computer's name.
rpmbuild -bb packaging/linux-wow-recorder.spec \
    --define "_topdir $top" \
    --define "app_version $version" \
    --define "wheel_name $wheel" \
    --define "python_sitelib $sitelib" \
    --define "python_abi $abi" \
    --define "_buildhost localhost"
cp "$top"/RPMS/noarch/*.rpm dist/
ls -1 dist/*.rpm
