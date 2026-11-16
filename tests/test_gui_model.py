import time
from pathlib import Path
from types import SimpleNamespace

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


def test_combat_log_hint_looks_in_subfolders_but_not_everywhere(tmp_path):
    import os
    archive = tmp_path / "archive" / "2026"
    archive.mkdir(parents=True)
    old, new = tmp_path / "WoWCombatLog-1.txt", archive / "WoWCombatLog-2.txt"
    for f, t in ((old, NOW - 7200), (new, NOW - 60)):
        f.write_text("x")
        os.utime(f, (t, t))
    assert model.newest_combat_log(tmp_path) == ((NOW - 60, new.name), False)
    assert model.newest_combat_log(tmp_path, max_depth=1) == ((NOW - 7200, old.name), False)

    # A home folder typed by mistake: the search stops instead of walking all of it.
    home = tmp_path / "home"
    for i in range(20):
        (home / f"d{i}").mkdir(parents=True)
    assert model.newest_combat_log(home, max_folders=5) == (None, True)


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
    assert model.matches(k, "completed", "king") and not model.matches(k, "abandoned", "")
    assert "nokey" not in model.FILTERS


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


def test_folder_to_watch(tmp_path):
    clips = tmp_path / "out" / "clips"
    assert model.folder_to_watch(clips) == tmp_path   # the output folder's parent
    clips.parent.mkdir()
    assert model.folder_to_watch(clips) == clips.parent
    clips.mkdir()
    assert model.folder_to_watch(clips) == clips
    assert model.folder_to_watch(tmp_path / "a" / "b" / "c" / "clips") is None


def test_result_text_comes_from_the_result_not_the_label(tmp_path):
    from linux_wow_recorder.clips import read_sidecar, write_sidecar
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"x")
    write_sidecar(clip, {"dungeon": "Ara-Kara", "label": "+10 Something Else",
                         "result": "abandoned", "level": 10})
    [info] = model.load_clips(tmp_path, read_sidecar)
    assert info.result_text == "Abandoned" and info.filter_key == "abandoned"


def test_completed_keys_say_timed_or_over_time(tmp_path):
    from linux_wow_recorder.clips import read_sidecar, write_sidecar
    for name, details in (
            # cut before time_limit was in the details: from the table, by name
            ("a.mp4", {"dungeon": "Den of Nalorakk", "timer": 2189.5}),
            ("b.mp4", {"dungeon": "Somewhere", "timer": 1000.0, "time_limit": 1800}),
            ("c.mp4", {"dungeon": "New Dungeon", "timer": 1000.0}),
            # no timer in the details: the length in the details or the name
            ("d.mp4", {"dungeon": "Kings' Rest", "run_length": "24m52s"})):
        (tmp_path / name).write_bytes(b"x")
        write_sidecar(tmp_path / name, {"result": "completed", "level": 12,
                                        "label": "+12 Completed", **details})
    by = {c.path.name: c for c in model.load_clips(tmp_path, read_sidecar)}
    a, b, c = by["a.mp4"], by["b.mp4"], by["c.mp4"]
    assert (a.result_text, a.status, a.time_left) == ("Over time", "overtime", "4m29s over")
    assert (b.result_text, b.status, b.time_left) == ("Timed +3", "completed", "13m20s left")
    assert (c.result_text, c.status, c.time_left) == ("Completed", "completed", "")
    assert (by["d.mp4"].result_text, by["d.mp4"].time_left) == ("Timed +2", "8m08s left")
    assert a.filter_key == b.filter_key == "completed"


DETAILS = {
    "run_offset": 30.0,
    "encounters": [{"name": "Avanoxx", "start": 60.0, "end": 120.0, "killed": True,
                    "forces": 41.8},
                   {"name": "Anub'zekt", "start": 300.0, "end": 330.0, "killed": False},
                   {"name": "Anub'zekt", "start": 400.0, "end": None, "killed": False}],
    "deaths": [{"name": "Tank-Realm-US", "at": 90.0}, {"name": "Healer-Realm-US", "at": 200.0},
               {"name": "Mage-Realm-US", "at": 330.0}],
    "lusts": [{"name": "Bloodlust", "caster": "Shammy-Realm-US", "at": 61.0}],
}


def test_event_rows_group_deaths_and_lusts_under_their_pull():
    rows = model.event_rows(DETAILS)
    assert [(r.at, r.kind, r.text, r.detail) for r in rows] == [
        (30.0, "start", "Run starts", ""),
        (60.0, "kill", "Avanoxx", "killed in 1:00 · 41.80% forces · 1 death"),
        (200.0, "death", "Healer died", ""),                    # between bosses
        (300.0, "wipe", "Anub'zekt", "wipe after 0:30 · 1 death"),
        (400.0, "pull", "Anub'zekt", "pulled"),                 # the clip ends mid-pull
    ]
    assert [(c.at, c.text) for c in rows[1].children] == [
        (61.0, "Bloodlust (Shammy)"), (90.0, "Tank died"), (120.0, "Killed")]
    assert [c.text for c in rows[3].children] == ["Mage died", "Wipe"]   # a death at the wipe
    assert rows[4].children == []


