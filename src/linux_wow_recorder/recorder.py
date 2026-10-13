"""Records the screen with gpu-screen-recorder.

The recorder runs as a separate program in its own process group, and SIGINT makes it finish
and save the file. Recordings are .mkv, which stays playable even if the recorder is killed.
"""

import contextlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path

from . import logs
from .config import STATE_DIR, WOW_SOUND, Config

log = logging.getLogger(__name__)

# gpu-screen-recorder's line when the window it records is gone (WoW crashed or closed): it then
# goes on writing that window's last frame.
_CAPTURE_LOST = re.compile(r'pipewire: stream \S+ previous state: "[^"]*", new state: "(error|unconnected)"')
# The recorder of the current session, so a run that crashed can find and stop it.
STATE_FILE = STATE_DIR / "recorder.json"
# WoW's sound when the running .exe isn't known (the 5-second test): every known name.
WOW_EXES = ("Wow.exe", "WowT.exe", "WowB.exe", "WowClassic.exe", "WowClassicT.exe",
            "WowClassicB.exe")
# What the game calls its sound in PipeWire (application.name): --list-application-audio shows
# retail as this, not its .exe, so asking for the .exe's name alone recorded silence. The .exe
# stays in the list for clients that might use it.
WOW_SOUND_NAME = "World of Warcraft"


def audio_source(track: str, game_exe: str | None) -> str:
    """One audio track for gpu-screen-recorder's -a, with "wow" turned into the names WoW's
    sound can have: app:<exe>|app:World of Warcraft (app: matches any case)."""
    parts = []
    for part in track.split("|"):
        if part.strip() == WOW_SOUND:
            parts += [f"app:{exe}" for exe in ((game_exe,) if game_exe else WOW_EXES)]
            parts.append(f"app:{WOW_SOUND_NAME}")
        else:
            parts.append(part)
    return "|".join(parts)


def _alive(pid: int) -> bool:
    """True if pid is a running (not zombie) gpu-screen-recorder."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    state = stat.rpartition(")")[2].split()[0]
    return state != "Z" and b"gpu-screen-recorder" in cmdline


def _signal_group(pid: int, sig: int):
    # The whole group, so a wrapper like `flatpak run` passes it on to the recorder.
    try:
        os.killpg(pid, sig)
    except PermissionError:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


class Recorder:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.pid: int | None = None
        self.file: Path | None = None
        self.game_exe: str | None = None   # WoW's .exe, for recording only its sound
        self.output_log: Path | None = None  # gpu-screen-recorder's output, logs/sessions/
        self._log_read = 0   # how much of output_log capture_lost() has read

    def command(self, output: Path) -> list[str]:
        cfg = self.cfg
        cmd = [*cfg.recorder_command, "-w", cfg.capture, "-f", str(cfg.fps)]
        if cfg.quality:
            cmd += ["-q", cfg.quality]
        for track in cfg.audio:
            cmd += ["-a", audio_source(track, self.game_exe)]
        if cfg.capture == "portal":
            # Remembers the screen-share permission, so it's asked only the first time.
            cmd += ["-restore-portal-session", "yes"]
        return [*cmd, *cfg.extra_args, "-o", str(output)]

    def start(self, output: Path) -> bool:
        """Starts recording to output. True if the recorder is still running 3 s later."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.command(output)
        log.info(f"Starting the recorder: {shlex.join(cmd)}")
        self.output_log = logs.new_session_log()
        self._log_read = 0
        # Set before the file exists, so clips are never cut from it while it's written.
        self.file = output
        with open(self.output_log, "ab") as out:
            out.write(f"$ {shlex.join(cmd)}\n".encode())
            out.flush()
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                             stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:
                log.error(f"Couldn't start the recorder: {e}")
                out.write(f"{e}\n".encode())
                self.file = None
                return False
        self.pid = self.proc.pid
        STATE_FILE.write_text(json.dumps({"pid": self.pid, "file": str(output)}))

        time.sleep(3)
        if not self.is_running():
            log.error(f"The recorder exited right away:\n{self.log_tail()}")
            self.stop()
            return False
        log.info(f"Recording to {output}")
        return True

    def is_running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return self.pid is not None and _alive(self.pid)

    def stop(self, timeout: int = 30):
        """Stops the recorder and waits for it to save the file. Safe to call when it isn't
        running."""
        if self.pid is not None and self.is_running():
            log.info(f"Stopping the recorder (saving {self.file.name if self.file else 'the file'})...")
            _signal_group(self.pid, signal.SIGINT)
            deadline = time.time() + timeout
            while self.is_running() and time.time() < deadline:
                time.sleep(0.5)
            if self.is_running():
                log.warning(f"The recorder didn't stop within {timeout}s; killing it.")
                _signal_group(self.pid, signal.SIGKILL)
        if self.proc is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
        self.proc = self.pid = self.file = None
        STATE_FILE.unlink(missing_ok=True)

    def adopt_leftover(self) -> bool:
        """True if a recorder started by an earlier run (that crashed) is still running; it
        becomes this one's, so stop() ends it."""
        try:
            state = json.loads(STATE_FILE.read_text())
        except (FileNotFoundError, ValueError):
            return False
        if not _alive(state.get("pid", 0)):
            STATE_FILE.unlink(missing_ok=True)
            return False
        self.pid, self.file = state["pid"], Path(state["file"])
        return True

    def capture_lost(self) -> bool:
        """True if the recorder has said, since the last call, that what it records is gone
        (the WoW window it was given closed). It keeps running, writing the last frame."""
        if self.output_log is None:
            return False
        try:
            with open(self.output_log, "rb") as f:
                f.seek(self._log_read)
                data = f.read()
        except OSError:
            return False
        done = data.rfind(b"\n") + 1   # whole lines only; the rest is read next time
        self._log_read += done
        return bool(_CAPTURE_LOST.search(data[:done].decode(errors="replace")))

    def audio_apps(self) -> list[str] | None:
        """The programs whose sound the recorder can record now (--list-application-audio), or
        None if it couldn't say. For the log: which name WoW's sound has."""
        try:
            result = subprocess.run([*self.cfg.recorder_command, "--list-application-audio"],
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return [l.strip() for l in result.stdout.splitlines() if l.strip()]

    def log_tail(self, lines: int = 8) -> str:
        """The last lines gpu-screen-recorder printed this time. For the local log and the
        terminal only: never put this in a webhook message."""
        if self.output_log is None:
            return ""
        text = logs.tail(self.output_log, lines + 1)
        out = [l for l in text.splitlines() if not l.startswith("$ ")]
        return "\n".join(out[-lines:]) or "(no output)"
