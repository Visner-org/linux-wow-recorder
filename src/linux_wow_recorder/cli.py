"""Command line: setup, run, status and a few test commands."""

import argparse
import logging
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import APP_NAME, APP_TITLE, __version__, config, logs, service, state
from .clips import free_gb, process_pending, set_keep
from .config import CONFIG_FILE, NOTIFY_EVENTS, ConfigError
from .detect import find_wow
from .notify import Notifier
from .recorder import Recorder

FLATPAK_ID = "com.dec05eba.gpu_screen_recorder"
FLATPAK_COMMAND = f"flatpak run --command=gpu-screen-recorder {FLATPAK_ID}"


# ── Prompts ──

def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{shown}: ").strip()
    except EOFError:
        print()
        sys.exit(1)
    return answer or default


def ask_yes(prompt: str, default: bool) -> bool:
    while True:
        answer = ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")


def ask_dir(prompt: str, default: str, create: bool) -> str:
    while True:
        answer = ask(prompt, default)
        path = Path(answer).expanduser()
        if not answer:   # Path("") is the working directory
            print("  A folder is needed.")
        elif path.is_dir():
            return answer
        elif create and ask_yes(f"  {path} doesn't exist. Create it?", True):
            path.mkdir(parents=True)
            return answer
        elif not create:
            print(f"  {path} doesn't exist.")


def ask_webhooks(current: list[str]) -> list[str]:
    """Each saved webhook: Enter keeps it, '-' removes it. Then new ones until a blank line."""
    urls = []
    for i, url in enumerate(current, 1):
        if ask(f"Webhook {i} ...{url[-8:]} (Enter keeps, '-' removes)") != "-":
            urls.append(url)
    while len(urls) < config.MAX_WEBHOOKS:
        url = ask(f"Add webhook {len(urls) + 1} URL (Enter to finish)")
        if not url:
            break
        if url not in urls:
            urls.append(url)
    else:
        print(f"   That's the most there can be ({config.MAX_WEBHOOKS}).")
    return urls


def choose_events(current: dict[str, bool]) -> dict[str, bool]:
    """A list of checkboxes: the user toggles them by number."""
    chosen = {k: current.get(k, default) for k, (default, _) in NOTIFY_EVENTS.items()}
    keys = list(NOTIFY_EVENTS)
    while True:
        for i, key in enumerate(keys, 1):
            print(f"  {i}. [{'x' if chosen[key] else ' '}] {NOTIFY_EVENTS[key][1]}")
        answer = ask("Numbers to turn on/off (e.g. 2 7), Enter when done")
        if not answer:
            return chosen
        for token in answer.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(keys):
                key = keys[int(token) - 1]
                chosen[key] = not chosen[key]


def find_recorder_command() -> str | None:
    if shutil.which("gpu-screen-recorder"):
        return "gpu-screen-recorder"
    if shutil.which("flatpak") and subprocess.run(["flatpak", "info", FLATPAK_ID],
                                                  capture_output=True).returncode == 0:
        return FLATPAK_COMMAND
    return None


# ── Commands ──