def test_timeline_spans():
    assert [(s.start, s.end, s.kind) for s in model.boss_spans(DETAILS, 500.0)] == [
        (60.0, 120.0, "kill"), (300.0, 330.0, "wipe"), (400.0, 500.0, "pull")]
    [lust] = model.lust_spans(DETAILS, 80.0)
    assert (lust.start, lust.end, lust.text) == (61.0, 80.0, "Bloodlust (Shammy)")   # cut at the end
    assert model.seek_target(61.0) == 58.0 and model.seek_target(1.0) == 0.0



def test_youtube_chapters_and_description():
    # Start, the run starting, each pull; a second pull of a boss says so.
    assert model.youtube_chapters(DETAILS) == [
        (0.0, "Start"), (30.0, "Run starts"), (60.0, "Avanoxx (kill)"),
        (300.0, "Anub'zekt (wipe)"), (400.0, "Anub'zekt pull 2")]
    # Too close together (under 10 s) or too few: YouTube wouldn't make chapters of them.
    assert model.youtube_chapters({"run_offset": 5.0, "encounters": [
        {"name": "Avanoxx", "start": 60.0, "end": 120.0, "killed": True}]}) == []
    assert model.chapter_time(3725) == "1:02:05" and model.chapter_time(245) == "4:05"

    clip = model.clip_info(Path("a.mp4"), {**DETAILS, "run_start": 1_800_000_000.0,
                                           "dungeon": "Ara-Kara <City>", "level": 10,
                                           "label": "+10 Completed", "result": "completed",
                                           "run_length": "8m00s", "deaths_total": 3})
    text = model.youtube_description(clip)
    day = time.strftime("%Y-%m-%d", time.localtime(1_800_000_000))
    assert text.startswith(f"Ara-Kara City +10 Completed, 8m00s, {day}, 3 deaths.\n\n0:00 Start\n")
    assert text.endswith("6:40 Anub'zekt pull 2\n")


def test_field_checks(tmp_path):
    from linux_wow_recorder import config
    data = config.load_raw(tmp_path / "missing.toml")
    checks = model.field_checks(data, free_gb=lambda path: 42.0)
    assert checks["combat_log_dir"].level == model.ERROR and checks["output_dir"].level == model.ERROR

    logs = tmp_path / "Logs"
    logs.mkdir()
    data["paths"] = {"combat_log_dir": str(logs), "output_dir": str(tmp_path / "out")}
    data["recorder"]["command"] = "sh"
    data["hooks"]["after_clips"] = "~/no-such-script.sh"
    data["discord"]["webhook_urls"] = ["https://discord.com/api/webhooks/1/x", "discord.com/oops"]
    data["storage"].update(min_free_gb=20, stop_below_gb=25)
    checks = model.field_checks(data, free_gb=lambda path: 42.0)
    assert checks["combat_log_dir"] == model.Check(model.WARNING, model.combat_log_hint(str(logs)))
    assert checks["output_dir"] == model.Check(model.INFO, "Will be created when you save (42 GB free)")
    assert checks["recorder_command"].level == model.OK
    assert checks["after_clips"].level == model.WARNING
    assert checks["webhooks"].text.endswith("#2")
    assert checks["stop_below_gb"].level == model.WARNING

    (tmp_path / "out").mkdir()
    data["recorder"]["command"] = "no-such-recorder -x"
    data["hooks"]["after_clips"] = "'unclosed"
    checks = model.field_checks(data, free_gb=lambda path: 42.0)
    assert checks["output_dir"] == model.Check(model.OK, "42 GB free")
    assert checks["recorder_command"] == model.Check(model.ERROR, "no-such-recorder isn't installed")
    assert checks["after_clips"].level == model.ERROR


def test_describe_paused():
    view = model.describe({"state": "paused", "flavor": "Retail", "session_start": NOW - 600,
                           "free_gb": 100}, "active", now=NOW)
    assert view.paused and not view.recording and view.icon == model.IDLE
    assert view.title == "Paused for this session"
    assert view.details[0].endswith("the next session records")


