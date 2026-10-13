"""A clean uninstall: everything the app made outside its package, and the package itself where
that doesn't need root.

Removed: the systemd user service, the autostart and menu entries the app wrote, the config
folder (settings, the clip browser's choices), the state folder (logs) and the runtime files.
Recordings and clips only when asked, and then only the app's own three subfolders of the output
folder. Clips kept elsewhere ("Also show clips from") are never touched.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, service, state
from .config import CONFIG_DIR, STATE_DIR, Config


@dataclass
class Program:
    """How the program itself was installed, and how it's removed."""
    kind: str                     # "uv", "pipx", "rpm", "pacman" or "other"
    command: list[str]            # the removal command
    needs_root: bool              # system packages: the user runs it (sudo), the app can't

    @property
    def text(self) -> str:
        return " ".join(self.command)


def _owned_by(tool: list[str], path: Path) -> bool:
    if not shutil.which(tool[0]):
        return False
    try:
        return subprocess.run([*tool, str(path)], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def program(package_file: Path | None = None) -> Program:
    """How this copy of the app was installed, from where its files are."""
    path = (package_file or Path(__file__)).resolve()
    # uv and pipx each leave a file of their own in the environment they made (pyvenv.cfg's
    # folder), wherever they keep it.
    venv = next((f for f in path.parents if (f / "pyvenv.cfg").exists()), None)
    if venv and (venv / "uv-receipt.toml").exists():
        return Program("uv", ["uv", "tool", "uninstall", APP_NAME], False)
    if venv and (venv / "pipx_metadata.json").exists():
        return Program("pipx", ["pipx", "uninstall", APP_NAME], False)
    if str(path).startswith("/usr/"):
        if _owned_by(["rpm", "-qf"], path):
            return Program("rpm", ["sudo", "dnf", "remove", APP_NAME], True)
        if _owned_by(["pacman", "-Qo"], path):
            return Program("pacman", ["sudo", "pacman", "-R", APP_NAME], True)
    return Program("other", [sys.executable, "-m", "pip", "uninstall", APP_NAME], False)


def recording_folders(cfg: Config | None) -> list[Path]:
    """The app's own folders in the output folder: what deleting recordings removes."""
    if cfg is None or cfg.output_dir is None:
        return []
    return [f for f in (cfg.sessions_dir, cfg.processed_dir, cfg.clips_dir) if f.is_dir()]


def size_gb(folders: list[Path]) -> float:
    total = 0
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
    return total / 1e9


def _rmtree(path: Path, done: list[str]):
    # Only the app's own folders, by name: a wrong XDG variable must never point this at $HOME.
    if path.name != APP_NAME:
        raise ValueError(f"not removing {path}: not an {APP_NAME} folder")
    if path.exists():
        shutil.rmtree(path)
        done.append(str(path))


def remove_user_files(cfg: Config | None, delete_recordings: bool) -> list[str]:
    """Removes everything the app made for this user. Returns what was removed."""
    from .gui import desktop   # plain file paths; no Qt needed
    done = []
    if service.UNIT_FILE.exists():
        service.uninstall()   # stops it first: a recording in progress is saved
        done.append(str(service.UNIT_FILE))
    for f in (desktop.AUTOSTART_FILE, desktop.MENU_FILE, desktop.ICON_FILE):
        if f.exists():
            f.unlink()
            done.append(str(f))
    if delete_recordings:
        for folder in recording_folders(cfg):
            shutil.rmtree(folder)
            done.append(str(folder))
        try:
            cfg.output_dir.rmdir()   # only if nothing else of the user's is in it
            done.append(str(cfg.output_dir))
        except (OSError, AttributeError):
            pass
    for folder in (CONFIG_DIR, STATE_DIR, state.STATUS_FILE.parent):   # the last: runtime files
        _rmtree(folder, done)
    return done


def remove_program(prog: Program) -> tuple[bool, str]:
    """Runs the removal for a user-level install (uv, pipx). (worked, output)."""
    if prog.needs_root or prog.kind == "other":
        return False, f"Remove the program with: {prog.text}"
    try:
        result = subprocess.run(prog.command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{prog.text} failed: {e}"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or prog.text
