import shutil
import subprocess
from pathlib import Path

import pytest

from linux_wow_recorder import highlight
from linux_wow_recorder.clips import media_length

DETAILS = {
    "dungeon": "Ara-Kara, City of Echoes", "label": "+10 Completed", "run_length": "24m00s",
    "clip_start": 1000.0, "clip_length": 1500.0, "run_offset": 30.0, "thumbnail": "a.jpg",
    "keep": True, "deaths_total": 3, "bosses_killed": 3,
    "encounters": [{"name": "Avanoxx", "start": 100.0, "end": 200.0, "killed": True},
                   {"name": "Anub'zekt", "start": 400.0, "end": 520.0, "killed": False},
                   {"name": "Ki'katal", "start": 900.0, "end": 1000.0, "killed": True}],
    "deaths": [{"name": "A", "at": 150.0}, {"name": "B", "at": 450.0}, {"name": "C", "at": 950.0}],
    "lusts": [{"name": "Bloodlust", "caster": "S", "at": 101.0}],
    "player": "Me-Realm-US", "casters": ["Me-Realm-US"],
    "casts": [[100.0, 1, 0], [130.0, 2, 0, 1.5], [500.0, 1, 0]],
    "spells": {"1": "Judgment", "2": "Flash of Light"},
}


def test_a_highlight_keeps_only_what_happens_inside_it():
    d = highlight.details(DETAILS, Path("a.mp4"), 120.0, 460.0)
    assert d["clip_start"] == 1120.0 and d["clip_length"] == 340.0
    # Avanoxx was pulled before the highlight starts: clamped to its start. Anub'zekt is still
    # going at its end: no end, like a pull still going when a clip ends.
    assert d["encounters"] == [{"name": "Avanoxx", "start": 0.0, "end": 80.0, "killed": True},
                               {"name": "Anub'zekt", "start": 280.0, "end": None, "killed": False}]
    assert d["deaths"] == [{"name": "A", "at": 30.0}, {"name": "B", "at": 330.0}]
    assert d["lusts"] == [] and d["deaths_total"] == 2 and d["bosses_killed"] == 1
    assert "run_offset" not in d and "thumbnail" not in d and d["keep"] is False
    assert d["highlight"] == {"of": "a.mp4", "start": 120.0, "end": 460.0, "length": "5m40s"}
    assert d["label"] == "+10 Completed" and d["run_length"] == "24m00s"   # still the run's
    assert d["casts"] == [[10.0, 2, 0, 1.5]] and d["spells"] == {"2": "Flash of Light"}
    assert d["casters"] == ["Me-Realm-US"]


def test_the_run_start_stays_when_its_inside():
    assert highlight.details(DETAILS, Path("a.mp4"), 20.0, 60.0)["run_offset"] == 10.0


def test_highlights_go_in_the_clips_folder(tmp_path):
    clip = tmp_path / "elsewhere" / "2026-10-21 21-07 Ruby Life Pools +13 Timed (26m45s).mp4"
    clips = tmp_path / "clips"
    assert highlight.output_path(clip, clips, 723.4, 768.9) == \
        clips / "2026-10-21 21-07 Ruby Life Pools +13 Timed (26m45s) highlight 12m03s-12m49s.mp4"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_command_copies_the_stretch(tmp_path):
    clip, part = tmp_path / "a.mp4", tmp_path / "b.mp4.part"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                    "-t", "10", "-g", "10", "-pix_fmt", "yuv420p", "-f", "mp4", str(clip)], check=True)
    subprocess.run(highlight.command(clip, 3.0, 7.0, part), check=True)
    assert 3.5 <= media_length(part) <= 5.5   # from the keyframe at or before 3 s
