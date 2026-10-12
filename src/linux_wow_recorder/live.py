"""The newest frame of the recording in progress, for the clips window's live preview.

A Matroska file that's still being written has no index, so ffmpeg can't seek to its end
(-sseof always gives the first frame). Instead ffmpeg is fed the file's header followed by its
second-to-last cluster onward. ffmpeg's Matroska muxer starts every cluster with a keyframe, and
that cluster is complete; the last one may still be half written. The frame is a few seconds
behind: gpu-screen-recorder writes in 512 KiB chunks and a cluster is about 2 s long.
"""

from pathlib import Path

CLUSTER = b"\x1f\x43\xb6\x75"   # the Matroska Cluster element's ID
HEADER_MAX = 1 << 20            # the header (EBML, tracks) ends well inside the first MiB
STEP = 4 << 20
TAIL_MAX = 64 << 20


def preview_input(path: Path) -> bytes | None:
    """The header plus the last whole cluster onward, or None while the file has no cluster yet."""
    try:
        with open(path, "rb") as f:
            head = f.read(HEADER_MAX)
            first = head.find(CLUSTER)
            if first < 0:
                return None
            size = f.seek(0, 2)
            want = STEP
            while True:
                start = max(first, size - want)
                f.seek(start)
                tail = f.read(size - start)
                last = tail.rfind(CLUSTER)
                prev = tail.rfind(CLUSTER, 0, last) if last > 0 else -1
                if prev >= 0:
                    return head[:first] + tail[prev:]
                if start == first or want >= TAIL_MAX:
                    return head[:first] + tail   # only one cluster so far: its first frame
                want *= 2
    except OSError:
        return None


def ffmpeg_command(width: int = 640) -> list[str]:
    """ffmpeg reading preview_input() on stdin and writing one JPEG to stdout."""
    return ["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-vf", f"scale={width}:-2",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
