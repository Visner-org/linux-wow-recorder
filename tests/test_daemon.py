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

    def start(self, output):
        self.starts += 1
        if not self.works:
            return False
        self.file, self.running = output, True
        return True

    def is_running(self):
        return self.running

    def stop(self, timeout=30):
        self.running, self.file = False, None

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
