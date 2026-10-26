import os
import threading
import types
from pathlib import Path

import pytest

from linux_wow_recorder import clips, config, daemon, state


class FakeRecorder:
    """Records nothing: a file name and a running flag the test can flip."""

    def __init__(self, cfg, works=True):
        self.cfg = cfg
        self.works = works
        self.running = False
        self.file: Path | None = None
        self.game_exe = None
        self.starts = 0
        self.lost = False   # what capture_lost() says next
        self.capture = None
        self.picking = False   # the screen-share dialog waits after each start
        self.waiting = False

    @property
    def capture_mode(self):
        return self.capture or self.cfg.capture

    def start(self, output):
        self.starts += 1
        if not self.works:
            return False
        self.file, self.running = output, True
        self.waiting = self.picking and self.capture_mode == "portal"
        return True

    def waiting_for_pick(self):
        return self.running and self.waiting

    def forget_pick(self):
        self.forgotten += 1

    forgotten = 0

    def is_running(self):
        return self.running

    def stop(self, timeout=30):
        self.running, self.file, self.waiting = False, None, False

    def adopt_leftover(self):
        return False

    def capture_lost(self):
        lost, self.lost = self.lost, False
        return lost

    def log_tail(self, lines=8):
        return "(no output)"

    def audio_apps(self):
        return self.apps

    apps = ["Firefox", "Wow.exe"]


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, event, message, force=False):
        self.sent.append(event)
        return []

    def flush(self, timeout):
        pass


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def time(self):
        return self.now


@pytest.fixture
def make(tmp_path, monkeypatch):
    """A Daemon with fakes around it, plus the fake clock and free space (in GB)."""
    def make(free=100.0, works=True, stale=()):
        data = config.load_raw(tmp_path / "missing.toml")
        data["paths"] = {"combat_log_dir": str(tmp_path / "logs"),
                         "output_dir": str(tmp_path / "out")}
        cfg = config.from_dict(data)
        clock = Clock()
        disk = types.SimpleNamespace(free=free)
        monkeypatch.setattr(daemon, "time", clock)
        monkeypatch.setattr(daemon, "free_gb", lambda cfg: disk.free)
        monkeypatch.setattr(daemon, "cleanup", lambda cfg: 0)
        monkeypatch.setattr(daemon, "stale_recordings", lambda cfg: stale)
        monkeypatch.setattr(daemon, "wow_alive", lambda pid: False)
        d = daemon.Daemon(cfg)
        d.recorder = FakeRecorder(cfg, works)
        d.notifier = FakeNotifier()
        d.status = state.Status(tmp_path / "status.json")
        monkeypatch.setattr(state, "STATUS_FILE", tmp_path / "status.json")
        monkeypatch.setattr(state, "CONTROL_FILE", tmp_path / "control.json")
        return d, clock, disk
    return make


def blocking_process_pending(monkeypatch):
    """Replaces process_pending with one that waits for release.set(); returns
    (entered, release, calls)."""
    entered, release, calls = threading.Event(), threading.Event(), []

    def fake(cfg, stop=None, in_use=lambda: None):
        calls.append(in_use())
        entered.set()
        assert release.wait(5)
        return []
    monkeypatch.setattr(daemon, "process_pending", fake)
    return entered, release, calls


def wait_for_cutter(d):
    cutter = d._cutter
    if cutter is not None:
        cutter.join(5)
        assert not cutter.is_alive()


def test_a_session_starts_while_clips_are_cut(make, monkeypatch):
    d, clock, _ = make()
    entered, release, calls = blocking_process_pending(monkeypatch)

    d.start_session("Retail", "Wow.exe")
    clock.now += 3600
    d.end_session()              # returns right away; the cutting goes on in the cutter thread
    assert entered.wait(5)
    assert d.status.data["state"] == state.PROCESSING

    d.start_session("Retail", "Wow.exe")   # WoW started again while cutting
    assert d.status.data["state"] == state.RECORDING
    assert d.recorder.running and d.recorder.file is not None

    release.set()
    wait_for_cutter(d)
    # The cutter's "waiting" doesn't overwrite the new session's state.
    assert d.status.data["state"] == state.RECORDING
    assert calls == [None]       # nothing was being recorded when the cut started
    assert d.notifier.sent == ["session_started", "session_ended", "session_started"]


