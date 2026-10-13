import datetime
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from linux_wow_recorder import config
from linux_wow_recorder.clips import (cleanup, clip_name, process_pending, read_sidecar, set_keep,
                                      stale_recordings)
from linux_wow_recorder.combatlog import Run


def make_cfg(tmp_path, **storage):
    data = config.load_raw(tmp_path / "missing.toml")
    (tmp_path / "logs").mkdir()
    (tmp_path / "out").mkdir()
    data["paths"] = {"combat_log_dir": str(tmp_path / "logs"), "output_dir": str(tmp_path / "out")}
    data["storage"].update(storage)
    return config.from_dict(data)


def stamp(t: float) -> str:
    """A combat log timestamp in local time, like WoW writes without an offset."""
    dt = datetime.datetime.fromtimestamp(t)
    return f"{dt.month}/{dt.day}/{dt.year} {dt:%H:%M:%S}.000"


def test_clip_name_marks_partial_runs():
    t = datetime.datetime(2026, 10, 8, 19, 3).timestamp()
    run = Run("Kings' Rest", t, t + 1492, "+12 Completed", 1492.0)
    assert clip_name(run, t - 60, t + 3000) == "2026-10-08 19-03 Kings' Rest +12 Completed (24m52s).mp4"
    assert clip_name(run, t + 600, t + 3000).endswith("(24m52s) (part from 19-13-00).mp4")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_cuts_a_session_into_clips(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.sessions_dir.mkdir()
    rec = cfg.sessions_dir / "2026-10-08_19-00-00.mkv"
    # A 5-minute recording, tiny and fast to make, big enough not to count as a failed start.
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                    "-f", "lavfi", "-i", "sine", "-t", "300", "-c:v", "libx264", "-preset",
                    "ultrafast", "-g", "10", "-b:v", "500k", "-c:a", "aac", str(rec)], check=True)
    end = 1_800_000_000.0
    os.utime(rec, (end, end))
    start = end - 300

    # A +7 from 1 to 3 minutes into the recording.
    log = cfg.combat_log_dir / f"WoWCombatLog-{datetime.datetime.fromtimestamp(start - 600):%m%d%y_%H%M%S}.txt"
    log.write_text(
        f'{stamp(start + 50)}  ZONE_CHANGE,2290,"Mists of Tirna Scithe",8\n'
        f'{stamp(start + 60)}  CHALLENGE_MODE_START,"Mists of Tirna Scithe",2290,375,7,[10]\n'
        f'{stamp(start + 90)}  ENCOUNTER_START,2397,"Ingra Maloch",8,5,2290\n'
        f'{stamp(start + 100)}  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,Player-1-A,"Tank-Realm-US",0x511,0x0,0\n'
        f'{stamp(start + 150)}  ENCOUNTER_END,2397,"Ingra Maloch",8,5,1,60000\n'
        f'{stamp(start + 180)}  CHALLENGE_MODE_END,2290,1,7,115000\n'
    )
    os.utime(log, (end, end))

    [result] = process_pending(cfg)
    assert result.problem is None
    [(run, clip)] = result.clips
    assert run.label == "+7 Completed"
    assert clip.name.endswith("Mists of Tirna Scithe +7 Completed (1m55s).mp4")
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True).stdout)
    assert 140 <= duration <= 150   # 120 s run, 10 s before, 15 s after, keyframe slack
    details = read_sidecar(clip)
    assert details["dungeon"] == "Mists of Tirna Scithe" and details["level"] == 7
    assert details["result"] == "completed" and details["keep"] is False
    assert details["run_offset"] == 10   # the clip starts pad_before seconds before the run
    [boss] = details["encounters"]
    assert (boss["name"], boss["start"], boss["end"], boss["killed"]) == ("Ingra Maloch", 40, 100, True)
    assert details["deaths"] == [{"name": "Tank-Realm-US", "at": 50}]
    # A picture for the clip browser, 20 s into the first pull.
    assert details["thumbnail"] == clip.with_suffix(".jpg").name
    assert clip.with_suffix(".jpg").read_bytes()[:2] == b"\xff\xd8"   # a JPEG
    assert not rec.exists() and (cfg.processed_dir / rec.name).exists()
    # The full recording's own details, for the clip browser's Full sessions.
    session = read_sidecar(cfg.processed_dir / rec.name)
    assert abs(session["recording_start"] - start) < 2 and 299 <= session["length"] <= 301
    [whole] = session["runs"]
    assert whole["dungeon"] == "Mists of Tirna Scithe" and abs(whole["run_offset"] - 60) < 2
    assert abs(whole["encounters"][0]["start"] - 90) < 2


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="needs ffprobe")
def test_no_combat_log(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.sessions_dir.mkdir()
    rec = cfg.sessions_dir / "2026-10-08_19-00-00.mkv"
    rec.write_bytes(b"\0" * 2_000_000)   # unreadable, so its start comes from the name
    [result] = process_pending(cfg)
    assert result.problem == "no_combat_log"
    assert (cfg.processed_dir / rec.name).exists()
    assert read_sidecar(cfg.processed_dir / rec.name)["runs"] == []


def test_cleanup_keeps_clips_by_default(tmp_path):
    cfg = make_cfg(tmp_path, session_retention_days=3, clip_retention_days=0)
    old = 1_000_000_000
    for folder in (cfg.processed_dir, cfg.clips_dir):
        folder.mkdir()
        f = folder / "old.mp4"
        f.write_bytes(b"x")
        os.utime(f, (old, old))
    (cfg.output_dir / "users-own-file.txt").write_text("x")
    os.utime(cfg.output_dir / "users-own-file.txt", (old, old))

    assert cleanup(cfg) == 1
    assert not (cfg.processed_dir / "old.mp4").exists()
    assert (cfg.clips_dir / "old.mp4").exists()
    assert (cfg.output_dir / "users-own-file.txt").exists()


def test_cleanup_skips_kept_clips_and_takes_sidecars(tmp_path):
    cfg = make_cfg(tmp_path, clip_retention_days=7)
    cfg.clips_dir.mkdir()
    old = 1_000_000_000
    kept, expired = cfg.clips_dir / "kept.mp4", cfg.clips_dir / "expired.mp4"
    for clip in (kept, expired):
        clip.write_bytes(b"x")
        set_keep(clip, clip is kept)
        for f in (clip, clip.with_suffix(".json")):
            os.utime(f, (old, old))

    for clip in (kept, expired):
        clip.with_suffix(".jpg").write_bytes(b"x")
        os.utime(clip.with_suffix(".jpg"), (old, old))
    assert cleanup(cfg) == 1
    assert kept.exists() and kept.with_suffix(".json").exists() and kept.with_suffix(".jpg").exists()
    assert not expired.exists() and not expired.with_suffix(".json").exists()
    assert not expired.with_suffix(".jpg").exists()


def test_cleanup_never_deletes_recordings_not_cut_yet(tmp_path):
    cfg = make_cfg(tmp_path, session_retention_days=3)
    cfg.sessions_dir.mkdir()
    old, new = cfg.sessions_dir / "old.mkv", cfg.sessions_dir / "new.mkv"
    for f in (old, new):
        f.write_bytes(b"x")
    os.utime(old, (1_000_000_000, 1_000_000_000))

    assert cleanup(cfg) == 0
    assert old.exists() and new.exists()
    assert stale_recordings(cfg) == [old]
    (tmp_path / "forever").mkdir()
    assert stale_recordings(make_cfg(tmp_path / "forever", session_retention_days=0)) == []


def test_thumbnail_time():
    from linux_wow_recorder.clips import thumbnail_time
    assert thumbnail_time({"encounters": [{"start": 40.0}], "clip_length": 500}) == 60.0
    assert thumbnail_time({"run_offset": 10.0, "clip_length": 500}) == 70.0
    assert thumbnail_time({"run_offset": 10.0, "clip_length": 30}) == 29.0   # within the clip


def test_a_cut_that_comes_out_short_fails_and_is_retried(tmp_path, monkeypatch, caplog):
    """ffmpeg takes a disk read error for the end of the recording and still exits 0."""
    from linux_wow_recorder import clips
    cfg = make_cfg(tmp_path)
    cfg.sessions_dir.mkdir()
    rec = cfg.sessions_dir / "2026-10-12_14-07-45.mkv"
    rec.write_bytes(b"\0" * 2_000_000)
    end = 1_800_000_000.0
    os.utime(rec, (end, end))
    start = end - 2557
    monkeypatch.setattr(clips, "recording_span", lambda path: (start, end))
    run = Run("Temple of Sethraliss", start + 415, start + 2081, "+10 Completed", 1666.7,
              level=10, result="completed")
    monkeypatch.setattr(clips, "combat_logs_between", lambda *a: [tmp_path / "log.txt"])
    monkeypatch.setattr(clips, "read_log_events", lambda logs: [])
    monkeypatch.setattr(clips, "find_runs", lambda events, minutes: [run])

    def ffmpeg(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"short")   # the .part file
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(clips.subprocess, "run", ffmpeg)
    monkeypatch.setattr(clips, "media_length", lambda path: 1133.1)   # of 1690 asked for

    [result] = process_pending(cfg)
    assert result.problem == "clip_failed" and result.clips == []
    assert list(cfg.clips_dir.iterdir()) == []         # no short clip, no .part left
    assert rec.exists()                                # stays in sessions/ for next time
    assert "came out short (1133 s of 1691 s)" in caplog.text


def test_cleanup_takes_a_full_recordings_details_and_picture_and_skips_kept_ones(tmp_path):
    cfg = make_cfg(tmp_path, session_retention_days=3)
    cfg.processed_dir.mkdir()
    old = 1_000_000_000
    kept, expired = cfg.processed_dir / "kept.mkv", cfg.processed_dir / "expired.mkv"
    for rec in (kept, expired):
        rec.write_bytes(b"x")
        set_keep(rec, rec is kept)
        rec.with_suffix(".jpg").write_bytes(b"x")
        for f in (rec, rec.with_suffix(".json"), rec.with_suffix(".jpg")):
            os.utime(f, (old, old))
    assert cleanup(cfg) == 1
    assert kept.exists() and kept.with_suffix(".json").exists() and kept.with_suffix(".jpg").exists()
    assert not any(f.exists() for f in (expired, expired.with_suffix(".json"), expired.with_suffix(".jpg")))
