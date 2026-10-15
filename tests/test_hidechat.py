import os
import shutil
import subprocess

import pytest

from linux_wow_recorder import hidechat


def test_boxes_stay_inside_the_picture():
    assert hidechat.clamp((0.9, -0.2, 0.5, 0.3)) == (0.9, 0.0, 0.1, 0.3)
    assert hidechat.clamp((0.5, 0.5, 0.0, 0.0)) == (0.5, 0.5, 0.01, 0.01)
    assert hidechat.parse_box(hidechat.box_text((0.01, 0.6, 0.3, 0.3))) == (0.01, 0.6, 0.3, 0.3)
    for saved in (None, "", "1,2", "a,b,c,d"):
        assert hidechat.parse_box(saved) == hidechat.DEFAULT_BOX
    assert hidechat.drawbox((0, 0.6, 0.3, 0.3)) == \
        "drawbox=x=iw*0.0:y=ih*0.6:w=iw*0.3:h=ih*0.3:color=black:t=fill"


def test_the_copy_goes_beside_the_clip_or_in_clips_when_it_cant(tmp_path):
    clip = tmp_path / "a" / "Run +10 Completed (20m00s).mp4"
    clip.parent.mkdir()
    clips = tmp_path / "clips"
    assert hidechat.output_path(clip, clips) == clip.parent / "Run +10 Completed (20m00s) (chat hidden).mp4"
    os.chmod(clip.parent, 0o555)
    try:
        if not os.access(clip.parent, os.W_OK):   # root can write anyway
            assert hidechat.output_path(clip, clips).parent == clips
    finally:
        os.chmod(clip.parent, 0o755)


def test_encoders_are_tried_gpu_first(tmp_path, monkeypatch):
    monkeypatch.setattr(hidechat, "render_nodes", lambda: ["/dev/dri/renderD128"])
    tries = hidechat.commands(tmp_path / "a.mp4", tmp_path / "b.part", hidechat.DEFAULT_BOX, 10_000_000)
    encoders = [t[t.index("-c:v") + 1] for t in tries]
    assert encoders == ["h264_nvenc", "h264_vaapi", "libx264", "libopenh264"]
    assert all(t[-1] == str(tmp_path / "b.part") and "-progress" in t for t in tries)


def test_progress_and_copied_details():
    assert hidechat.progress_seconds("frame=1\nout_time_us=1500000\nout_time_us=N/A\nout_time_us=2500000\n") == 2.5
    assert hidechat.progress_seconds("progress=continue\n") is None
    copy = hidechat.copied_details({"dungeon": "Ara-Kara", "keep": True, "thumbnail": "a.jpg"})
    assert copy == {"dungeon": "Ara-Kara", "keep": False, "chat_hidden": True}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_box_is_black_in_the_copy(tmp_path):
    clip = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=white:size=320x180:rate=10",
                    "-t", "2", "-pix_fmt", "yuv420p", str(clip)], check=True)
    part = tmp_path / "b.mp4.part"
    for cmd in hidechat.commands(clip, part, (0.0, 0.5, 0.5, 0.5), 500_000):
        if subprocess.run(cmd, capture_output=True).returncode == 0:
            break
    else:
        pytest.fail("no encoder worked")
    pixels = subprocess.run(["ffmpeg", "-v", "error", "-i", str(part), "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True, check=True).stdout
    assert pixels[170 * 320 + 10] < 40      # bottom left: black
    assert pixels[10 * 320 + 300] > 200     # top right: white
