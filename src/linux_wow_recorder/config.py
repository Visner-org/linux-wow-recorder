"""The TOML config file: defaults, loading, checking and writing it."""

import json
import os
import shlex
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / APP_NAME

# Most webhooks the config takes (Discord and Fluxer both work).
MAX_WEBHOOKS = 25

# Webhook message events: key -> (on by default, description shown in setup and in the config file).
NOTIFY_EVENTS = {
    "session_started":  (False, "WoW started and recording began"),
    "session_ended":    (False, "WoW closed and the recording was saved"),
    "clips_ready":      (True,  "Clips are ready after a session"),
    "no_runs":          (False, "A session ended with no dungeon runs or raid pulls in it"),
    "no_combat_log":    (True,  "No combat log covers a session (combat logging off?)"),
    "recording_failed": (True,  "The recorder didn't start, or stopped mid-session"),
    "low_disk_space":   (True,  "Free space in the output folder is low"),
    "clip_failed":      (True,  "A clip couldn't be cut"),
    "hook_failed":      (True,  "The after_clips command failed"),
    "cleanup":          (False, "Old recordings were deleted"),
}

# [clips] raids: which raid boss pulls get a clip.
RAID_CHOICES = {"all": "Every pull, kills and wipes", "kills": "Kills only", "off": "None"}

# [clips_window]: what the clips window shows, each on by default. key -> what Settings calls it.
CLIPS_WINDOW = {
    "raids": "Raids view",
    "full_sessions": "Full sessions view",
    "hide_chat": "Hide chat button",
    "youtube": "Upload to YouTube button",
}

# An audio source meaning WoW's own sound. The recorder turns it into gpu-screen-recorder's
# "app:Wow.exe|app:World of Warcraft" for the WoW it found (recorder.audio_source).
WOW_SOUND = "wow"
# What game sound to record, as the settings offer it.
GAME_SOUND = {
    "wow": "Only WoW's sound",
    "desktop": "All desktop sound (WoW, Discord, music...)",
    "none": "No sound",
}


def game_sound(audio: list[str]) -> str:
    """Which GAME_SOUND choice an audio list matches."""
    sources = [s.strip() for track in audio for s in track.split("|")]
    if WOW_SOUND in sources:
        return "wow"
    return "desktop" if "default_output" in sources else "none"


def audio_tracks(game: str, mic: bool) -> list[str]:
    """The audio list for a GAME_SOUND choice, with the microphone as its own track."""
    tracks = {"wow": [WOW_SOUND], "desktop": ["default_output"]}.get(game, [])
    return tracks + ["default_input"] if mic else tracks


DEFAULTS = {
    "paths": {"combat_log_dir": "", "output_dir": ""},
    "recorder": {
        "command": "gpu-screen-recorder",
        "capture": "portal",
        "monitor": "",
        "fps": 30,
        "quality": "",
        "audio": [WOW_SOUND],
        "extra_args": [],
    },
    "clips": {"pad_before": 10, "pad_after": 15, "min_run_minutes": 5, "raids": "all",
              "other_folders": []},
    "storage": {
        "session_retention_days": 3,
        "clip_retention_days": 0,
        "min_free_gb": 20,
        "stop_below_gb": 5,
    },
    "discord": {
        "webhook_urls": [],
        "notify": {key: default for key, (default, _) in NOTIFY_EVENTS.items()},
    },
    "hooks": {"after_clips": ""},
    "clips_window": {key: True for key in CLIPS_WINDOW},
    # Off until chosen: setup asks (the settings window's first run, or `setup`).
    "updates": {"check": False},
    "daemon": {"poll_interval": 5},
    "logging": {"detailed": False},
}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    combat_log_dir: Path | None
    output_dir: Path | None
    recorder_command: list[str]
    capture: str
    fps: int
    quality: str
    audio: list[str]
    extra_args: list[str]
    pad_before: int
    pad_after: int
    min_run_minutes: int
    session_retention_days: int
    clip_retention_days: int
    min_free_gb: float
    stop_below_gb: float
    webhook_urls: list[str]
    notify: dict[str, bool]
    after_clips: str
    poll_interval: int
    detailed_logging: bool = False
    monitor: str = ""   # what "Record monitor instead" records; "" = "screen"
    raids: str = "all"  # raid boss pulls clipped: "all", "kills" or "off"
    check_updates: bool = False   # the tray app asks GitHub once a day for a newer version
    other_clip_dirs: list[Path] = field(default_factory=list)   # the clip browser's, read only
    # What the clips window shows (CLIPS_WINDOW keys); only the desktop app reads it.
    clips_window: dict[str, bool] = field(default_factory=lambda: {k: True for k in CLIPS_WINDOW})

    # Everything the app writes is in these three folders, never loose in output_dir, so the
    # retention cleanup can't touch anything else the user keeps there.
    @property
    def sessions_dir(self) -> Path:
        """Full session recordings not cut into clips yet (the current one included)."""
        return self.output_dir / "sessions"

    @property
    def processed_dir(self) -> Path:
        """Session recordings already cut, kept for session_retention_days."""
        return self.output_dir / "processed"

    @property
    def clips_dir(self) -> Path:
        return self.output_dir / "clips"


