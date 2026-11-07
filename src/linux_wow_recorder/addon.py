"""The companion WoW addon (addon/LinuxWoWRecorder): it turns combat logging on in dungeons and
raids, so a key's start is in the log even when other addons start logging late, and notes
level-ups and elite NPCs for moment clips (read_saved). Installed by the user (Settings or
`install-addon`) into the WoW folder the combat log folder is in, and into the other clients
beside it (_forever_ next to _retail_); the service keeps installed copies up to date."""

import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

NAME = "LinuxWoWRecorder"
SOURCE = Path(__file__).parent / "addon" / NAME

INSTALLED, OLD, MISSING, NO_WOW = "installed", "old", "missing", "no wow"


def addons_dir(log_dir: Path | None) -> Path | None:
    """<WoW>/_retail_/Interface/AddOns, from the combat log folder (<WoW>/_retail_/Logs); None
    if that isn't in a WoW folder."""
    if log_dir is None:
        return None
    game = log_dir.parent
    # WTF appears at the first login, Interface with the first addon: either says it's WoW's.
    if not ((game / "WTF").is_dir() or (game / "Interface").is_dir()):
        return None
    return game / "Interface" / "AddOns"


def all_addons_dirs(log_dir: Path | None) -> list[Path]:
    """addons_dir, then the same for the other WoW clients beside it (<WoW>/_forever_ next to
    <WoW>/_retail_)."""
    first = addons_dir(log_dir)
    if first is None:
        return []
    found = [first]
    game = log_dir.parent
    if re.fullmatch(r"_\w+_", game.name):
        for other in sorted(game.parent.glob("_*_")):
            if other != game and ((other / "WTF").is_dir() or (other / "Interface").is_dir()):
                found.append(other / "Interface" / "AddOns")
    return found


def version(folder: Path) -> str | None:
    """The addon's version, from its .toc."""
    try:
        text = (folder / f"{NAME}.toc").read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r"^## Version:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def status(log_dir: Path | None) -> str:
    target = addons_dir(log_dir)
    if target is None:
        return NO_WOW
    installed = version(target / NAME)
    if installed is None:
        return MISSING
    return INSTALLED if installed == version(SOURCE) else OLD


def install(log_dir: Path | None) -> str | None:
    """Copies the addon into WoW's AddOns folder, and the other clients'. Returns what went
    wrong, or None."""
    targets = all_addons_dirs(log_dir)
    if not targets:
        return "The combat log folder isn't in a WoW folder (…/_retail_/Logs)."
    for target in targets:
        if why := _copy(target):
            return why
    return None


def _copy(target: Path) -> str | None:
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE, target / NAME, dirs_exist_ok=True)
    except OSError as e:
        return f"Couldn't copy the addon into {target}: {e}"
    log.info(f"WoW addon installed in {target / NAME}.")
    return None


def refresh(log_dir: Path | None):
    """Updates the installed copies this version of the app has a newer one of. Once it's in
    the combat log folder's WoW (the user chose it), the other clients get it too."""
    targets = all_addons_dirs(log_dir)
    if not targets or version(targets[0] / NAME) is None:
        return
    for target in targets:
        if version(target / NAME) != version(SOURCE):
            why = _copy(target)
            if why:
                log.warning(why)


def remove(log_dir: Path | None) -> Path | None:
    """Removes the addon from every client (uninstall). Returns the first folder it was in."""
    first = None
    for target in all_addons_dirs(log_dir):
        if (target / NAME / f"{NAME}.toc").exists():
            shutil.rmtree(target / NAME)
            first = first or target / NAME
    return first


# In WoW's saved variables file (WTF/Account/<account>/SavedVariables/LinuxWoWRecorder.lua):
# levels as "time:level" strings, elites as [npcID] = "classification".
_LEVEL = re.compile(r'"(\d+):(\d+)"')
_ELITE = re.compile(r'\[(\d+)\]\s*=\s*"(\w+)"')


def read_saved(log_dir: Path | None) -> tuple[list[tuple[float, int]], dict[int, str]]:
    """What the addon noted, from every client and account: level-ups as (time, level), and
    NPC ID -> classification ("elite", "rare", ...) of the elites seen. WoW writes the file at
    logout or /reload."""
    levels: set[tuple[float, int]] = set()
    elites: dict[int, str] = {}
    for target in all_addons_dirs(log_dir):
        game = target.parent.parent
        for f in game.glob(f"WTF/Account/*/SavedVariables/{NAME}.lua"):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            head, _, tail = text.partition('["elites"]')
            if '["levels"]' in tail:   # either order
                tail, _, more = tail.partition('["levels"]')
                head += more
            levels.update((float(t), int(lvl)) for t, lvl in _LEVEL.findall(head))
            elites.update((int(n), kind) for n, kind in _ELITE.findall(tail))
    return sorted(levels), elites
