"""The companion WoW addon (addon/LinuxWoWRecorder): it turns combat logging on in dungeons and
raids, so a key's start is in the log even when other addons start logging late. Installed
into the WoW folder the combat log folder is in, by the user (Settings or `install-addon`);
the service keeps an installed copy up to date."""

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
    """Copies the addon into WoW's AddOns folder. Returns what went wrong, or None."""
    target = addons_dir(log_dir)
    if target is None:
        return "The combat log folder isn't in a WoW folder (…/_retail_/Logs)."
    try:
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE, target / NAME, dirs_exist_ok=True)
    except OSError as e:
        return f"Couldn't copy the addon into {target}: {e}"
    log.info(f"WoW addon installed in {target / NAME}.")
    return None


def refresh(log_dir: Path | None):
    """Updates an installed addon this version of the app has a newer copy of."""
    if status(log_dir) == OLD:
        why = install(log_dir)
        if why:
            log.warning(why)


def remove(log_dir: Path | None) -> Path | None:
    """Removes the addon (uninstall). Returns its folder if it was there."""
    target = addons_dir(log_dir)
    if target is None or not (target / NAME / f"{NAME}.toc").exists():
        return None
    shutil.rmtree(target / NAME)
    return target / NAME
