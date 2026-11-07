"""Finds a running WoW client in /proc.

However WoW is installed (Lutris, Heroic, Bottles, Faugus, Steam/Proton, plain Wine, Flatpak
builds of those), it ends up as a Wine process whose command line names the game's .exe. The
host sees processes inside Flatpak sandboxes too, so one scan of /proc covers all of them.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

# Wow.exe, WowT.exe (PTR), WowB.exe (beta), WowClassic.exe, WowClassicT.exe, WowClassicB.exe,
# and any other Wow*.exe a new game version brings (WoW Forever's name wasn't known when this was
# written). Wine sometimes puts the whole Windows command line in one argument, hence the space.
_WOW_EXE = re.compile(r"(?:^|[\\/ ])(wow\w*)\.exe(?:\s|$)", re.IGNORECASE)
# The game folder an unknown .exe is in: "_forever_", "_classic_beta_", ...
_GAME_FOLDER = re.compile(r"[\\/]_(\w+?)_[\\/][^\\/]*$")
# Programs next to the game that aren't the game.
_HELPERS = ("voiceproxy", "error", "launcher", "crash", "update", "wowup")

FLAVORS = {
    "wow": "Retail", "wowt": "Retail PTR", "wowb": "Retail beta",
    "wowclassic": "Classic", "wowclassict": "Classic PTR", "wowclassicb": "Classic beta",
}


def wow_process(args: list[str]) -> tuple[str, str] | None:
    """(flavor, .exe name) of the WoW a process with these arguments is running, e.g.
    ("Retail", "Wow.exe"), or None if it isn't WoW."""
    # The Battle.net launcher and its agent can name the game's .exe in their arguments.
    if any("agent.exe" in a.lower() or "battle.net" in a.lower() for a in args):
        return None
    for arg in args:
        m = _WOW_EXE.search(arg)
        if not m:
            continue
        exe = m.group(1).lower()
        folder = _GAME_FOLDER.search(arg[:m.end()].rstrip())
        if exe == "wow" and folder and folder.group(1).lower() != "retail":
            # Another game run by the same Wow.exe name: WoW Forever's is _forever_/Wow.exe.
            return folder.group(1).replace("_", " ").title(), f"{m.group(1)}.exe"
        if exe in FLAVORS:
            return FLAVORS[exe], f"{m.group(1)}.exe"
        if any(h in exe for h in _HELPERS):
            continue
        # An unknown name counts only inside a game folder ("_forever_"), so other programs
        # named Wow-something (addon managers and the like) don't start a recording.
        if folder:
            return folder.group(1).replace("_", " ").title(), f"{m.group(1)}.exe"
    return None


def wow_flavor(args: list[str]) -> str | None:
    """The WoW flavor a process with these arguments is running, or None if it isn't WoW."""
    found = wow_process(args)
    return found[0] if found else None


def _args(pid_dir: Path) -> list[str] | None:
    try:
        raw = (pid_dir / "cmdline").read_bytes()
    except OSError:
        return None
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def find_wow_process(proc: Path = Path("/proc")) -> tuple[str, str, int] | None:
    """(flavor, .exe name, process ID) of the WoW client running now, or None. The game itself
    wins over a launcher's process that names its .exe too (umu-run, Proton's start.exe): Wine
    makes the game's .exe its first argument."""
    first = None
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        args = _args(pid_dir)
        found = wow_process(args) if args else None
        if not found:
            continue
        if _WOW_EXE.search(args[0]):
            return (*found, int(pid_dir.name))
        first = first or (*found, int(pid_dir.name))
    return first


def wow_alive(pid: int, proc: Path = Path("/proc")) -> bool:
    """True if pid is still a running WoW process (not gone, a zombie, or reused)."""
    try:
        stat = (proc / str(pid) / "stat").read_text()
    except OSError:
        return False
    args = _args(proc / str(pid))
    return stat.rpartition(")")[2].split()[:1] != ["Z"] and bool(args and wow_process(args))


def find_wow() -> str | None:
    """The flavor of the WoW client running now ("Retail", "Classic", ...), or None."""
    found = find_wow_process()
    return found[0] if found else None


def x_display() -> dict[str, str]:
    """DISPLAY and XAUTHORITY as the desktop has them now. A service started at boot runs
    before the desktop hands them to systemd, so its own environment has none; and
    XAUTHORITY changes with each login."""
    found = {k: os.environ[k] for k in ("DISPLAY", "XAUTHORITY") if os.environ.get(k)}
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return found
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key in ("DISPLAY", "XAUTHORITY") and value:
            found[key] = value
    return found


def _xprop(display: dict[str, str], *args: str) -> str:
    return subprocess.run(["xprop", *args], capture_output=True, text=True, timeout=5,
                          check=True, env={**os.environ, **display}).stdout


def wow_window(pid: int) -> bool | None:
    """True once WoW's window is up: an X11 window of process pid, or one named "World of
    Warcraft" (a Flatpak's Wine has process IDs of its own). Wine draws through XWayland unless
    told otherwise. None if that can't be told: no X display, no xprop, Wine's Wayland driver."""
    display = x_display()
    if not display.get("DISPLAY") or not shutil.which("xprop"):
        return None
    try:
        listed = _xprop(display, "-root", "_NET_CLIENT_LIST")
        if "#" not in listed:
            return None
        for window in re.findall(r"0x[0-9a-fA-F]+", listed.partition("#")[2]):
            props = _xprop(display, "-id", window, "_NET_WM_PID", "_NET_WM_NAME")
            if re.search(rf"_NET_WM_PID\(CARDINAL\) = {pid}$", props, re.MULTILINE) or \
                    '= "World of Warcraft"' in props:
                return True
    except (OSError, subprocess.SubprocessError):
        return None
    return False