def test_a_session_ending_while_cutting_gets_another_round(make, monkeypatch):
    d, clock, _ = make()
    entered, release, calls = blocking_process_pending(monkeypatch)

    d.start_session("Retail")
    d.end_session()
    assert entered.wait(5)
    d.start_session("Retail")
    d.end_session()              # the cutter is busy: it goes round once more
    assert len(calls) == 1
    release.set()
    wait_for_cutter(d)
    assert len(calls) == 2
    assert d._cutter is None and d.status.data["state"] == state.WAITING


def test_the_recording_being_written_is_never_cut(make, monkeypatch):
    d, _, _ = make()
    entered, release, calls = blocking_process_pending(monkeypatch)
    d.start_session("Retail")
    d.start_cutting()            # as on start, with a recording going
    assert entered.wait(5)
    release.set()
    wait_for_cutter(d)
    assert calls == [d.recorder.file]


def test_a_crash_while_cutting_is_not_fatal(make, monkeypatch):
    d, _, _ = make()

    def broken(cfg, stop=None, in_use=lambda: None):
        raise RuntimeError("ffprobe exploded")
    monkeypatch.setattr(daemon, "process_pending", broken)
    d.start_cutting()
    wait_for_cutter(d)
    assert d._cutter is None and d.status.data["state"] == state.WAITING


def test_the_recorder_is_restarted_at_most_max_restarts_times(make):
    d, clock, _ = make()
    d.start_session("Retail")
    assert d.recorder.starts == 1
    d.recorder.works = False
    d.recorder.running = False          # it died
    for _ in range(daemon.MAX_RESTARTS + 2):
        clock.now += 5
        d.check_session()
    assert d.recorder.starts == 1 + daemon.MAX_RESTARTS
    assert d.notifier.sent.count("recording_failed") == 1   # once per session
    assert "keeps failing" in d.status.data["problem"]


def test_low_disk_space_warns_once_then_stops(make):
    d, clock, disk = make(free=100.0)
    d.start_session("Retail")
    d.check_session()                   # the first check is right away
    disk.free = d.cfg.min_free_gb - 1
    clock.now += 10
    d.check_session()                   # less than DISK_CHECK_INTERVAL later: not checked
    assert "low_disk_space" not in d.notifier.sent
    for _ in range(3):
        clock.now += daemon.DISK_CHECK_INTERVAL
        d.check_session()
    assert d.notifier.sent.count("low_disk_space") == 1 and d.recorder.running

    disk.free = d.cfg.stop_below_gb - 0.5
    clock.now += daemon.DISK_CHECK_INTERVAL
    d.check_session()
    assert not d.recorder.running and d.session.disk_stopped
    assert d.status.data["state"] == state.NOT_RECORDING
    clock.now += daemon.DISK_CHECK_INTERVAL
    d.check_session()                   # not restarted after a stop for disk space
    assert d.recorder.starts == 1


def test_a_missing_output_folder_is_said_and_recording_starts_when_its_back(make, monkeypatch):
    d, clock, disk = make()
    gone = types.SimpleNamespace(on=True)

    def free(cfg):
        if gone.on:
            raise FileNotFoundError(cfg.output_dir)
        return disk.free
    monkeypatch.setattr(daemon, "free_gb", free)
    d.start_session("Retail", "Wow.exe", 1)
    assert d.recorder.starts == 0
    assert d.status.data["state"] == state.NOT_RECORDING
    assert d.status.data["problem"] == daemon.OUTPUT_PROBLEM
    for _ in range(daemon.MAX_RESTARTS + 2):   # waits, however long it takes
        clock.now += daemon.DISK_CHECK_INTERVAL
        d.check_session(1)
    assert d.recorder.starts == 0 and d.notifier.sent.count("recording_failed") == 1

    gone.on = False
    d.cfg.output_dir.mkdir(parents=True)
    clock.now += 5
    d.check_session(1)
    assert d.recorder.running and d.status.data["state"] == state.RECORDING
    assert d.status.data["problem"] is None