def cmd_setup(args) -> int:
    data = config.load_raw()
    paths, rec, discord = data["paths"], data["recorder"], data["discord"]
    print(f"{APP_TITLE} setup. Enter keeps the value in [brackets].\n")

    print("1. WoW's combat log folder: the Logs folder next to WoW's _retail_ (or _classic_)")
    print("   folder, e.g. ~/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs")
    paths["combat_log_dir"] = ask_dir("Combat log folder", paths["combat_log_dir"], create=False)
    if not any(Path(paths["combat_log_dir"]).expanduser().glob("**/WoWCombatLog-*.txt")):
        print("  No combat logs in it yet. That's fine if combat logging has never been on.")

    print("\n2. Where recordings and clips go. A session takes several GB, so pick a drive with room.")
    paths["output_dir"] = ask_dir("Output folder", paths["output_dir"], create=True)

    print("\n3. Recorder")
    found = find_recorder_command()
    if found:
        print(f"   Found: {found}")
        rec["command"] = found
    else:
        print("   gpu-screen-recorder isn't installed. Install it from your distro (AUR, COPR,")
        print(f"   ...) or as a Flatpak (flatpak install flathub {FLATPAK_ID}),")
        print("   then run setup again, or enter its command now.")
        rec["command"] = ask("Recorder command", rec["command"])
    print("   What to record: 'screen' (the whole screen), 'portal' (Wayland's screen-share dialog:")
    print("   pick the WoW window to record only WoW), or a monitor name from")
    print("   `gpu-screen-recorder --list-capture-options`.")
    rec["capture"] = ask("Capture", rec["capture"])
    print("   Sound:")
    choices = list(config.GAME_SOUND)
    for i, key in enumerate(choices, 1):
        print(f"  {i}. {config.GAME_SOUND[key]}")
    current = choices.index(config.game_sound(rec["audio"])) + 1
    while (answer := ask("Number", str(current))) not in [str(i) for i in range(1, len(choices) + 1)]:
        pass
    mic = ask_yes("Record your microphone too?", any("default_input" in a for a in rec["audio"]))
    rec["audio"] = config.audio_tracks(choices[int(answer) - 1], mic)

    print("\n4. Discord or Fluxer messages (optional)")
    print("   Discord: Server Settings > Integrations > Webhooks > New Webhook > Copy Webhook URL.")
    print("   Fluxer: the channel's or community's settings > Webhooks.")
    print(f"   Up to {config.MAX_WEBHOOKS} webhooks; every message goes to each one.")
    discord["webhook_urls"] = ask_webhooks(discord["webhook_urls"])
    if discord["webhook_urls"]:
        print("   Send a message when:")
        discord["notify"] = choose_events(discord["notify"])

    print("\n5. New versions")
    print("   The tray icon can ask GitHub once a day whether a newer version is out. Nothing about")
    print("   you is sent; installing it is up to you.")
    data["updates"]["check"] = ask_yes("Check for new versions?", data["updates"]["check"]
                                       or not CONFIG_FILE.exists())

    config.save(data)
    print(f"\nSaved {CONFIG_FILE}. Everything else (retention, clip padding, ...) is in there.")

    cfg = config.load()
    if cfg.webhook_urls and ask_yes("Send a test message to the webhooks?", True):
        print("  " + _sent(Notifier(cfg).send("", "Test message: setup works.", force=True)))
    if ask_yes("Record 5 seconds to test the recorder?", True):
        cmd_test_recorder(args)
    if ask_yes("Start it now and at every login (systemd user service)?", True):
        return cmd_install_service(args)
    print(f"Start it later with `{APP_NAME} install-service`, or run `{APP_NAME} run` in a terminal.")
    return 0


def _load() -> config.Config:
    try:
        return config.load()
    except ConfigError as e:
        sys.exit(f"Error: {e}")


def cmd_run(args) -> int:
    cfg = _load()
    found = config.problems(cfg)
    if found:
        for p in found:
            logging.error(p)
        return 1
    from .daemon import Daemon
    Daemon(cfg).run()
    return 0


def cmd_status(args) -> int:
    print(f"{APP_TITLE} {__version__}")
    print(f"Config   : {CONFIG_FILE}")
    print(f"Service  : {service.state()}")
    print(f"WoW      : {find_wow() or 'not running'}")
    status = state.read()
    if status:
        doing = status["state"].replace("_", " ")
        if status.get("session_start"):
            doing += f" (session since {time.strftime('%H:%M', time.localtime(status['session_start']))})"
        print(f"Daemon   : {doing}")
        if status.get("problem"):
            print(f"Problem  : {status['problem']}")
    else:
        print("Daemon   : not running")
    try:
        cfg = config.load()
    except ConfigError as e:
        print(f"Config error: {e}")
        return 1
    print(f"Logs     : {cfg.combat_log_dir}")
    print(f"Output   : {cfg.output_dir}")
    print(f"Recorder : {shlex.join(cfg.recorder_command)} -w {cfg.capture}")
    print(f"Webhooks : {len(cfg.webhook_urls) or 'off'}")
    found = config.problems(cfg)
    if not found:
        print(f"Free     : {free_gb(cfg):.1f} GB")
        print("OK")
    for p in found:
        print(f"Problem  : {p}")
    return 1 if found else 0


