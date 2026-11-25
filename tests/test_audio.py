import pytest

from linux_wow_recorder import config
from linux_wow_recorder.config import DEFAULTS, audio_tracks, game_sound
from linux_wow_recorder.recorder import WOW_EXES, Recorder, audio_source


def test_default_is_wow_only():
    assert DEFAULTS["recorder"]["audio"] == ["wow"]
    assert game_sound(DEFAULTS["recorder"]["audio"]) == "wow"


@pytest.mark.parametrize("game, mic, tracks", [
    ("wow", False, ["wow"]),
    ("wow", True, ["wow", "default_input"]),
    ("desktop", False, ["default_output"]),
    ("desktop", True, ["default_output", "default_input"]),
    ("none", False, []),
    ("none", True, ["default_input"]),
])
def test_choices_round_trip(game, mic, tracks):
    assert audio_tracks(game, mic) == tracks
    assert game_sound(tracks) == game


def test_older_configs_read_as_desktop_sound():
    assert game_sound(["default_output"]) == "desktop"
    assert game_sound(["default_output|default_input"]) == "desktop"
    assert game_sound(["wow|default_input"]) == "wow"


def test_wow_becomes_the_running_exe_and_the_games_name():
    assert audio_source("wow", "WowClassic.exe") == "app:WowClassic.exe|app:World of Warcraft"
    assert audio_source("wow|default_input", "Wow.exe") == \
        "app:Wow.exe|app:World of Warcraft|default_input"
    assert audio_source("default_output", "Wow.exe") == "default_output"


def test_wow_without_a_running_game_lists_every_known_exe():
    assert audio_source("wow", None) == \
        "|".join(f"app:{e}" for e in WOW_EXES) + "|app:World of Warcraft"


def test_recorder_command(tmp_path):
    data = config.DEFAULTS | {"paths": {"combat_log_dir": str(tmp_path), "output_dir": str(tmp_path)}}
    rec = Recorder(config.from_dict(data))
    rec.game_exe = "Wow.exe"
    cmd = rec.command(tmp_path / "out.mkv")
    assert cmd[cmd.index("-a") + 1] == "app:Wow.exe|app:World of Warcraft"


def test_a_leftover_recorder_is_adopted_only_if_it_writes_the_saved_file(tmp_path, monkeypatch):
    """The state file outlives a reboot: its process ID can be another recorder's by then."""
    import json
    import subprocess
    import sys

    from linux_wow_recorder import recorder
    monkeypatch.setattr(recorder, "STATE_FILE", tmp_path / "recorder.json")
    ours = str(tmp_path / "2026-10-25_18-00-00.mkv")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)",
                             "gpu-screen-recorder", "-o", ours])
    try:
        data = config.DEFAULTS | {"paths": {"combat_log_dir": str(tmp_path),
                                            "output_dir": str(tmp_path)}}
        cfg = config.from_dict(data)
        recorder.STATE_FILE.write_text(json.dumps({"pid": proc.pid, "file": ours}))
        assert Recorder(cfg).adopt_leftover()
        recorder.STATE_FILE.write_text(json.dumps({"pid": proc.pid,
                                                   "file": str(tmp_path / "other.mkv")}))
        assert not Recorder(cfg).adopt_leftover()
        assert not recorder.STATE_FILE.exists()
    finally:
        proc.kill()
        proc.wait()


def test_the_recorder_gets_the_desktops_display_as_it_is_now(tmp_path, monkeypatch):
    # The service can start before the desktop, so its own environment has no display.
    from linux_wow_recorder import detect, recorder
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(detect, "desktop_env", lambda: {"WAYLAND_DISPLAY": "wayland-0"})
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)
    seen = {}

    class FakeProc:
        pid = 4321

        def __init__(self, cmd, **kwargs):
            seen.update(kwargs["env"])

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(recorder.subprocess, "Popen", FakeProc)
    data = config.DEFAULTS | {"paths": {"combat_log_dir": str(tmp_path), "output_dir": str(tmp_path)}}
    Recorder(config.from_dict(data)).start(tmp_path / "out.mkv")
    assert seen["WAYLAND_DISPLAY"] == "wayland-0"


def test_canceled_reads_the_recorders_output(tmp_path):
    rec = Recorder(config.from_dict(config.load_raw(tmp_path / "missing.toml")))
    assert not rec.canceled()
    rec.output_log = tmp_path / "out.log"
    rec.output_log.write_text("gsr error: failed to create window\n")
    assert not rec.canceled()
    rec.output_log.write_text("gsr error: gsr_capture_portal_setup: desktop portal capture failed. "
                              "It seems like desktop portal capture was canceled by the user.\n")
    assert rec.canceled()
