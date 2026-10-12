"""The main loop: watches for WoW, records each session, then cuts the clips."""

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import APP_TITLE, __version__, logs, state
from .clips import SessionResult, cleanup, free_gb, new_recording_path, process_pending
from .config import Config
from .detect import find_wow, find_wow_process
from .notify import Notifier
from .recorder import Recorder

log = logging.getLogger(__name__)

# Webhook messages never carry log text, error output or file paths (docs/development.md,
# "Logging"); they point to the log on this computer instead.
SEE_LOG = "Details are in the log on this computer (tray menu: Open log folder)."

# How often a session tries a new recording after the recorder fails, before giving up.
MAX_RESTARTS = 3
DISK_CHECK_INTERVAL = 60


@dataclass
class Session:
    flavor: str
    start: float = 0.0
    restarts: int = 0
    alerted: bool = False        # recording_failed already sent this session
    disk_warned: bool = False
    disk_stopped: bool = False   # stopped for low disk space; not restarted this session
    last_disk_check: float = 0.0


def run_summary(run) -> str:
    """" · 3/3 bosses · 2 deaths · 2 lusts", for the Discord message."""
    parts = []
    if run.encounters:
        bosses = {e.name for e in run.encounters}
        killed = {e.name for e in run.encounters if e.killed}
        parts.append(f"{len(killed)}/{len(bosses)} bosses")
    if run.deaths or run.encounters:
        parts.append(f"{len(run.deaths)} death{'s' if len(run.deaths) != 1 else ''}")
    if run.lusts:
        parts.append(f"{len(run.lusts)} lust{'s' if len(run.lusts) != 1 else ''}")
    return "".join(f" · {p}" for p in parts)


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.notifier = Notifier(cfg)
        self.recorder = Recorder(cfg)
        self.session: Session | None = None
        self.status = state.Status()
        self._stop = threading.Event()

    def _on_signal(self, signum, frame):
        log.info(f"Signal {signum} received; stopping.")
        self._stop.set()

    def run(self):
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        cfg = self.cfg
        log.info(f"{APP_TITLE} {__version__} on {logs.system_summary()}")
        log.info(f"Combat logs : {cfg.combat_log_dir}")
        log.info(f"Output      : {cfg.output_dir}")
        log.info(f"Recorder    : {shlex.join(cfg.recorder_command)} -w {cfg.capture}")

        self.status.update(state=state.WAITING, free_gb=self._free())
        if self.recorder.adopt_leftover():
            log.warning("A recorder left running by an earlier run was found; stopping it.")
            self.recorder.stop()
        if not find_wow():
            try:
                self.finish()   # whatever an earlier run left behind
            except Exception:
                # Never fatal: systemd would restart the service into the same error forever.
                log.exception("Processing what an earlier run left failed")
            self.status.update(state=state.WAITING)

        log.info("Watching for WoW...")
        misses = 0
        while not self._stop.is_set():
            try:
                found = find_wow_process()
                flavor = found[0] if found else None
                if self.session is None:
                    if found:
                        self.start_session(*found)
                        misses = 0
                elif flavor:
                    misses = 0
                    self.check_session()
                else:
                    # Two misses in a row, so one odd read of /proc can't end a session.
                    misses += 1
                    if misses >= 2:
                        self.end_session()
            except Exception:
                log.exception("Unexpected error")
            self._stop.wait(cfg.poll_interval)

        if self.session:
            log.info("Stopped mid-session: the recording so far is saved and gets cut after "
                     "the next session.")
            self.recorder.stop()
        self.status.update(state=state.STOPPED)
        log.info("Exiting.")

    def _free(self) -> float | None:
        try:
            return round(free_gb(self.cfg), 1)
        except OSError:
            return None

    # ── Sessions ──

    def start_session(self, flavor: str, exe: str | None = None):
        log.info(f"WoW {flavor} ({exe}) started; recording.")
        self.session = Session(flavor, start=time.time())
        self.recorder.game_exe = exe
        self.status.update(flavor=flavor, session_start=self.session.start, problem=None)
        self.start_recording()
        if self.recorder.is_running():
            self.notifier.send("session_started", f"🔴 WoW {flavor} started; recording.")

    def start_recording(self):
        cfg, s = self.cfg, self.session
        free = free_gb(cfg)
        if free < cfg.stop_below_gb:
            s.disk_stopped = True
            self.status.update(state=state.NOT_RECORDING, recording_file=None, free_gb=round(free, 1),
                               problem=f"Only {free:.1f} GB free; not recording.")
            log.error(f"Only {free:.1f} GB free in {cfg.output_dir}; not recording.")
            self.notifier.send("low_disk_space",
                               f"🛑 Only **{free:.1f} GB** free in the output folder: "
                               "this session isn't being recorded.")
            return
        if self.recorder.start(new_recording_path(cfg)):
            self.status.update(state=state.RECORDING, recording_file=str(self.recorder.file),
                               free_gb=round(free, 1))
            return
        self.status.update(state=state.NOT_RECORDING, recording_file=None,
                           problem="The recorder didn't start.")
        if not s.alerted:
            s.alerted = True
            self.notifier.send("recording_failed", f"⚠️ **The recorder didn't start.** {SEE_LOG}")

    def check_session(self):
        s = self.session
        now = time.time()
        if now - s.last_disk_check >= DISK_CHECK_INTERVAL:
            s.last_disk_check = now
            self.check_disk()
        if s.disk_stopped or self.recorder.is_running() or s.restarts >= MAX_RESTARTS:
            return

        s.restarts += 1
        tail = self.recorder.log_tail()
        log.warning(f"The recorder isn't running; new recording (try {s.restarts} of "
                    f"{MAX_RESTARTS}). Its last output:\n{tail}")
        self.recorder.stop()   # forgets the dead one
        if not s.alerted:
            s.alerted = True
            self.notifier.send("recording_failed", "⚠️ **The recorder stopped mid-session**; "
                               f"starting a new recording. {SEE_LOG}")
        self.start_recording()
        if s.restarts >= MAX_RESTARTS and not self.recorder.is_running():
            log.error("The recorder keeps failing; giving up until WoW is started again.")
            self.status.update(problem="The recorder keeps failing; trying again next session.")

    def check_disk(self):
        cfg, s = self.cfg, self.session
        free = free_gb(cfg)
        self.status.update(free_gb=round(free, 1))
        if free < cfg.stop_below_gb and self.recorder.is_running():
            log.error(f"Only {free:.1f} GB free; stopping the recording.")
            self.recorder.stop()
            s.disk_stopped = True
            self.status.update(state=state.NOT_RECORDING, recording_file=None,
                               problem=f"Only {free:.1f} GB free; recording stopped.")
            self.notifier.send("low_disk_space",
                               f"🛑 Only **{free:.1f} GB** left in the output folder: "
                               "recording stopped. What was recorded so far is kept.")
        elif free < cfg.min_free_gb and not s.disk_warned:
            s.disk_warned = True
            log.warning(f"Low disk space: {free:.1f} GB free.")
            self.notifier.send("low_disk_space",
                               f"⚠️ Low disk space: **{free:.1f} GB** free in the output folder.")

    def end_session(self):
        log.info("WoW closed; finishing the session.")
        was_recording = self.recorder.is_running()
        self.recorder.stop()
        length = time.time() - self.session.start
        self.session = None
        if was_recording:
            hours, rest = divmod(int(length), 3600)
            self.notifier.send("session_ended", f"⏹️ WoW closed after {hours}h{rest // 60:02d}m; "
                                                "the recording is saved. Cutting clips...")
        try:
            self.finish()
        finally:
            self.status.update(state=state.WAITING, flavor=None, session_start=None,
                               recording_file=None, problem=None, free_gb=self._free())
        log.info("Watching for WoW...")

    # ── After a session ──

    def finish(self):
        """Cuts the waiting recordings into clips, reports, runs the hook, cleans up."""
        self.status.update(state=state.PROCESSING)
        results = process_pending(self.cfg)
        if results:
            self.status.update(last_session={
                "end": time.time(),
                "clips": [str(clip) for r in results for _, clip in r.clips],
                "problems": [r.detail for r in results if r.problem],
            })
        self.report(results)
        clips = [clip for r in results for _, clip in r.clips]
        if self.cfg.after_clips and clips:
            # In the background: an upload can take hours, and WoW may be started again
            # meanwhile. Retention can't touch the new clips, so cleanup can go ahead.
            threading.Thread(target=self.run_hook, args=(clips,), daemon=True).start()
        deleted = cleanup(self.cfg)
        if deleted:
            self.notifier.send("cleanup", f"🧹 Deleted **{deleted}** old file(s) from the "
                                          "output folder.")

    def report(self, results: list[SessionResult]):
        clips = [(run, clip) for r in results for run, clip in r.clips]
        if clips:
            log.info(f"{len(clips)} clip(s) saved in {self.cfg.clips_dir}")
            lines = [f"✅ **{len(clips)}** clip(s) saved:"]
            lines += [f"• {run.name} {run.label} ({run.length}){run_summary(run)}"
                      for run, _ in clips]
            self.notifier.send("clips_ready", "\n".join(lines))
        for r in results:
            if r.problem:
                log.warning(r.detail)
                self.notifier.send(r.problem, f"⚠️ {r.detail}")
        if results and not clips and not any(r.problem for r in results):
            log.info("No dungeon runs this session.")
            self.notifier.send("no_runs", "Session recorded; no dungeon runs in it.")

    def run_hook(self, clips: list[Path]):
        """Runs the after_clips command on clips. Called in a thread of its own."""
        try:
            cmd = shlex.split(self.cfg.after_clips)
            cmd[0] = os.path.expanduser(cmd[0])
            log.info(f"Running the after_clips hook: {cmd[0]} ({len(clips)} clip(s))")
            result = subprocess.run([*cmd, *map(str, clips)], capture_output=True, text=True,
                                    timeout=4 * 3600)
            output = (result.stdout + result.stderr).strip()
            failed = result.returncode != 0
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as e:
            output, failed = str(e), True
        if output:
            log.info(f"Hook output:\n{output}")
        if failed:
            log.error("The after_clips hook failed.")
            self.notifier.send("hook_failed", f"❌ **The after_clips command failed.** {SEE_LOG}")
