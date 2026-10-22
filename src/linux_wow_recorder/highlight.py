"""A highlight: a stretch of a clip saved as a clip of its own, made when the user asks. The
video is stream-copied like the clips themselves, so it takes seconds and loses nothing; it
starts on the keyframe at or before the chosen start (a second or two early at most)."""

from pathlib import Path

from .clips import cut_command

MIN_SECONDS = 2.0   # shorter isn't worth a clip


def length_text(seconds: float) -> str:
    """"0m45s", the way clips' lengths are written."""
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m{s:02d}s"


def output_path(clip: Path, clips_dir: Path, start: float, end: float) -> Path:
    """Where the highlight goes: the clips folder, also for a clip in another folder (the app
    writes only in its own). The times keep two highlights of one clip apart."""
    return clips_dir / f"{clip.stem} highlight {length_text(start)}-{length_text(end)}.mp4"


def command(clip: Path, start: float, end: float, part: Path) -> list[str]:
    """ffmpeg copying start..end of clip into part. Highlights are short, so the index goes at
    the front with faststart (a second pass over a few MB)."""
    return cut_command(clip, start, end - start, part, faststart=True)


def details(clip_details: dict, source: Path, start: float, end: float) -> dict:
    """The highlight's details: the clip's run, with only the bosses, deaths and lusts inside
    start..end, their times counted from the highlight's start. A boss pull cut off at either end
    is kept, clamped to the highlight (still going at its end: no end, as in a clip)."""
    length = end - start

    def at(t: float | None) -> float | None:
        return round(t - start, 1) if t is not None and start <= t <= end else None

    encounters = []
    for e in clip_details.get("encounters") or []:
        e_end = e.get("end")
        if e["start"] > end or (e_end is not None and e_end < start):
            continue
        encounters.append({**e, "start": round(max(0.0, e["start"] - start), 1),
                           "end": at(e_end)})
    deaths = [{**d, "at": at(d["at"])} for d in clip_details.get("deaths") or []
              if at(d["at"]) is not None]
    lusts = [{**l, "at": at(l["at"])} for l in clip_details.get("lusts") or []
             if at(l["at"]) is not None]
    copy = {k: v for k, v in clip_details.items() if k not in ("thumbnail", "run_offset")}
    copy.update({
        "clip_start": (clip_details.get("clip_start") or 0) + start,
        "clip_length": round(length, 1),
        "encounters": encounters,
        "deaths": deaths,
        "lusts": lusts,
        "deaths_total": len(deaths),
        "bosses_killed": sum(1 for e in encounters if e.get("killed") and e.get("end") is not None),
        "keep": False,
        "highlight": {"of": source.name, "start": round(start, 1), "end": round(end, 1),
                      "length": length_text(length)},
    })
    offset = at(clip_details.get("run_offset"))
    if offset is not None:
        copy["run_offset"] = offset   # the run starts inside the highlight
    return copy