def test_setup_checklist(tmp_path, monkeypatch):
    from linux_wow_recorder import config
    data = config.load_raw(tmp_path / "missing.toml")
    logs, out = tmp_path / "Logs", tmp_path / "out"
    logs.mkdir()
    out.mkdir()
    data["paths"] = {"combat_log_dir": str(logs), "output_dir": str(out)}
    data["recorder"]["command"] = "sh"
    cfg = config.from_dict(data)
    monkeypatch.setattr(model, "missing_tools", lambda: [])
    steps = {s.title: s.ok for s in model.setup_checklist(cfg, "active")}
    assert steps == {"gpu-screen-recorder installed": True, "ffmpeg, ffprobe and grep installed": True,
                     "Combat log folder found": True, "Combat logging on": False,
                     "Advanced Combat Logging on": True, "Combat log version known": True,
                     "Output folder ready": True, "Recorder service running": True}
    (logs / "WoWCombatLog-100826_190000.txt").write_text("x")
    assert all(s.ok for s in model.setup_checklist(cfg, "active"))
    # The newest log's first line says how it was written: neither stops the recorder
    (logs / "WoWCombatLog-100826_190000.txt").write_text(
        "10/8/2026 19:00:00.000-7  COMBAT_LOG_VERSION,23,ADVANCED_LOG_ENABLED,0,BUILD_VERSION,12.2.0,"
        "PROJECT_ID,1\n")
    off = [s for s in model.setup_checklist(cfg, "active") if not s.ok]
    assert [s.title for s in off] == ["Advanced Combat Logging on", "Combat log version known"]
    assert not any(s.needed for s in off) and "version 23" in off[1].hint
    assert not all(s.ok for s in model.setup_checklist(cfg, "inactive"))
    assert [s.ok for s in model.setup_checklist(None, "not installed")] == [False]


def test_license_text_is_the_gpl():
    text = model.license_text()
    assert text is not None and "GNU GENERAL PUBLIC LICENSE" in text and "Version 3" in text


RUN = {"dungeon": "The Stonevault", "label": "+12 Completed", "run_length": "30m00s",
       "deaths_total": 1, "bosses_killed": 1, "run_offset": 10.0,
       "encounters": [{"name": "E.D.N.A.", "start": 100.0, "end": 160.0, "killed": True}],
       "deaths": [{"name": "Tank-Realm-US", "at": 120.0}], "lusts": []}


def test_a_session_with_its_details_file(tmp_path):
    rec = tmp_path / "2026-10-12_16-39-07.mkv"
    info = model.session_info(rec, {"recording_start": 1000.0, "length": 8040.0, "keep": True,
                                    "runs": [RUN, {**RUN, "dungeon": "Ara-Kara", "run_offset": 5.0}]}, [])
    assert info.is_session and info.started == 1000.0 and info.keep
    assert (info.dungeon, info.result_text, info.length) == ("Ara-Kara, The Stonevault", "2 runs", "2h14m")
    assert info.deaths == 2 and len(info.details["encounters"]) == 2
    rows = model.session_event_rows(info.details)
    assert [(r.text, r.detail) for r in rows] == [("Ara-Kara +12 Completed", "30m00s · 1 death"),
                                                  ("The Stonevault +12 Completed", "30m00s · 1 death")]
    assert [c.text for c in rows[0].children] == ["E.D.N.A."]   # the pull, holding its death
    assert any(m.kind == "start" and m.text.startswith("Ara-Kara") for m in model.markers(info.details))


def test_an_older_session_takes_its_runs_from_its_clips(tmp_path):
    import time
    rec = tmp_path / "2026-10-12_16-39-07.mkv"
    start = time.mktime(time.strptime("2026-10-12 16:39:07", "%Y-%m-%d %H:%M:%S"))
    clip = {**RUN, "clip_start": start + 600.0}   # its clip starts 10 minutes in
    info = model.session_info(rec, {}, [clip])
    assert info.started == start and info.length == "" and info.result_text == "1 run"
    [run] = info.details["runs"]
    assert run["run_offset"] == 610.0 and run["encounters"][0]["start"] == 700.0
    assert run["deaths"][0]["at"] == 720.0
    assert model.session_info(tmp_path / "not-a-recording.mkv", {}, []) is None


def test_load_sessions(tmp_path):
    import json
    processed, sessions, clips = (tmp_path / n for n in ("processed", "sessions", "clips"))
    for d in (processed, sessions, clips):
        d.mkdir()
    big = b"x" * 2000
    (processed / "2026-10-10_20-00-00.mkv").write_bytes(big)
    (processed / "2026-10-10_20-00-00.json").write_text(json.dumps({"recording_start": 1.0, "runs": []}))
    (processed / "2026-10-11_20-00-00.mkv").write_bytes(b"x")          # a failed start
    (sessions / "2026-10-12_20-00-00.mkv").write_bytes(big)            # not cut yet
    (sessions / "2026-10-12_21-00-00.mkv").write_bytes(big)            # being recorded

    def read(path):
        try:
            return json.loads(path.with_suffix(".json").read_text())
        except OSError:
            return {}
    found = model.load_sessions(processed, sessions, clips, read,
                                str(sessions / "2026-10-12_21-00-00.mkv"), min_bytes=1000)
    assert [(c.path.name, c.result, c.result_text) for c in found] == [
        ("2026-10-12_20-00-00.mkv", "uncut", "Not cut yet"),
        ("2026-10-10_20-00-00.mkv", "session", "Session")]
    assert found[0].deaths is None and found[1].deaths == 0