def _merge(defaults: dict, data: dict) -> dict:
    """defaults with data's values on top. Unknown keys in data are dropped."""
    out = {}
    for key, default in defaults.items():
        value = data.get(key, default)
        if isinstance(default, dict):
            out[key] = _merge(default, value if isinstance(value, dict) else {})
        else:
            out[key] = value
    return out


def _path(value: str) -> Path | None:
    # An empty path would mean the working directory; None makes problems() catch it instead.
    return Path(value).expanduser() if value else None


def _list(value) -> list[str]:
    # `audio = "default_output"` instead of a list would otherwise be split into letters.
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _webhooks(value) -> list[str]:
    urls = list(dict.fromkeys(u.strip() for u in _list(value) if u.strip()))   # no repeats
    if len(urls) > MAX_WEBHOOKS:
        raise ConfigError(f"{len(urls)} webhooks; at most {MAX_WEBHOOKS}")
    return urls


def _raids(value) -> str:
    value = str(value).strip().lower()
    if value not in RAID_CHOICES:
        raise ConfigError(f"raids = {value!r}; it's one of {', '.join(RAID_CHOICES)}")
    return value


def _upgrade(data: dict) -> dict:
    """Older configs had one webhook_url; it becomes the first entry of webhook_urls."""
    d = data.get("discord")
    if isinstance(d, dict) and "webhook_url" in d and "webhook_urls" not in d:
        old = str(d.pop("webhook_url")).strip()
        d["webhook_urls"] = [old] if old else []
    return data


def _at_least(value, low, name: str):
    if value < low:
        raise ConfigError(f"{name} = {value}; it can't be below {low}")
    return value


