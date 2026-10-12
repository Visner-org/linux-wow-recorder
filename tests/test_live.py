import re
import shutil
import subprocess

import pytest

from linux_wow_recorder import live
from linux_wow_recorder.live import CLUSTER


def test_preview_input_takes_header_and_second_to_last_cluster(tmp_path):
    f = tmp_path / "rec.mkv"
    f.write_bytes(b"HEADER" + CLUSTER + b"one" + CLUSTER + b"two" + CLUSTER + b"thr")
    assert live.preview_input(f) == b"HEADER" + CLUSTER + b"two" + CLUSTER + b"thr"
    f.write_bytes(b"HEADER" + CLUSTER + b"only")   # one cluster so far: all of it
    assert live.preview_input(f) == b"HEADER" + CLUSTER + b"only"
    f.write_bytes(b"HEADER, nothing written yet")
    assert live.preview_input(f) is None
    assert live.preview_input(tmp_path / "missing.mkv") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_preview_frame_is_near_the_end_of_an_unfinished_file(tmp_path):
    # 20 s with a keyframe every second, cut off mid-write like a recording in progress
    full, cut = tmp_path / "full.mkv", tmp_path / "rec.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=20",
                    "-c:v", "libx264", "-g", "30", "-preset", "ultrafast", str(full)], check=True)
    data = full.read_bytes()
    cut.write_bytes(data[:len(data) * 9 // 10])
    out = subprocess.run(["ffmpeg", "-v", "info", "-copyts", "-i", "pipe:0", "-frames:v", "1",
                          "-vf", "showinfo", "-f", "null", "-"],
                         input=live.preview_input(cut), capture_output=True)
    pts = float(re.search(rb"pts_time:([\d.]+)", out.stderr).group(1))
    assert pts > 14
    jpeg = subprocess.run(live.ffmpeg_command(160), input=live.preview_input(cut), capture_output=True).stdout
    assert jpeg[:2] == b"\xff\xd8"