def test_clips_moved_to_another_folder(tmp_path):
    import json
    clips, moved = tmp_path / "clips", tmp_path / "server"
    clips.mkdir()
    moved.mkdir()
    name = "2026-10-12 14-14 Temple of Sethraliss +10 Completed (27m46s)"
    # Cut here, moved away and renamed with hyphens; its .json and .jpg stayed behind.
    (clips / f"{name}.json").write_text(json.dumps({**RUN, "dungeon": "Temple of Sethraliss",
                                                    "run_start": 5000.0, "thumbnail": f"{name}.jpg"}))
    (moved / f"{name.replace(' ', '-')}.mp4").write_bytes(b"x")
    # Moved without its details: only the name to go on.
    (moved / "2026-10-12-16-39-Voidscar-Arena-+10-Abandoned-(13m21s).mp4").write_bytes(b"x")
    # In both places: listed once, from clips/.
    (clips / "2026-10-09 18-14 The Blinding Vale +8 Completed (22m14s).mp4").write_bytes(b"x")
    (moved / "2026-10-09-18-14-The-Blinding-Vale-+8-Completed-(22m14s).mp4").write_bytes(b"x")
    (moved / "not a clip.mp4").write_bytes(b"x")

    def read(path):
        try:
            return json.loads(path.with_suffix(".json").read_text())
        except OSError:
            return {}
    found = model.load_clips(clips, read, [moved, tmp_path / "not-mounted"])
    by_name = {c.dungeon: c for c in found}
    assert set(by_name) == {"Temple of Sethraliss", "Voidscar Arena", "The Blinding Vale"}
    temple = by_name["Temple of Sethraliss"]
    assert temple.elsewhere and temple.details_file == clips / f"{name}.json"
    assert temple.deaths == 1 and temple.path.parent == moved
    void = by_name["Voidscar Arena"]
    assert (void.level, void.label, void.result, void.length) == (10, "+10 Abandoned", "abandoned", "13m21s")
    assert void.deaths is None and void.details_file is None
    assert not by_name["The Blinding Vale"].elsewhere

    # Full sessions find a moved clip's runs from the details it left behind.
    (clips / f"{name}.json").write_text(json.dumps({**RUN, "recording": "2026-10-12_14-00-00.mkv",
                                                    "clip_start": 9000.0}))
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "2026-10-12_14-00-00.mkv").write_bytes(b"x" * 2000)
    [session] = model.load_sessions(processed, tmp_path / "sessions", clips, read, min_bytes=1000)
    assert session.result_text == "1 run"


def test_other_folders_are_checked():
    import copy

    from linux_wow_recorder import config
    data = copy.deepcopy(config.DEFAULTS)
    data["clips"]["other_folders"] = ["/no/such/folder"]
    check = model.field_checks(data, free_gb=lambda p: 100)["other_folders"]
    assert check.level == model.WARNING and "/no/such/folder" in check.text


def test_describe_offers_the_monitor_switch_only_for_a_picked_window():
    waiting = {"state": "not_recording", "session_start": NOW - 5, "capture": "portal",
               "picking": True, "problem": "Not recording: pick WoW's window."}
    view = model.describe(waiting, "active", now=NOW)
    assert view.can_switch and not view.monitor_switch and view.icon == model.ATTENTION
    assert model.switch_text(view.monitor_switch) == "Record monitor instead"
    switched = {"state": "recording", "session_start": NOW - 5, "capture": "DP-1",
                "monitor_switch": True}
    view = model.describe(switched, "active", now=NOW)
    assert view.can_switch and model.switch_text(view.monitor_switch) == "Record the window again"
    monitor = {"state": "recording", "session_start": NOW - 5, "capture": "DP-1"}
    assert not model.describe(monitor, "active", now=NOW).can_switch
    assert not model.describe({"state": "waiting"}, "active", now=NOW).can_switch


def test_describe_offers_another_pick_while_recording_a_window():
    recording = {"state": "recording", "session_start": NOW - 5, "capture": "portal"}
    assert model.describe(recording, "active", now=NOW).can_repick
    picking = {"state": "not_recording", "session_start": NOW - 5, "capture": "portal",
               "picking": True}
    assert not model.describe(picking, "active", now=NOW).can_repick   # the dialog's open already
    monitor = {"state": "recording", "session_start": NOW - 5, "capture": "DP-1"}
    assert not model.describe(monitor, "active", now=NOW).can_repick