def from_dict(data: dict) -> Config:
    p, r, c, s, d = (data[k] for k in ("paths", "recorder", "clips", "storage", "discord"))
    try:
        after_clips = str(data["hooks"]["after_clips"]).strip()
        shlex.split(after_clips)   # unbalanced quotes: caught here, not after a session
        return Config(
            combat_log_dir=_path(p["combat_log_dir"]),
            output_dir=_path(p["output_dir"]),
            recorder_command=shlex.split(r["command"]),
            capture=str(r["capture"]),
            monitor=str(r["monitor"]).strip(),
            raids=_raids(c["raids"]),
            check_updates=bool(data["updates"]["check"]),
            fps=_at_least(int(r["fps"]), 1, "fps"),
            quality=str(r["quality"]).strip(),
            audio=_list(r["audio"]),
            extra_args=_list(r["extra_args"]),
            pad_before=_at_least(int(c["pad_before"]), 0, "pad_before"),
            pad_after=_at_least(int(c["pad_after"]), 0, "pad_after"),
            min_run_minutes=_at_least(int(c["min_run_minutes"]), 0, "min_run_minutes"),
            session_retention_days=_at_least(int(s["session_retention_days"]), 0,
                                             "session_retention_days"),
            clip_retention_days=_at_least(int(s["clip_retention_days"]), 0, "clip_retention_days"),
            min_free_gb=_at_least(float(s["min_free_gb"]), 0, "min_free_gb"),
            stop_below_gb=_at_least(float(s["stop_below_gb"]), 0, "stop_below_gb"),
            webhook_urls=_webhooks(d["webhook_urls"]),
            notify={k: bool(v) for k, v in d["notify"].items()},
            after_clips=after_clips,
            poll_interval=max(1, int(data["daemon"]["poll_interval"])),
            detailed_logging=bool(data["logging"]["detailed"]),
            other_clip_dirs=[Path(f).expanduser() for f in _list(c["other_folders"]) if f.strip()],
            clips_window={k: bool(v) for k, v in data["clips_window"].items()},
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"Bad value in the config: {e}") from e


def load_raw(path: Path = CONFIG_FILE) -> dict:
    """The config file merged over the defaults, as a plain dict (defaults if it's missing)."""
    if not path.exists():
        return _merge(DEFAULTS, {})
    try:
        return _merge(DEFAULTS, _upgrade(tomllib.loads(path.read_text())))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def load(path: Path = CONFIG_FILE) -> Config:
    if not path.exists():
        raise ConfigError(f"No config at {path}. Run `{APP_NAME} setup` first.")
    return from_dict(load_raw(path))


def problems(cfg: Config) -> list[str]:
    """What stops the app from working with this config; empty when it's fine."""
    found = []
    for name in ("combat_log_dir", "output_dir"):
        value = getattr(cfg, name)
        if value is None:
            found.append(f"{name} isn't set")
        elif not value.is_dir():
            found.append(f"{name} {value} doesn't exist (drive not mounted?)")
    if not cfg.recorder_command or not shutil.which(cfg.recorder_command[0]):
        found.append(f"recorder command not found: {shlex.join(cfg.recorder_command)}")
    for tool in ("ffmpeg", "ffprobe", "grep"):
        if not shutil.which(tool):
            found.append(f"{tool} isn't installed")
    return found


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)   # a valid TOML basic string
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    raise TypeError(f"can't write {value!r} to TOML")


def _webhook_list(urls: list[str]) -> str:
    if not urls:
        return "[]"
    return "[\n" + "".join(f"    {_toml(u)},\n" for u in urls) + "]"