def cmd_process(args) -> int:
    """Cuts the recordings waiting in sessions/ now, without waiting for a session to end."""
    cfg = _load()
    if find_wow():
        print("WoW is running; the current session's recording is still being written.")
        return 1
    results = process_pending(cfg)
    if not results:
        print(f"Nothing waiting in {cfg.sessions_dir}.")
    for r in results:
        print(f"{r.recording.name}: {len(r.clips)} clip(s)")
        for _run, clip in r.clips:
            print(f"  {clip.name}")
        if r.problem:
            print(f"  {r.detail}")
    return 0


def cmd_pause(args) -> int:
    """Stops recording for the rest of this WoW session; `resume` records again."""
    pause = args.command == "pause"
    why = state.request_pause(pause)
    if why:
        print(why)
        return 1
    print("Paused: the rest of this session isn't recorded. The next time WoW starts, it is."
          if pause else "Recording again.")
    return 0


def cmd_keep(args) -> int:
    """Marks clips to be kept forever (or not), whatever clip_retention_days says."""
    cfg = _load()
    failed = 0
    for name in args.clips:
        clip = Path(name).expanduser()
        if not clip.is_absolute() and not clip.exists():
            clip = cfg.clips_dir / name   # a bare file name from clips/
        if not clip.is_file():
            print(f"No such clip: {name}")
            failed += 1
            continue
        set_keep(clip, not args.undo)
        print(f"{'Not kept' if args.undo else 'Kept forever'}: {clip.name}")
    return 1 if failed else 0


def cmd_test_recorder(args) -> int:
    cfg = _load()
    status = state.read()
    if status and status.get("state") == state.RECORDING:
        # Two recorders would fight over the encoder, and share the recorder's state file.
        print("The service is recording WoW right now; test once WoW is closed.")
        return 1
    out = cfg.output_dir / "test-recording.mkv"
    out.unlink(missing_ok=True)
    recorder = Recorder(cfg)
    print("Recording 5 seconds...")
    if not recorder.start(out):
        print(f"The recorder failed:\n{recorder.log_tail()}")
        return 1
    time.sleep(5)
    recorder.stop()
    if not out.exists() or out.stat().st_size == 0:
        print(f"No video was saved. Recorder output:\n{recorder.log_tail()}")
        return 1
    print(f"Saved {out} ({out.stat().st_size / 1e6:.1f} MB). Watch it to check the picture and sound.")
    return 0


def _sent(failed: list[int]) -> str:
    if not failed:
        return "Sent."
    return f"Failed for webhook {', '.join(map(str, failed))}; check the URL (see the message above)."


def cmd_test_webhooks(args) -> int:
    cfg = _load()
    if not cfg.webhook_urls:
        print("No webhook_urls in the config.")
        return 1
    failed = Notifier(cfg).send("", "Test message: the webhook works.", force=True)
    print(_sent(failed))
    return 1 if failed else 0


def cmd_install_service(args) -> int:
    try:
        service.install()
    except RuntimeError as e:
        print(f"Couldn't install the service: {e}")
        return 1
    print(f"Installed and started {service.UNIT_NAME}. Log: {logs.LOG_DIR / 'recorder.log'}")
    return 0


def cmd_logs(args) -> int:
    """Where the logs are, and the service log's last lines."""
    print(f"Logs are kept in {logs.LOG_DIR} and never leave this computer.\n")
    text = logs.tail(logs.LOG_DIR / "recorder.log", args.lines)
    print(text or "(the service hasn't logged anything yet)")
    return 0


def cmd_report(args) -> int:
    """Writes a report file for sharing by hand; nothing is sent."""
    folder = Path(args.folder).expanduser() if args.folder else logs.default_report_folder()
    path = logs.write_report(folder)
    print(f"Wrote {path}")
    print("Nothing has been sent anywhere. Read the file before you share it; webhook links,")
    print("your home folder, user name, computer name and IP addresses have been replaced.")
    return 0