def test_highlights_list_their_own_length_and_frame_events():
    info = model.clip_info(Path("a.mp4"), {"dungeon": "Ara-Kara", "result": "completed", "run_start": 1.0,
                                           "run_length": "24m00s",
                                           "highlight": {"start": 1.0, "end": 46.0, "length": "0m45s"}})
    assert info.highlight and info.length == "0m45s" and info.timer is None
    assert model.highlight_around("pull", 100.0, 200.0, 1500.0) == (95.0, 205.0)
    assert model.highlight_around("pull", 100.0, None, 150.0) == (95.0, 150.0)
    assert model.highlight_around("death", 10.0, None, 1500.0) == (0.0, 20.0)
    assert model.highlight_around("kill", 200.0, None, 1500.0) == (180.0, 210.0)
    assert model.highlight_around("lust", 1490.0, None, 1500.0) == (1485.0, 1500.0)


def test_casts_are_read_from_the_details():
    d = {"casters": ["Me-R-US", "Healer-R-US"],
         "casts": [[12.0, 2, 1], [3.0, 1, 0, 2.5], ["bad"], [4.0, 1, 7], [5.0, 99, 0]],
         "spells": {"1": "Pyroblast", "2": "Rejuvenation"}}
    assert model.casts(d) == [model.CastMark(3.0, 1, "Pyroblast", "Me-R-US", 2.5),
                              model.CastMark(5.0, 99, "Spell 99", "Me-R-US"),
                              model.CastMark(12.0, 2, "Rejuvenation", "Healer-R-US")]
    assert model.casts({}) == [] and model.casters(d) == ["Me-R-US", "Healer-R-US"]
    assert model.cast_text(model.casts(d)[0]) == "0:03  Me: Pyroblast (cast 2.5 s)"
    assert model.cast_text(model.CastMark(61.0, 190319, "Combustion", "Me-R-US")) == (
        "1:01  Me: Combustion (damage cooldown)")
    many = [model.CastMark(i, spell, "", "A") for i, spell in
            enumerate([7, 7, 7, 3, 3] + list(range(20, 30)))] + [model.CastMark(0, 3, "", "B")]
    hues = model.spell_hues(many)
    assert hues["A", 7] == model.SPELL_HUES[0] and hues["A", 3] == model.SPELL_HUES[1]
    assert hues["B", 3] == model.SPELL_HUES[0]   # each player's own colours
    assert len([k for k in hues if k[0] == "A"]) == len(model.SPELL_HUES)


def test_enemy_casts_are_read_from_the_details():
    d = {"enemies": ["Tender", "Striker"],
         "enemy_casts": [[9.0, 2, 1], [3.0, 1, 0, 2.0], [5.0, 1, 0, 1.5, "kicked", "Me-R-US"],
                         [6.0, 3, 0, 0, "stopped"], ["bad"], [7.0, 1, 5]],
         "spells": {"1": "Earth Bolt", "2": "Shoot"}}
    marks = model.enemy_casts(d)
    assert marks == [model.EnemyMark(3.0, 1, "Earth Bolt", "Tender", 2.0),
                     model.EnemyMark(5.0, 1, "Earth Bolt", "Tender", 1.5, "kicked", "Me-R-US"),
                     model.EnemyMark(6.0, 3, "Spell 3", "Tender", None, "stopped"),
                     model.EnemyMark(9.0, 2, "Shoot", "Striker")]
    assert model.enemy_casts({}) == [] and model.enemies(d) == ["Tender", "Striker"]
    assert marks[0].end == 5.0 and marks[3].end == 9.0
    assert [model.enemy_text(m) for m in marks[:3]] == [
        "0:03  Tender: Earth Bolt (cast 2.0 s)",
        "0:05  Tender: Earth Bolt (cast 1.5 s, kicked by Me)",
        "0:06  Tender: Spell 3 (didn't finish)"]
    hues = model.enemy_hues(marks)
    assert hues["Tender", 1] == model.SPELL_HUES[0]   # the most cast first, over every mob


def test_enemy_spam_is_dozens_a_minute_and_never_kicked():
    n = model.SPAM_PER_MINUTE + 1
    claws = [model.EnemyMark(i * 0.5, 1, "Claws", "Descendant") for i in range(n)]   # in 20 s
    bolts = [model.EnemyMark(i * 0.5, 2, "Earth Bolt", "Tender") for i in range(n)]
    bolts[3].result = "kicked"
    slow = [model.EnemyMark(i * 2.0, 3, "Shoot", "Striker") for i in range(n * 2)]   # 30 a minute
    assert model.enemy_spam(claws + bolts + slow) == {("Descendant", 1)}
    assert model.enemy_spam(claws[:-1]) == set()   # not more than the limit