def render(data: dict) -> str:
    """The config file for data (a dict shaped like DEFAULTS), with comments."""
    p, r, c, s, d = (data[k] for k in ("paths", "recorder", "clips", "storage", "discord"))
    width = max(len(k) for k in NOTIFY_EVENTS)
    clips_window = "\n".join(f"{k} = {_toml(bool(data['clips_window'].get(k, True)))}  # {label}"
                              for k, label in CLIPS_WINDOW.items())
    notify = "\n".join(f"{k:<{width}} = {_toml(bool(d['notify'].get(k, default)))}  # {desc}"
                       for k, (default, desc) in NOTIFY_EVENTS.items())
    return f"""\
# {APP_NAME} configuration. `{APP_NAME} setup` writes this file; edit it freely and
# restart the service afterwards (systemctl --user restart {APP_NAME}).

[paths]
# WoW's combat log folder, e.g. ".../World of Warcraft/_retail_/Logs"
combat_log_dir = {_toml(p["combat_log_dir"])}
# Recordings and clips go here, in the subfolders sessions/, processed/ and clips/
output_dir = {_toml(p["output_dir"])}

[recorder]
# The gpu-screen-recorder command. For its Flatpak:
# "flatpak run --command=gpu-screen-recorder com.dec05eba.gpu_screen_recorder"
command = {_toml(r["command"])}
# "portal" (Wayland's screen-share dialog: you pick, it's remembered), a monitor name from
# `gpu-screen-recorder --list-capture-options`, or "screen": the first monitor in that list,
# which may not be your main monitor or the one your desktop calls "1"
capture = {_toml(r["capture"])}
# The monitor "Record monitor instead" (tray menu, clips window) switches a session to while
# capture is "portal", e.g. "DP-1"; "" = "screen"
monitor = {_toml(r["monitor"])}
fps = {_toml(r["fps"])}
# "medium", "high", "very_high" or "ultra"; empty = gpu-screen-recorder's default
quality = {_toml(r["quality"])}
# One audio track per entry: "wow" is WoW's own sound, "default_output" all desktop sound,
# "default_input" the microphone; "wow|default_input" mixes two into one track. [] = no audio.
# Anything else is passed to gpu-screen-recorder's -a as it is (e.g. "app:discord").
audio = {_toml(r["audio"])}
# Passed to gpu-screen-recorder as they are, e.g. ["-q", "very_high", "-k", "hevc"]
extra_args = {_toml(r["extra_args"])}

[clips]
# Seconds of video kept before and after each run
pad_before = {_toml(c["pad_before"])}
pad_after = {_toml(c["pad_after"])}
# Runs shorter than this are skipped, except completed keys
min_run_minutes = {_toml(c["min_run_minutes"])}
# Raid bosses: "all" (a clip per pull, kills and wipes; pulls under 30 s are skipped), "kills"
# or "off"
raids = {_toml(c["raids"])}
# Folders on this computer the clip browser also lists clips from, e.g. where you move them to.
# A clip there finds its details in clips/ when its .json stayed behind. [] = only clips/.
other_folders = {_toml(c["other_folders"])}

[storage]
# Full session recordings are deleted this many days after they're made (0 = never)
session_retention_days = {_toml(s["session_retention_days"])}
# Clips are deleted this many days after they're cut (0 = never)
clip_retention_days = {_toml(s["clip_retention_days"])}
# Warn when the output folder's drive has less free space than this, in GB
min_free_gb = {_toml(s["min_free_gb"])}
# Stop recording below this, so the drive never fills up completely
stop_below_gb = {_toml(s["stop_below_gb"])}

[discord]
# Optional, Discord or Fluxer webhooks (up to {MAX_WEBHOOKS}); every message goes to each one.
# Discord: Server Settings > Integrations > Webhooks > New Webhook > Copy Webhook URL.
# Fluxer: the channel's or community's settings > Webhooks.
# [] = no messages.
webhook_urls = {_webhook_list(d["webhook_urls"])}

[discord.notify]
{notify}

[hooks]
# Optional command run after clips are cut, with the new clips' paths added as arguments,
# e.g. "~/bin/upload-clips.sh". Empty = none.
after_clips = {_toml(data["hooks"]["after_clips"])}

[clips_window]
# What the clips window shows (only the desktop app reads these): false hides it
{clips_window}

[updates]
# Ask GitHub once a day whether a newer version is out (the tray app says so; installing it is
# up to you). It sends nothing about you; GitHub sees your IP address.
check = {_toml(bool(data["updates"]["check"]))}

[daemon]
# Seconds between checks for WoW
poll_interval = {_toml(data["daemon"]["poll_interval"])}

[logging]
# Logs are in ~/.local/state/linux-wow-recorder/logs/ and never leave this computer.
# true adds detailed lines, for tracking down a problem.
detailed = {_toml(data["logging"]["detailed"])}
"""


def save(data: dict, path: Path = CONFIG_FILE):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)   # one left over could be readable by others
    # Made private from the start: webhook URLs are secrets (anyone with one can post to its
    # channel), so they're never in a file others can read, not even for a moment.
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w",
              encoding="utf-8") as f:
        f.write(render(data))
    tmp.replace(path)
