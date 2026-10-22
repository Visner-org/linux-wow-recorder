#!/bin/bash
# Builds dist/linux-wow-recorder_<version>-<revision>_all.deb for Ubuntu and Debian.
# The package is put together inside an Ubuntu container, so it builds on any distro.
# Needs uv and podman (or docker). Run from anywhere: ./packaging/build-deb.sh
#   DEB_REVISION=2 ./packaging/build-deb.sh     packaging-only change (0.8.5-2)
#   DEB_IMAGE=ubuntu:26.04                      the release it's built in (the default)
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/linux_wow_recorder/__init__.py)
revision=${DEB_REVISION:-1}
image=${DEB_IMAGE:-docker.io/library/ubuntu:26.04}
engine=$(command -v podman || command -v docker || true)
[ -n "$engine" ] || { echo "Needs podman or docker." >&2; exit 1; }

rm -f dist/*.whl
uv build --wheel --out-dir dist
wheel=dist/linux_wow_recorder-$version-py3-none-any.whl

top="$PWD/build/deb"
rm -rf "$top"
mkdir -p "$top/in" "$top/out"
cp "$wheel" packaging/linux-wow-recorder-gui.desktop packaging/deb/* "$top/in/"

# docker runs as root, so the .deb is handed back to this user; rootless podman needs no chown.
owner=""
[[ $engine == *docker ]] && owner="$(id -u):$(id -g)"
"$engine" run --rm -v "$top/in:/in:ro,Z" -v "$top/out:/out:Z" \
    -e VERSION="$version" -e REVISION="$revision" -e OWNER="$owner" \
    "$image" bash /in/build.sh
cp "$top"/out/*.deb dist/
ls -1 dist/*.deb
