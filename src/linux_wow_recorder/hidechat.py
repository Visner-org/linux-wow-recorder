"""A copy of a clip with a black box over the chat (or anything else), made when the user asks.

Drawing on the picture means encoding the video again, unlike cutting (a copy): on the GPU
when there's an encoder for it, about 7x faster than the clip plays (NVENC, 1440p60).
"""

import os
import subprocess
from pathlib import Path

# A box as fractions of the picture (x, y, width, height), so it fits any resolution.
Box = tuple[float, float, float, float]
DEFAULT_BOX: Box = (0.0, 0.7, 0.25, 0.3)   # WoW's chat frame: bottom left
SUFFIX = " (chat hidden)"
FALLBACK_BITRATE = 20_000_000


def output_path(clip: Path, clips_dir: Path) -> Path:
    """Where the copy goes: beside the clip, or in clips_dir when the clip's folder is read
    only (another folder)."""
    folder = clip.parent if os.access(clip.parent, os.W_OK) else clips_dir
    return folder / f"{clip.stem}{SUFFIX}.mp4"


def clamp(box: Box) -> Box:
    """box kept inside the picture and at least 1% each way."""
    x, y, w, h = (min(1.0, max(0.0, v)) for v in box)
    w, h = max(0.01, min(w, 1 - x)), max(0.01, min(h, 1 - y))
    return round(min(x, 0.99), 4), round(min(y, 0.99), 4), round(w, 4), round(h, 4)


def parse_box(text) -> Box:
    """A box saved as "x,y,w,h"; DEFAULT_BOX if it isn't one."""
    try:
        values = [float(v) for v in str(text).split(",")]
    except ValueError:
        return DEFAULT_BOX
    return clamp(tuple(values)) if len(values) == 4 else DEFAULT_BOX


def box_text(box: Box) -> str:
    return ",".join(f"{v:g}" for v in clamp(box))


def drawbox(box: Box) -> str:
    x, y, w, h = clamp(box)
    return f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=black:t=fill"


def video_bitrate(clip: Path) -> int:
    """The clip's video bitrate, so the copy is about the same size; FALLBACK_BITRATE if
    ffprobe can't say."""
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=bit_rate:format=bit_rate",
                                 "-of", "csv=p=0:nk=1", str(clip)],
                                capture_output=True, text=True, timeout=60)
        rates = [int(v) for v in result.stdout.split() if v.isdigit()]
    except (OSError, subprocess.TimeoutExpired):
        rates = []
    return rates[0] if rates else FALLBACK_BITRATE


def render_nodes() -> list[str]:
    return sorted(str(p) for p in Path("/dev/dri").glob("renderD*"))


def commands(clip: Path, part: Path, box: Box, bitrate: int) -> list[list[str]]:
    """ffmpeg commands to try in order until one works: NVIDIA, then VA-API on each GPU (AMD,
    Intel), then the CPU. -progress prints how far it got, for a progress bar."""
    rate = ["-b:v", str(bitrate), "-maxrate", str(int(bitrate * 1.5)),
            "-bufsize", str(bitrate * 2)]
    start = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-progress", "pipe:1", "-nostats"]
    end = ["-map", "0:v:0", "-map", "0:a?", "-c:a", "copy", "-movflags", "+faststart",
           "-f", "mp4", str(part)]
    tries = [[*start, "-i", str(clip), "-vf", drawbox(box), "-c:v", "h264_nvenc",
              "-preset", "p1", *rate, *end]]
    for node in render_nodes():
        tries.append([*start, "-vaapi_device", node, "-i", str(clip),
                      "-vf", f"{drawbox(box)},format=nv12,hwupload", "-c:v", "h264_vaapi",
                      *rate, *end])
    for encoder in ("libx264", "libopenh264"):
        tries.append([*start, "-i", str(clip), "-vf", drawbox(box), "-c:v", encoder,
                      *(["-preset", "veryfast"] if encoder == "libx264" else []), *rate, *end])
    return tries


def frame_command(clip: Path, at: float, out: Path) -> list[str]:
    """ffmpeg grabbing the frame at `at` s, for drawing the box on."""
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.1f}",
            "-i", str(clip), "-frames:v", "1", "-vf", "scale='min(1600,iw)':-2", "-q:v", "3",
            "-f", "mjpeg", str(out)]


def progress_seconds(text: str) -> float | None:
    """The last "out_time_us=" in ffmpeg's -progress output, in seconds."""
    seconds = None
    for line in text.splitlines():
        if line.startswith("out_time_us="):
            try:
                seconds = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                pass
    return seconds


def copied_details(details: dict) -> dict:
    """The copy's details: the clip's, marked chat hidden, not kept, and with no picture (one
    is made from the copy)."""
    copy = {k: v for k, v in details.items() if k != "thumbnail"}
    copy["keep"] = False
    copy["chat_hidden"] = True
    return copy
