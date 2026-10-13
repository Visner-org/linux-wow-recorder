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
from .clips import (SessionResult, cleanup, free_gb, new_recording_path, process_pending,
                    stale_recordings)
from .config import WOW_SOUND, Config
from .detect import find_wow, find_wow_process
from .notify import Notifier
from .recorder import WOW_SOUND_NAME, Recorder

log = logging.getLogger(__name__)

# Webhook messages never carry log text, error output or file paths (docs/development.md,
# "Logging"); they point to the log on this computer instead.
SEE_LOG = "Details are in the log on this computer (tray menu: Open log folder)."

# How often a session tries a new recording after the recorder fails, before giving up.
MAX_RESTARTS = 3
DISK_CHECK_INTERVAL = 60
# After what was recorded is gone (WoW's window closed): a new recording starts when WoW is back
# as a new process, or after this long if it's the same one.
CAPTURE_RETRY = 30
# ... and says so if WoW is still there after this long. Closing WoW takes its window first, a
# few seconds before the process ends: that's no news.
CAPTURE_NOTICE = 15
# How long into a session the programs with sound are logged once: WoW opens its sound a little
# after it starts.
AUDIO_CHECK_AFTER = 90


@dataclass
class Session:
    flavor: str
    start: float = 0.0
    restarts: int = 0
    alerted: bool = False        # recording_failed already sent this session
    disk_warned: bool = False
    disk_stopped: bool = False   # stopped for low disk space; not restarted this session
    paused: bool = False         # the user paused recording for this session
    pid: int | None = None       # WoW's process
    lost_at: float = 0.0         # when what was recorded went away; 0 while it's fine
    captures_lost: int = 0
    audio_checked: bool = False
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
        self.notifier = Notifier(cfg, background=True)
        self.recorder = Recorder(cfg)
        self.session: Session | None = None
        self.status = state.Status()
        self._stop = threading.Event()
        # Clips are cut in a thread of their own, so WoW started again meanwhile is recorded.
        # The lock covers self.session and the cutter's fields, so the cutter never writes its
        # state over a new session's.
        self._lock = threading.Lock()
        self._cutter: threading.Thread | None = None
        self._cut_again = False   # a session ended while cutting: go round once more
        self._stale_warned: set[str] = set()   # un-cut recordings past retention, told once

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
            self.start_cutting()   # whatever an earlier run left behind

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
                    self.check_session(found[2])
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
        cutter = self._cutter
        if cutter is not None:
            # It stops after the recording it's on; the rest are cut next time.
            log.info("Waiting for the clip being cut...")
            cutter.join(timeout=10)   # within systemd's TimeoutStopSec, after recorder.stop()
        self.notifier.flush(timeout=3)   # all of it within systemd's TimeoutStopSec
        self.status.update(state=state.STOPPED)
        log.info("Exiting.")

    def _free(self) -> float | None:
        try:
            return round(free_gb(self.cfg), 1)
        except OSError:
            return None

    # ── Sessions ──

    def start_session(self, flavor: str, exe: str | None = None, pid: int | None = None):
        log.info(f"WoW {flavor} ({exe}) started; recording.")
        state.clear_requests()   # a pause was for the session before
        with self._lock:
            self.session = Session(flavor, start=time.time(), pid=pid)
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

    def check_session(self, pid: int | None = None):
        """pid: WoW's process now."""
        s = self.session
        if self.check_pause() or self.check_capture(pid):
            return
        now = time.time()
        if now - s.last_disk_check >= DISK_CHECK_INTERVAL:
            s.last_disk_check = now
            self.check_disk()
        if not s.audio_checked and now - s.start >= AUDIO_CHECK_AFTER:
            s.audio_checked = True
            self.check_audio()
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

    def check_capture(self, pid: int | None) -> bool:
        """Handles the recording losing what it records: WoW crashed and came back within a
        poll or two (or something kept the session going), and the recorder, given the old WoW
        window, records its last frame. The recording so far is kept; a new one starts once
        WoW is back. True while waiting for that."""
        s, cfg = self.session, self.cfg
        restarted = pid is not None and s.pid is not None and pid != s.pid
        if s.lost_at:
            waited = time.time() - s.lost_at
            if restarted or waited >= CAPTURE_RETRY:
                s.lost_at, s.pid = 0.0, pid if pid is not None else s.pid
                log.info("WoW is back; starting a new recording.")
                self.start_recording()
                if self.recorder.is_running():
                    self.status.update(problem=None)
                return False
            if waited >= CAPTURE_NOTICE and self.status.data["state"] != state.NOT_RECORDING:
                pick = " (pick it in the screen-share dialog)" if cfg.capture == "portal" else ""
                self.status.update(state=state.NOT_RECORDING, problem=(
                    f"WoW's window closed: recording again when it's back{pick}."))
            return True
        if pid is not None and s.pid is None:
            s.pid = pid
        if restarted:
            log.info(f"WoW restarted (process {s.pid} -> {pid}).")
            s.pid = pid
        # A window picked in the screen-share dialog can't follow a new WoW: its window is new.
        lost = (self.recorder.is_running() and self.recorder.capture_lost()) or \
            (restarted and cfg.capture == "portal" and self.recorder.is_running())
        if not lost or s.captures_lost >= MAX_RESTARTS:
            return False
        s.captures_lost += 1
        log.warning("What was being recorded is gone (WoW's window closed). The recording so "
                    "far is kept; a new one starts if WoW comes back.")
        self.recorder.stop()
        self.status.update(recording_file=None)
        if restarted:   # WoW's already back
            s.lost_at = 0.0
            self.start_recording()
            if self.recorder.is_running():
                self.status.update(problem=None)
            return False
        s.lost_at = time.time()
        return True

    def check_audio(self):
        """Logs which programs' sound can be recorded, and warns if WoW's sound is to be
        recorded but isn't among them under any of the names asked for."""
        if not any(WOW_SOUND in track for track in self.cfg.audio) or not self.recorder.is_running():
            return
        apps = self.recorder.audio_apps()
        if apps is None:
            return
        log.info(f"Programs with sound now: {', '.join(apps) or 'none'}")
        wanted = {n.lower() for n in (self.recorder.game_exe or "", WOW_SOUND_NAME) if n}
        if not wanted & {a.lower() for a in apps}:
            log.warning("WoW's sound isn't among them, so it isn't being recorded. Please "
                        "report the list above.")

    def check_pause(self) -> bool:
        """Pauses or resumes recording as the desktop app (or the CLI) asked. True while
        paused: the recorder is meant to be off, so it isn't restarted."""
        s = self.session
        want = state.pause_requested(s.start)
        if want and not s.paused:
            s.paused = True
            log.info("Recording paused for this session, as asked.")
            self.recorder.stop()   # what was recorded so far is kept and cut after the session
            self.status.update(state=state.PAUSED, recording_file=None)
        elif not want and s.paused:
            s.paused = False
            log.info("Recording again, as asked.")
            self.start_recording()
        return s.paused

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
        with self._lock:
            self.session = None
        state.clear_requests()
        if was_recording:
            hours, rest = divmod(int(length), 3600)
            self.notifier.send("session_ended", f"⏹️ WoW closed after {hours}h{rest // 60:02d}m; "
                                                "the recording is saved. Cutting clips...")
        self.status.update(state=state.WAITING, flavor=None, session_start=None,
                           recording_file=None, problem=None, free_gb=self._free())
        self.start_cutting()
        log.info("Watching for WoW...")

    # ── After a session ──

    def start_cutting(self):
        """Runs finish() in a thread of its own. If it's already running, it runs once more
        when done, for the recording that just ended."""
        with self._lock:
            if self._cutter is not None:
                self._cut_again = True
                return
            self._cutter = threading.Thread(target=self._cut_loop, name="cutter", daemon=True)
            self._cutter.start()

    def _cut_loop(self):
        while True:
            try:
                self.finish()
            except Exception:
                # Never fatal: systemd would restart the service into the same error forever.
                log.exception("Cutting clips failed")
            with self._lock:
                if self._cut_again and not self._stop.is_set():
                    self._cut_again = False
                    continue
                self._cutter = None
                if self.session is None and not self._stop.is_set():
                    self.status.update(state=state.WAITING)
                return

    def _set_idle_state(self, value: str):
        """Sets the state unless a session is running: its state (recording) wins."""
        with self._lock:
            if self.session is None:
                self.status.update(state=value)

    def finish(self):
        """Cuts the waiting recordings into clips, reports, runs the hook, cleans up. Runs in
        the cutter thread (start_cutting)."""
        self._set_idle_state(state.PROCESSING)
        # The recording of a session started meanwhile is still being written: not that one.
        results = process_pending(self.cfg, self._stop, lambda: self.recorder.file)
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
        self.warn_stale()

    def warn_stale(self):
        """Recordings whose clips still couldn't be cut, past their retention: kept, and the
        user told once per run of the service."""
        stale = [f for f in stale_recordings(self.cfg) if f.name not in self._stale_warned]
        if not stale:
            return
        self._stale_warned.update(f.name for f in stale)
        days = self.cfg.session_retention_days
        for f in stale:
            log.warning(f"{f.name} is older than {days} days but its clips couldn't be cut; "
                        "it's kept in sessions/ until it's cut or you delete it.")
        self.notifier.send("clip_failed", f"⚠️ **{len(stale)}** recording(s) older than {days} "
                                          "days still couldn't be cut into clips. They're kept "
                                          f"instead of deleted. {SEE_LOG}")

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