def test_a_full_session_shows_its_casts():
    info = model.session_info(Path("2026-10-08_19-00-00.mkv"),
                              {"recording_start": 1.0, "length": 60.0, "runs": [],
                               "casts": [[5.0, 1, 0]], "spells": {"1": "Judgment"},
                               "player": "Me-R-US", "casters": ["Me-R-US"],
                               "enemies": ["Mob"], "enemy_casts": [[6.0, 2, 0]]}, [])
    assert model.casts(info.details) == [model.CastMark(5.0, 1, "Judgment", "Me-R-US")]
    assert model.enemy_casts(info.details) == [model.EnemyMark(6.0, 2, "Spell 2", "Mob")]
    assert info.details["player"] == "Me-R-US"


def test_every_major_cooldown_has_a_kind_with_a_colour():
    from linux_wow_recorder import cooldowns
    assert set(cooldowns.MAJOR.values()) == set(model.KIND_HUES)
    assert cooldowns.kind(31884) == "damage" and cooldowns.kind(642) == "defensive"
    assert cooldowns.kind(98008) == "healing" and cooldowns.kind(53600) is None


def test_a_session_highlight_takes_the_run_it_is_in():
    runs = [{"dungeon": "A", "run_offset": 0.0, "run_start": 100.0, "run_end": 700.0},
            {"dungeon": "B", "run_offset": 900.0, "run_start": 1000.0, "run_end": 1600.0}]
    d = {"runs": runs, "casts": [[1.0, 5]], "spells": {}}
    assert model.session_highlight_base(d, 950, 980, 0)["dungeon"] == "B"
    assert model.session_highlight_base(d, 100, 150, 0)["dungeon"] == "A"
    plain = model.session_highlight_base(d, 800, 850, 5000.0)
    assert plain["dungeon"] == "Highlight" and plain["run_start"] == 5800.0


def test_highlights_are_listed_where_they_were_made():
    def info(details, highlight):
        return model.clip_info(Path("h.mp4"), {"run_start": 1.0, **details,
                                               "highlight": {"start": 1.0, "end": 9.0, **highlight}})
    key = info({"dungeon": "Ara-Kara", "result": "completed"}, {"of": "a.mp4"})
    pull = info({"kind": "raid", "dungeon": "Vexie", "raid": "Undermine", "result": "kill"},
                {"of": "b.mp4"})
    session = info({"dungeon": "Ara-Kara", "result": "completed"}, {"of": "c.mp4", "session": True})
    older = info({"dungeon": "Highlight", "result": ""}, {"of": "2026-10-28_08-02-53.mkv"})
    assert [model.in_mode(c, model.KEYS) for c in (key, pull, session, older)] == [True, False, False, False]
    assert model.in_mode(pull, model.RAIDS) and not model.in_mode(session, model.RAIDS)
    assert model.in_mode(session, model.SESSIONS) and model.in_mode(older, model.SESSIONS)
    assert older.result_text == "Highlight" and key.result_text != "Highlight"
    assert older.status == "highlight" and key.status == "completed"


def test_each_clip_is_listed_in_one_view_only():
    def info(details):
        return model.clip_info(Path("c.mp4"), {"run_start": 1.0, **details})
    key = info({"dungeon": "Ara-Kara", "result": "completed", "label": "+12 Timed"})
    pull = info({"kind": "raid", "dungeon": "Vexie", "raid": "Undermine", "result": "kill"})
    moment = info({"kind": "moment", "dungeon": "Death", "label": "Mulgore", "result": "moment"})
    views = (model.KEYS, model.RAIDS, model.MOMENTS)   # Full sessions loads its own list
    assert [[model.in_mode(c, v) for v in views] for c in (key, pull, moment)] == [
        [True, False, False], [False, True, False], [False, False, True]]
    assert moment.result_text == "Mulgore" and moment.deaths is None


def test_details_made_from_an_older_recording_leave_the_enemies_out():
    # Without the keys, add-casts still takes them for clips without enemies' casts.
    old = {"runs": [], "casts": [[5.0, 1, 0]], "casters": ["Me-R-US"], "spells": {"1": "Judgment"}}
    assert "enemies" not in model.session_highlight_base(old, 0, 10, 0)
    assert "enemy_casts" not in model.session_highlight_base(old, 0, 10, 0)
    clip = SimpleNamespace(details={**old, "clip_length": 30.0}, started=1.0)
    assert "enemies" not in model.session_copy_details(clip)
    clip.details["enemies"] = ["Mob"]
    assert model.session_copy_details(clip)["enemy_casts"] == []
    new = {**old, "enemies": ["Mob"], "enemy_casts": [[6.0, 2, 0]]}
    base = model.session_highlight_base(new, 0, 10, 0)
    assert base["enemies"] == ["Mob"] and base["enemy_casts"] == [[6.0, 2, 0]]
    assert model._enemy_keys({"enemies": None}) == {} and model._enemy_keys({"enemies": []}) == {
        "enemies": [], "enemy_casts": []}


