"""Finds a running WoW client in /proc.

However WoW is installed (Lutris, Heroic, Bottles, Faugus, Steam/Proton, plain Wine, Flatpak
builds of those), it ends up as a Wine process whose command line names the game's .exe. The
host sees processes inside Flatpak sandboxes too, so one scan of /proc covers all of them.
"""

import re
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
        if exe in FLAVORS:
            return FLAVORS[exe], f"{m.group(1)}.exe"
        if any(h in exe for h in _HELPERS):
            continue
        # An unknown name counts only inside a game folder ("_forever_"), so other programs
        # named Wow-something (addon managers and the like) don't start a recording.
        folder = _GAME_FOLDER.search(arg[:m.end()].rstrip())
        if folder:
            return folder.group(1).replace("_", " ").title(), f"{m.group(1)}.exe"
    return None


def wow_flavor(args: list[str]) -> str | None:
    """The WoW flavor a process with these arguments is running, or None if it isn't WoW."""
    found = wow_process(args)
    return found[0] if found else None


def find_wow_process() -> tuple[str, str, int] | None:
    """(flavor, .exe name, process ID) of the WoW client running now, or None."""
    for pid_dir in Path("/proc").iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            raw = (pid_dir / "cmdline").read_bytes()
        except OSError:
            continue
        found = wow_process([a.decode(errors="replace") for a in raw.split(b"\0") if a])
        if found:
            return (*found, int(pid_dir.name))
    return None


def find_wow() -> str | None:
    """The flavor of the WoW client running now ("Retail", "Classic", ...), or None."""
    found = find_wow_process()
    return found[0] if found else None