def test_the_output_folder_going_away_mid_session_is_said(make, monkeypatch):
    d, clock, _ = make()
    d.start_session("Retail", "Wow.exe", 1)
    def gone(cfg):
        raise FileNotFoundError(cfg.output_dir)
    monkeypatch.setattr(daemon, "free_gb", gone)
    clock.now += daemon.DISK_CHECK_INTERVAL
    d.check_session(1)                  # the disk check doesn't fail on it
    d.recorder.running = False          # the recorder can't write: it dies
    clock.now += 5
    d.check_session(1)
    assert d.status.data["problem"] == daemon.OUTPUT_PROBLEM
    assert d.notifier.sent.count("recording_failed") == 1


def test_run_records_a_session_and_cuts_it(make, monkeypatch):
    d, clock, _ = make()
    d.cfg.poll_interval = 0
    sightings = iter([None, ("Retail", "Wow.exe", 7), ("Retail", "Wow.exe", 7), None, None, None])
    cut = threading.Event()

    def find():
        try:
            clock.now += 5
            return next(sightings)
        except StopIteration:
            assert cut.wait(5)
            d._stop.set()
            return None

    def fake_pending(cfg, stop=None, in_use=lambda: None):
        if d.session is None and d.notifier.sent[-1:] == ["session_ended"]:
            cut.set()
        return []
    monkeypatch.setattr(daemon, "find_wow", lambda: None)
    monkeypatch.setattr(daemon, "find_wow_process", find)
    monkeypatch.setattr(daemon, "process_pending", fake_pending)
    d.run()
    assert d.notifier.sent == ["session_started", "session_ended"]
    assert d.status.data["state"] == state.STOPPED


def test_un_cut_recordings_past_retention_are_reported_once(make, monkeypatch):
    stale = [Path("/out/sessions/2026-01-01_20-00-00.mkv")]
    d, _, _ = make(stale=stale)
    monkeypatch.setattr(daemon, "process_pending", lambda cfg, stop=None, in_use=None: [])
    d.finish()
    d.finish()
    assert d.notifier.sent == ["clip_failed"]


def test_pause_and_resume_this_session(make, monkeypatch):
    d, clock, _ = make()
    d.start_session("Retail", "Wow.exe")
    clock.now += 60
    assert state.request_pause(True) is None
    d.check_session()
    assert not d.recorder.running and d.status.data["state"] == state.PAUSED
    for _ in range(daemon.MAX_RESTARTS + 1):   # paused: not a dead recorder to restart
        clock.now += 5
        d.check_session()
    assert d.recorder.starts == 1 and "recording_failed" not in d.notifier.sent

    assert state.request_pause(False) is None
    d.check_session()
    assert d.recorder.running and d.recorder.starts == 2
    assert d.status.data["state"] == state.RECORDING

    state.request_pause(True)
    d.end_session()                            # the pause was for this session only
    assert not state.CONTROL_FILE.exists()
    monkeypatch.setattr(daemon, "process_pending", lambda cfg, stop=None, in_use=None: [])
    wait_for_cutter(d)
    d.start_session("Retail", "Wow.exe")
    d.check_session()
    assert d.recorder.running and d.status.data["state"] == state.RECORDING


def test_a_pause_needs_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATUS_FILE", tmp_path / "status.json")
    assert state.request_pause(True) == "The recorder service isn't running."
    state.Status(tmp_path / "status.json").update(state=state.WAITING)
    assert state.request_pause(True) == "WoW isn't running."


# ── clips.process_pending ──