def key_clip(name, level, timer, kills, result="completed", started=1_800_000_000.0, **details):
    """A key's ClipInfo whose bosses died `kills` seconds into the run (it starts 10 s in)."""
    return model.clip_info(Path(name), {
        "dungeon": "Ara-Kara", "level": level, "label": f"+{level}", "result": result,
        "timer": timer, "time_limit": 1800, "run_start": started, "run_offset": 10.0,
        "run_length": "20m00s", "encounters": [
            {"name": boss, "start": 10.0 + at - 60, "end": 10.0 + at, "killed": True}
            for boss, at in kills.items()], **details})


def test_a_key_is_compared_with_the_fastest_at_its_level_or_higher():
    mine = key_clip("mine.mp4", 10, 1500.0, {"Avanoxx": 300, "Ki'katal": 1400})
    fast = key_clip("fast.mp4", 12, 1300.0, {"Avanoxx": 335, "Ki'katal": 1250}, started=1_800_100_000.0)
    others = [
        mine, fast,
        key_clip("slow.mp4", 10, 1700.0, {"Avanoxx": 200}, started=1_800_200_000.0),
        key_clip("low.mp4", 9, 1000.0, {"Avanoxx": 100}, started=1_800_300_000.0),       # a lower key
        key_clip("left.mp4", 12, None, {"Avanoxx": 100}, "abandoned", started=1_800_400_000.0),
        key_clip("copy.mp4", 10, 1500.0, {"Avanoxx": 300}, chat_hidden=True),            # the same run
        key_clip("part.mp4", 12, 900.0, {"Avanoxx": 50}, started=1_800_500_000.0,
                 highlight={"length": "0m30s"}),
    ]
    assert model.best_run(mine, others) is fast
    assert model.best_run(fast, others) is None   # nothing as high
    assert model.best_run(others[4], others) is fast   # an abandoned key is compared too
    assert model.best_run(others[6], others) is None   # a highlight's times aren't the run's
    assert model.best_text(fast).startswith("best +12 in 21m40s (")
    rows = {r.text: r.detail for r in model.event_rows(mine.details, fast.details)}
    assert rows["Avanoxx"] == "killed in 1:00 · at 5:00, 0:35 faster than best"
    assert rows["Ki'katal"] == "killed in 1:00 · at 23:20, 2:30 slower than best"
    assert model.event_rows(mine.details)[1].detail == "killed in 1:00"
    assert model.split_text(0.4) == "level with best"


def test_a_death_says_what_killed_and_lists_its_last_hits():
    death = {"name": "Tank-Realm-US", "at": 90.0, "hits": [
        {"at": -2.0, "by": "", "spell": "Falling", "amount": 900},
        {"at": -0.1, "by": "Venom Leech", "spell": "Septic Spatter", "amount": 531886, "killing": True},
        {"at": 0.0, "by": "Venom Leech", "spell": "Melee", "amount": 1_250_000}]}
    assert model.killed_by(death) == "Septic Spatter (Venom Leech), 532k"
    assert model.killed_by({"name": "X", "at": 1.0}) == ""
    assert model.killed_by({"hits": [{"spell": "Lava", "amount": 40}]}) == "Lava, 40"   # the last
    details = {"encounters": [{"name": "Boss", "start": 60.0, "end": 120.0, "killed": True}],
               "deaths": [death, {"name": "Healer-Realm-US", "at": 200.0}]}
    pull, loose = model.event_rows(details)
    row = pull.children[0]
    assert (row.text, row.detail, pull.detail) == (
        "Tank died", "Septic Spatter (Venom Leech), 532k", "killed in 1:00 · 1 death")
    assert [(h.at, h.kind, h.text, h.detail) for h in row.children] == [
        (88.0, "hit", "Falling", "900"), (89.9, "hit", "Septic Spatter (Venom Leech)", "532k"),
        (90.0, "hit", "Melee (Venom Leech)", "1.2M")]
    assert (loose.text, loose.detail, loose.children) == ("Healer died", "", [])
    texts = [m.text for m in model.markers(details) if m.kind == "death"]
    assert texts == ["Tank died: Septic Spatter (Venom Leech), 532k", "Healer died"]
    assert model.seek_target(90.0, "death") == 84.0 and model.seek_target(90.0) == 87.0


