from linux_wow_recorder.gui import model

NOW = 1_800_000_000.0


def test_recording():
    view = model.describe({"state": "recording", "flavor": "Retail", "session_start": NOW - 3725,
                           "free_gb": 412.3, "problem": None}, "active", now=NOW)
    assert view.icon == model.RECORDING and view.recording
    assert view.title == "Recording  1:02:05"
    assert view.details[1] == "412 GB free"


def test_problem_wins_the_icon():
    view = model.describe({"state": "not_recording", "flavor": "Retail",
                           "problem": "Only 3.0 GB free; not recording."}, "active", now=NOW)
    assert view.icon == model.ATTENTION and not view.recording
    assert "Only 3.0 GB free; not recording." in view.details


def test_service_states_without_a_daemon():
    assert model.describe(None, "not installed").icon == model.OFF
    assert model.describe(None, "failed").icon == model.ATTENTION
    assert model.describe(None, "inactive").title == "Recorder service stopped"
    assert not model.describe(None, "inactive").service_running


def test_last_session_lines():
    status = {"last_session": {"end": NOW, "clips": ["a.mp4", "b.mp4"],
                               "problems": ["No combat log covers `x.mkv`. Is combat logging on?"]}}
    lines = model.last_session_lines(status)
    assert lines[0].endswith(": 2 clips")
    assert lines[1] == "⚠ No combat log covers x.mkv. Is combat logging on?"
    assert model.last_session_lines({"last_session": None}) == []


def test_parse_capture_options():
    # gpu-screen-recorder 6.1.3 on Wayland with a webcam: no "screen" line, one line per webcam mode
    out = ("DP-1|2560x1440\nDP-2|2560x1440\nregion\n/dev/video1|2560x1472@2hz|yuyv\n"
           "/dev/video1|1280x720@30hz|mjpeg\nportal\n")
    assert model.parse_capture_options(out) == [
        ("portal", "A window or screen you pick (e.g. only WoW)"), ("screen", "Whole screen: always DP-1, the first one gpu-screen-recorder lists"),
        ("DP-1", "Monitor DP-1 (2560×1440)"), ("DP-2", "Monitor DP-2 (2560×1440)")]
    # older versions list "screen" themselves; X11 adds window and focused
    out = "window\nfocused\nscreen\nportal\nHDMI-A-1|1920x1080\n"
    assert [v for v, _ in model.parse_capture_options(out)] == ["portal", "screen", "HDMI-A-1"]
    assert model.parse_capture_options("portal\nscreen\n")[1] == ("screen", model.SCREEN_LABEL)
    assert model.parse_capture_options("") == []   # not installed: the settings window's defaults


def test_combat_log_hint(tmp_path):
    import os
    assert model.combat_log_hint(str(tmp_path / "nope")) == "Folder not found"
    assert model.combat_log_hint(str(tmp_path)).startswith("No combat logs")
    log = tmp_path / "WoWCombatLog-100826_190000.txt"
    log.write_text("x")
    os.utime(log, (NOW - 600, NOW - 600))
    assert model.combat_log_hint(str(tmp_path), now=NOW) == f"Newest: {log.name}, written 10 min ago"


def test_clip_info_from_sidecar_and_from_name(tmp_path):
    from linux_wow_recorder.clips import read_sidecar, write_sidecar
    old = tmp_path / "2026-10-08 19-03 Kings' Rest +12 Completed (24m52s).mp4"
    new = tmp_path / "2026-10-09 20-00 Ara-Kara +10 Abandoned (8m00s).mp4"
    for f in (old, new):
        f.write_bytes(b"x")
    write_sidecar(new, {"dungeon": "Ara-Kara, City of Echoes", "level": 10, "label": "+10 Abandoned",
                        "result": "abandoned", "run_length": "8m00s", "run_start": NOW,
                        "deaths_total": 4, "bosses_killed": 1, "keep": True})
    clips = model.load_clips(tmp_path, read_sidecar)
    assert [c.dungeon for c in clips] == ["Ara-Kara, City of Echoes", "Kings' Rest"]
    a, k = clips
    assert (a.level, a.filter_key, a.deaths, a.keep) == (10, "abandoned", 4, True)
    assert (k.level, k.result, k.length, k.deaths) == (12, "completed", "24m52s", None)
    assert model.matches(k, "completed", "king") and not model.matches(k, "nokey", "")


def test_markers():
    found = model.markers({
        "run_offset": 10,
        "encounters": [{"name": "Boss", "start": 40, "end": 100, "killed": True}],
        "deaths": [{"name": "Tank-Realm-US", "at": 50}],
        "lusts": [{"name": "Bloodlust", "caster": "Shammy-Realm-US", "at": 41}],
    })
    assert [(m.at, m.kind, m.text) for m in found] == [
        (10, "start", "Run starts"), (40, "pull", "Boss pulled"), (41, "lust", "Bloodlust (Shammy)"),
        (50, "death", "Tank died"), (100, "kill", "Boss killed")]


def test_capture_monitor():
    options = ["portal", "screen", "DP-1", "DP-2"]
    assert model.capture_monitor("DP-2", options) == "DP-2"
    assert model.capture_monitor("screen", options) == "DP-1"   # the first one listed
    assert model.capture_monitor("portal", options) is None
    assert model.capture_monitor("screen", ["portal", "screen"]) is None