def cmd_uninstall(args) -> int:
    """Removes everything the app made, and the program too where that needs no root."""
    from . import uninstall
    try:
        cfg = config.load()
    except (ConfigError, OSError):
        cfg = None
    prog = uninstall.program()
    folders = uninstall.recording_folders(cfg)
    print(f"This stops {APP_TITLE} and removes its service, login and menu entries, settings "
          "and logs.")
    if not args.yes and not ask_yes("Uninstall?", False):
        return 1
    delete = args.delete_recordings
    if folders and not delete and not args.yes:
        delete = ask_yes(f"Also delete your recordings and clips "
                         f"({uninstall.size_gb(folders):.1f} GB in {cfg.output_dir})?", False)
    remove = not args.keep_program and not prog.needs_root and prog.kind != "other"
    if remove and not args.yes:
        remove = ask_yes(f"Also remove the program ({prog.text})?", True)

    for path in uninstall.remove_user_files(cfg, delete):
        print(f"Removed {path}")
    if folders and not delete:
        print(f"Kept your recordings and clips in {cfg.output_dir}")
    if remove:
        ok, output = uninstall.remove_program(prog)
        print(output)
        return 0 if ok else 1
    if prog.kind in ("rpm", "pacman", "other"):
        print(f"To remove the program itself: {prog.text}")
    return 0


def cmd_uninstall_service(args) -> int:
    service.uninstall()
    print(f"Stopped and removed {service.UNIT_NAME}. The config and recordings are left alone.")
    return 0


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME, description=APP_TITLE + ": records WoW "
                                     "sessions and cuts one clip per dungeon run.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, help_text in [
        ("setup", cmd_setup, "ask for the folders and options, and write the config"),
        ("run", cmd_run, "watch for WoW and record (what the service runs)"),
        ("status", cmd_status, "show the config and check it"),
        ("process", cmd_process, "cut the recordings waiting in sessions/ now"),
        ("test-recorder", cmd_test_recorder, "record 5 seconds to check the recorder"),
        ("pause", cmd_pause, "stop recording for the rest of this WoW session"),
        ("resume", cmd_pause, "record again after pause"),
        ("test-webhooks", cmd_test_webhooks, "send a test message to every webhook"),
        ("install-service", cmd_install_service, "run at every login (systemd user service)"),
        ("uninstall-service", cmd_uninstall_service, "remove the systemd user service"),
    ]:
        sub.add_parser(name, help=help_text).set_defaults(func=func)
    # The old name, from when only Discord was supported.
    sub.add_parser("test-discord").set_defaults(func=cmd_test_webhooks)
    keep = sub.add_parser("keep", help="keep clips forever, whatever clip_retention_days says")
    keep.add_argument("clips", nargs="+", help="clip files, or names in clips/")
    keep.add_argument("--undo", action="store_true", help="let the clips expire again")
    keep.set_defaults(func=cmd_keep)
    show = sub.add_parser("logs", help="show where the logs are and the last lines")
    show.add_argument("-n", "--lines", type=int, default=40)
    show.set_defaults(func=cmd_logs)
    remove = sub.add_parser("uninstall", help="remove the service, settings, logs and the program")
    remove.add_argument("--yes", action="store_true", help="don't ask")
    remove.add_argument("--delete-recordings", action="store_true",
                        help="also delete sessions/, processed/ and clips/ in the output folder")
    remove.add_argument("--keep-program", action="store_true",
                        help="leave the program installed (uv and pipx installs)")
    remove.set_defaults(func=cmd_uninstall)
    report = sub.add_parser("report", help="write a report file to share by hand (nothing is sent)")
    report.add_argument("folder", nargs="?", help="where to write it (default: the Desktop)")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    try:
        cfg = config.load()
        detailed, webhooks = cfg.detailed_logging, cfg.webhook_urls
    except (ConfigError, OSError):
        detailed, webhooks = False, []
    # The service has its own log; everything else shares the desktop app's.
    logs.setup("recorder" if args.command == "run" else "app", detailed, webhooks)
    sys.exit(args.func(args))