KICKS = {
    "casters": ["Me-Realm-US", "Tank-Realm-US", "Idle-Realm-US"],
    "enemies": ["Tender", "Boss"],
    "spells": {"1": "Earth Bolt", "2": "Slam", "3": "Heal", "7": "Paralyzing Shots"},
    "enemy_casts": [[10.0, 1, 0, 1.5, "kicked", "Me-Realm-US"], [20.0, 1, 0, 2.0],
                    [30.0, 1, 0, 1.0, "kicked", "Tank-Realm-US"], [40.0, 1, 0, 2.0],
                    [50.0, 2, 1, 3.0], [60.0, 3, 0, 0, "stopped"],
                    [70.0, 3, 0, 1.0, "kicked", "Me-Realm-US"], [80.0, 1, 0, 0.5, "kicked", "Felhunter"]],
    "dispels": [[15.0, "Me-Realm-US", 7, "Tank-Realm-US"], [25.0, "Me-Realm-US", 7, "Me-Realm-US"],
                ["bad"]],
}


def test_the_kicks_list():
    assert model.has_kicks(KICKS) and not model.has_kicks({"enemy_casts": [[1.0, 1, 0]]})
    kicks, through, dispels = model.kick_rows(KICKS)
    assert (kicks.text, kicks.detail, kicks.at) == ("Kicks", "4", None)
    assert [(r.text, r.detail) for r in kicks.children] == [
        ("Me", "2 kicks"), ("Tank", "1 kick"), ("Felhunter", "1 kick"), ("Idle", "0 kicks")]
    assert [(k.at, k.kind, k.text) for k in kicks.children[0].children] == [
        (10.0, "kick", "Earth Bolt (Tender)"), (70.0, "kick", "Heal (Tender)")]
    # Only what was kicked at least once can be: Slam isn't listed, nor the Heal that was stopped
    assert [(r.text, r.detail) for r in through.children] == [
        ("Earth Bolt (Tender)", "2 went off, 3 kicked")]
    assert [(c.at, c.text, c.detail) for c in through.children[0].children] == [
        (20.0, "Went off", "cast 2.0 s"), (40.0, "Went off", "cast 2.0 s")]
    assert (dispels.text, dispels.detail) == ("Dispels", "2")
    assert [(r.text, r.detail) for r in dispels.children] == [("Me", "2 dispels")]
    assert [(d.at, d.text, d.detail) for d in dispels.children[0].children] == [
        (15.0, "Paralyzing Shots", "off Tank"), (25.0, "Paralyzing Shots", "off Me")]
    assert [r.text for r in model.kick_rows({**KICKS, "enemy_casts": []})] == ["Dispels"]
    assert model.kick_rows({}) == []


def test_enemies_casting_together_are_one_box_and_the_next_ones_are_listed():
    details = {"enemies": ["Imp", "Boss"], "spells": {"1": "Felfire", "2": "Slam"},
               "enemy_casts": [[10.0, 1, 0, 1.0], [10.4, 1, 0, 2.0], [10.9, 1, 0, 1.0],
                               [11.5, 1, 0, 1.0],                       # over a second after the first
                               [11.6, 1, 0, 0.5, "kicked", "Me-R-US"],  # a kicked one stays its own
                               [11.7, 1, 0, 1.0], [12.0, 2, 1], [30.0, 2, 1, 3.0]]}
    casts = model.enemy_casts(details)
    grouped = model.group_enemy_casts(casts)
    assert [(c.at, c.count, c.cast, c.label) for c in grouped] == [
        (10.0, 3, 2.0, "Felfire ×3"), (11.5, 1, 1.0, "Felfire"), (11.6, 1, 0.5, "✕ Felfire"),
        (11.7, 1, 1.0, "Felfire"), (12.0, 1, None, "Slam"), (30.0, 1, 3.0, "Slam")]
    assert all(c.count == 1 for c in casts)   # the clip's own are left as they were
    assert "3 at once" in model.enemy_text(grouped[0])
    # What ends in the next 10 s, the soonest first: under way or still to begin
    soon = model.upcoming(grouped, 11.0)
    assert [(round(left, 1), c.label) for left, c in soon] == [
        (1.0, "Felfire ×3"), (1.0, "Slam"), (1.1, "✕ Felfire"), (1.5, "Felfire"), (1.7, "Felfire")]
    assert model.upcoming(grouped, 22.9) == []
    assert [c.label for _, c in model.upcoming(grouped, 23.5)] == ["Slam"]
    many = [model.EnemyMark(float(i), 1, "Bolt", "Imp") for i in range(20)]
    assert len(model.upcoming(many, 0.0)) == model.UPCOMING_ROWS


def test_a_stopped_cast_of_the_groups():
    details = {"casters": ["Me-Realm-US"], "spells": {"1": "Fireball"},
               "casts": [[5.0, 1, 0, 0, "stopped"], [9.0, 1, 0, 2.5]]}
    stopped, done = model.casts(details)
    assert (stopped.result, stopped.cast, done.result, done.cast) == ("stopped", None, "", 2.5)
    assert model.cast_text(stopped) == "0:05  Me: Fireball (didn't finish)"