def test_process_pending_skips_the_file_in_use_and_stops_when_asked(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    data["paths"] = {"combat_log_dir": str(tmp_path / "logs"), "output_dir": str(tmp_path / "out")}
    cfg = config.from_dict(data)
    cfg.sessions_dir.mkdir(parents=True)
    old, new = cfg.sessions_dir / "a.mkv", cfg.sessions_dir / "b.mkv"
    for f in (old, new):
        f.write_bytes(b"x")              # nearly empty: moved to processed/ without ffmpeg

    stop = threading.Event()
    stop.set()
    assert clips.process_pending(cfg, stop) == []
    assert old.exists() and new.exists()

    [result] = clips.process_pending(cfg, in_use=lambda: new)
    assert result.recording == old
    assert not old.exists() and new.exists()


# ── WoW's window going away mid-session ──

def test_a_lost_window_is_recorded_again_when_wow_is_back(make):
    d, clock, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    d.recorder.lost = True                     # WoW crashed: the recorder says its window is gone
    d.check_session(100)
    assert not d.recorder.running
    assert d.status.data["problem"] is None    # quitting WoW looks the same for a few seconds
    clock.now += daemon.CAPTURE_NOTICE
    d.check_session(100)                       # the same WoW still there: wait, and say so
    assert d.recorder.starts == 1 and d.status.data["state"] == state.NOT_RECORDING
    assert "window closed" in d.status.data["problem"]
    clock.now += 5
    d.check_session(200)                       # WoW is back as a new process
    assert d.recorder.running and d.recorder.starts == 2
    assert d.status.data["state"] == state.RECORDING and d.status.data["problem"] is None


def test_a_lost_window_with_the_same_wow_is_retried_after_a_while(make):
    d, clock, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    d.recorder.lost = True
    d.check_session(100)
    clock.now += daemon.CAPTURE_RETRY
    d.check_session(100)
    assert d.recorder.running and d.recorder.starts == 2


def test_a_new_wow_process_needs_a_new_window_only_for_portal_capture(make):
    d, clock, _ = make()
    assert d.cfg.capture == "portal"
    d.start_session("Retail", "Wow.exe", pid=100)
    d.check_session(300)                       # WoW restarted between two polls
    assert d.recorder.running and d.recorder.starts == 2   # straight to a new recording
    d.cfg.capture = "DP-1"                     # a monitor goes on showing the new WoW
    d.check_session(400)
    assert d.recorder.starts == 2


def test_capture_lost_reads_the_recorders_output(tmp_path):
    from linux_wow_recorder import recorder
    r = recorder.Recorder(config.from_dict(config.load_raw(tmp_path / "missing.toml")))
    r.output_log = tmp_path / "out.log"
    r.output_log.write_text('gsr info: pipewire: stream 0x1 previous state: "paused", '
                            'new state: "streaming" (error: none)\ngsr info: update fps: 60\n')
    assert not r.capture_lost()
    with open(r.output_log, "a") as f:
        f.write('gsr info: pipewire: stream 0x1 previous state: "streaming", new state: "paused" '
                '(error: none)\ngsr info: pipewire: stream 0x1 previous state: "paused", new st')
    assert not r.capture_lost()                # half a line: read when it's whole
    with open(r.output_log, "a") as f:
        f.write('ate: "error" (error: no more input formats)\n')
    assert r.capture_lost()
    assert not r.capture_lost()                # said once


def test_quitting_wow_isnt_reported_as_a_lost_window(make, monkeypatch):
    d, clock, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    d.recorder.lost = True                     # the window goes first...
    d.check_session(100)
    clock.now += 5
    d.check_session(100)
    monkeypatch.setattr(daemon, "process_pending", lambda cfg, stop=None, in_use=None: [])
    d.end_session()                            # ...then the process, within two polls
    wait_for_cutter(d)
    assert d.status.data["problem"] is None
    assert "recording_failed" not in d.notifier.sent


def test_the_programs_with_sound_are_logged_once(make, caplog):
    caplog.set_level("INFO", logger="linux_wow_recorder")
    d, clock, _ = make()
    d.start_session("Retail", "WoW.exe", pid=100)
    d.check_session(100)
    assert "Programs with sound" not in caplog.text          # too early: WoW opens it later
    clock.now += daemon.AUDIO_CHECK_AFTER
    d.check_session(100)
    assert "Programs with sound now: Firefox, Wow.exe" in caplog.text
    assert "isn't among them" not in caplog.text              # any case
    caplog.clear()
    d.recorder.apps = ["Firefox"]
    clock.now += daemon.AUDIO_CHECK_AFTER
    d.check_session(100)
    assert "Programs with sound" not in caplog.text           # once per session

    d2, clock2, _ = make()
    d2.recorder.apps = ["Firefox"]
    d2.start_session("Retail", "WoW.exe", pid=100)
    clock2.now += daemon.AUDIO_CHECK_AFTER
    d2.check_session(100)
    assert "isn't among them" in caplog.text


def test_a_new_version_waits_for_the_session_the_cutting_and_the_hook(make, monkeypatch):
    d, clock, _ = make()
    monkeypatch.setattr(daemon.service, "installed_version", lambda: "0.6.0")
    d.updates.running = "0.5.0"

    def ready():
        clock.now += daemon.service.UPDATE_CHECK_SECONDS
        return d.update_ready()
    assert not ready()                 # seen once: maybe still being installed
    d.start_session("Retail", "Wow.exe", 7)
    assert not ready()                 # recording
    d.recorder.running = False
    d.end_session()
    wait_for_cutter(d)
    release = threading.Event()
    hook = threading.Thread(target=release.wait, args=(5,))
    hook.start()
    d._hooks.append(hook)
    assert not ready()                 # an after_clips upload is running
    release.set()
    hook.join()
    clock.now += 1
    assert not d.update_ready()        # too soon after the last check
    assert ready()


def test_run_restarts_into_a_new_version_when_idle(make, monkeypatch):
    d, clock, _ = make()
    d.cfg.poll_interval = 0
    monkeypatch.setattr(daemon, "find_wow", lambda: None)
    monkeypatch.setattr(daemon, "process_pending", lambda *a, **k: [])
    calls = []

    def find():
        if not calls:
            wait_for_cutter(d)         # what an earlier run left: cut at the start
        clock.now += 30
        calls.append(1)
        if len(calls) > 50:
            d._stop.set()
        return None
    monkeypatch.setattr(daemon, "find_wow_process", find)
    monkeypatch.setattr(daemon.service, "installed_version", lambda: "0.6.0")
    d.updates.running = "0.5.0"
    restarted = []
    monkeypatch.setattr(d, "restart_into_new_version", lambda: restarted.append(1))
    d.run()
    assert restarted == [1] and len(calls) < 50
    assert d.status.data["state"] == state.STOPPED


# ── The screen-share dialog ──

def test_waiting_for_a_pick_isnt_recording(make):
    d, _, _ = make()
    d.recorder.picking = True
    d.start_session("Retail", "Wow.exe", pid=100)
    assert d.status.data["state"] == state.NOT_RECORDING and d.status.data["picking"]
    assert d.status.data["problem"] == daemon.PICK_PROBLEM
    assert d.status.data["recording_file"] is None
    for _ in range(daemon.MAX_RESTARTS + 1):   # waiting, not dead: never restarted
        d.check_session(100)
    assert d.recorder.starts == 1
    d.recorder.waiting = False                 # picked
    d.check_session(100)
    assert d.status.data["state"] == state.RECORDING and d.status.data["problem"] is None
    assert d.status.data["recording_file"] == str(d.recorder.file)


def test_closing_the_dialog_without_a_pick_ends_this_sessions_recording(make):
    d, _, _ = make()
    d.recorder.picking = True
    d.start_session("Retail", "Wow.exe", pid=100)
    d.recorder.running = False                 # the dialog was cancelled
    for _ in range(3):
        d.check_session(100)
    assert d.recorder.starts == 1              # no dialog again and again
    assert d.status.data["state"] == state.NOT_RECORDING and not d.status.data["picking"]


def test_wow_closed_while_waiting_isnt_a_recorded_session(make, monkeypatch):
    d, _, _ = make()
    d.recorder.picking = True
    d.start_session("Retail", "Wow.exe", pid=100)
    monkeypatch.setattr(daemon, "process_pending", lambda cfg, stop=None, in_use=None: [])
    d.end_session()
    wait_for_cutter(d)
    assert "session_ended" not in d.notifier.sent


def test_record_monitor_instead_for_this_session_only(make):
    d, clock, _ = make()
    d.cfg.monitor = "DP-2"
    d.recorder.picking = True
    d.start_session("Retail", "Wow.exe", pid=100)
    assert state.request_monitor(True) is None
    d.check_session(100)
    assert d.recorder.capture_mode == "DP-2" and d.recorder.starts == 2
    assert d.status.data["state"] == state.RECORDING and d.status.data["monitor_switch"]
    assert d.status.data["capture"] == "DP-2"
    assert state.request_pause(True) is None   # another request keeps this one
    d.check_session(100)
    assert d.recorder.capture_mode == "DP-2" and d.status.data["state"] == state.PAUSED
    state.request_pause(False)
    assert state.request_monitor(False) is None
    d.check_session(100)
    assert d.recorder.capture_mode == "portal" and d.status.data["picking"]
    state.request_monitor(True)
    d.check_session(100)
    d.end_session()
    d.start_session("Retail", "Wow.exe", pid=200)
    assert d.recorder.capture_mode == "portal"  # the next session is the window again
    assert not d.status.data["monitor_switch"]


def test_monitor_instead_defaults_to_the_first_screen(make):
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    state.request_monitor(True)
    d.check_session(100)
    assert d.recorder.capture_mode == "screen"


def test_waiting_for_pick_reads_the_recorders_output(tmp_path, monkeypatch):
    from linux_wow_recorder import recorder
    r = recorder.Recorder(config.from_dict(config.load_raw(tmp_path / "missing.toml")))
    monkeypatch.setattr(r, "is_running", lambda: True)
    assert not r.waiting_for_pick()            # nothing started
    r.output_log = tmp_path / "out.log"
    r.output_log.write_text("gsr info: gsr_capture_portal_setup_dbus: Start\n")
    assert r.waiting_for_pick()
    with open(r.output_log, "a") as f:
        f.write('gsr info: pipewire: stream 0x1 previous state: "paused", new state: "streaming" '
                '(error: none)\n')
    assert not r.waiting_for_pick()
    r.capture = "DP-1"
    r.output_log.write_text("")
    r._streaming = False
    assert not r.waiting_for_pick()            # a monitor has no dialog
    assert r.command(tmp_path / "x.mkv")[1:3] == ["-w", "DP-1"]
    assert "-restore-portal-session" not in r.command(tmp_path / "x.mkv")


def test_pick_another_window(make):
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    assert d.status.data["state"] == state.RECORDING
    d.recorder.picking = True                  # the dialog opens again once the pick is forgotten
    assert state.request_repick() is None
    d.check_session(100)
    assert d.recorder.forgotten == 1 and d.recorder.starts == 2
    assert d.status.data["picking"] and d.status.data["problem"] == daemon.PICK_PROBLEM
    d.check_session(100)
    assert d.recorder.starts == 2              # acted on once
    d.recorder.waiting = False
    d.check_session(100)
    assert d.status.data["state"] == state.RECORDING


def write_combat_log(d, when: float):
    """A combat log in the session, last written at when."""
    log = d.cfg.combat_log_dir / "WoWCombatLog-102526_200237.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch()
    os.utime(log, (when, when))


def test_cut_the_clips_so_far_records_on_in_a_new_file(make, monkeypatch):
    d, clock, _ = make()
    entered, release, calls = blocking_process_pending(monkeypatch)
    in_key = [True]
    monkeypatch.setattr(daemon, "run_in_progress", lambda events: in_key[0])
    d.start_session("Retail", "Wow.exe", pid=100)
    first = d.recorder.file
    first.parent.mkdir(parents=True)
    first.touch()                              # the next one, maybe the same second, isn't named so
    monkeypatch.setattr(state, "time", clock)
    assert state.request_cut() is None
    write_combat_log(d, clock.now + 1)
    d.check_session(100)                       # inside a key: it waits
    assert d.recorder.starts == 1 and d._cutter is None and d.status.data["cut_waiting"]
    in_key[0] = False
    clock.now += 5
    d.check_session(100)                       # the log isn't read again so soon
    assert d._cutter is None
    clock.now += daemon.CUT_CHECK_INTERVAL
    d.check_session(100)
    assert entered.wait(5)
    assert d.recorder.starts == 2 and d.recorder.running and d.recorder.file != first
    assert calls == [d.recorder.file]          # the new recording isn't cut
    assert not d.status.data["cut_waiting"] and d.status.data["state"] == state.RECORDING
    release.set()
    wait_for_cutter(d)
    clock.now += daemon.CUT_CHECK_INTERVAL
    d.check_session(100)
    assert d.recorder.starts == 2 and d._cutter is None   # acted on once


def test_cut_the_clips_so_far_waits_for_the_combat_log_to_be_written(make, monkeypatch):
    d, clock, _ = make()
    blocking = blocking_process_pending(monkeypatch)
    read = []
    monkeypatch.setattr(daemon, "run_in_progress", lambda events: read.append(1) or False)
    monkeypatch.setattr(state, "time", clock)
    d.start_session("Retail", "Wow.exe", pid=100)
    write_combat_log(d, clock.now - 1)         # last written before the click
    assert state.request_cut() is None
    d.check_session(100)                       # a key started just now may not be in it yet
    assert not read and d._cutter is None and not d.status.data["cut_waiting"]
    clock.now += 3
    write_combat_log(d, clock.now)
    d.check_session(100)                       # written since: no need to wait for the interval
    assert read and blocking[0].wait(5)
    blocking[1].set()
    wait_for_cutter(d)


def test_cut_the_clips_so_far_goes_ahead_if_the_combat_log_stays_quiet(make, monkeypatch):
    d, clock, _ = make()
    blocking = blocking_process_pending(monkeypatch)
    monkeypatch.setattr(daemon, "run_in_progress", lambda events: False)
    monkeypatch.setattr(state, "time", clock)
    d.start_session("Retail", "Wow.exe", pid=100)
    assert state.request_cut() is None
    clock.now += daemon.CUT_LOG_WAIT - 1
    d.check_session(100)
    assert d._cutter is None
    clock.now += 1
    d.check_session(100)
    assert blocking[0].wait(5)
    blocking[1].set()
    wait_for_cutter(d)


def test_pick_another_window_does_nothing_for_a_monitor(make):
    d, _, _ = make()
    d.cfg.capture = "DP-1"
    d.start_session("Retail", "Wow.exe", pid=100)
    state.request_repick()
    d.check_session(100)
    assert d.recorder.forgotten == 0 and d.recorder.starts == 1


def test_forget_pick_removes_the_saved_pick(tmp_path, monkeypatch):
    from linux_wow_recorder import recorder
    tokens = (tmp_path / "a" / "restore_token", tmp_path / "missing" / "restore_token")
    tokens[0].parent.mkdir()
    tokens[0].write_text("x")
    monkeypatch.setattr(recorder, "RESTORE_TOKENS", tokens)
    recorder.Recorder(config.from_dict(config.load_raw(tmp_path / "missing.toml"))).forget_pick()
    assert not tokens[0].exists()


def test_raid_pulls_are_one_line_per_raid_in_discord():
    from linux_wow_recorder.combatlog import Run
    pulls = [Run(boss, 0, 100, f"Heroic {r.capitalize()}", result=r,
                 raid="Liberation of Undermine", difficulty="Heroic", pull=1)
             for boss, r in (("Vexie", "wipe"), ("Vexie", "kill"), ("Rik Reverb", "wipe"))]
    pulls.append(Run("Vexie", 0, 100, "LFR Wipe", result="wipe",
                     raid="Liberation of Undermine", difficulty="LFR", pull=1))
    assert daemon.raid_summary(pulls) == [
        "• Liberation of Undermine (Heroic): 3 pulls, kills: Vexie",
        "• Liberation of Undermine (LFR): 1 pull, no kills"]


def test_a_pause_outlives_a_service_restart_while_wow_runs(make):
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    assert state.request_pause(True) is None
    d.check_session(100)
    assert d.status.data["state"] == state.PAUSED
    d, _, _ = make()                           # the service restarted (a Settings save)
    d.start_session("Retail", "Wow.exe", pid=100)
    assert d.recorder.starts == 0 and d.status.data["state"] == state.PAUSED
    d.end_session()
    d.start_session("Retail", "Wow.exe", pid=200)   # WoW started again: recording
    assert d.recorder.running


def test_a_pause_for_another_wow_is_dropped_on_restart(make):
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    assert state.request_pause(True) is None
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=300)   # WoW closed and came back meanwhile
    assert d.recorder.running and d.status.data["state"] == state.RECORDING


def test_a_launcher_process_found_first_isnt_a_wow_restart(make, monkeypatch):
    d, _, _ = make()
    d.start_session("Retail", "Wow.exe", pid=100)
    monkeypatch.setattr(daemon, "wow_alive", lambda pid: pid == 100)
    d.check_session(300)                        # the old process still runs: same WoW
    assert d.recorder.starts == 1 and d.session.pid == 300
    monkeypatch.setattr(daemon, "wow_alive", lambda pid: False)
    d.check_session(300)
    assert d.recorder.starts == 1
