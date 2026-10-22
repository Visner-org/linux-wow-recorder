"""Installs the wheel into a package tree with Debian's own layout (deb_system):
/usr/lib/python3/dist-packages, which every Python 3 on the system shares, and /usr/bin.

Usage: python3 install_wheel.py <wheel> <package root>
"""

import sys
import sysconfig

from installer import install
from installer.destinations import SchemeDictionaryDestination
from installer.sources import WheelFile

wheel, root = sys.argv[1:]
paths = sysconfig.get_paths("deb_system")
scheme = {key: paths[key] for key in ("purelib", "platlib", "scripts", "data")}
# No bytecode here: postinst compiles it with py3compile, like Debian's own Python packages.
destination = SchemeDictionaryDestination(scheme, interpreter="/usr/bin/python3",
                                          script_kind="posix", destdir=root,
                                          bytecode_optimization_levels=())
with WheelFile.open(wheel) as source:
    install(source, destination, {"INSTALLER": b"deb\n"})
