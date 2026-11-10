"""The commands, run through cli.main() as a terminal would. The service, WoW, the recorder and
the uninstall are stand-ins: nothing here reaches systemd or removes anything real."""

import builtins
import sys

import pytest

from linux_wow_recorder import cli, config, notify, service, state, uninstall
from linux_wow_recorder.clips import read_sidecar


@pytest.fixture
def run(monkeypatch, capsys):
    """run("keep", "a.mp4", answers=[...]) -> (exit code, what it printed)."""
    monkeypatch.setattr(cli.logs, "setup", lambda *a, **k: None)
    monkeypatch.setattr(cli, "find_wow", lambda: None)
    monkeypatch.setattr(service, "state", lambda: "inactive")
    monkeypatch.setattr(state, "read", lambda path=None: None)
    for name in ("install", "uninstall", "start"):   # never the real systemctl
        monkeypatch.setattr(service, name, lambda *a, **k: pytest.fail("the real service"))

    def run(*argv, answers=()):
        waiting = iter(answers)

        def answer(prompt=""):
            try:
                return next(waiting)
            except StopIteration:
                raise EOFError from None
        monkeypatch.setattr(builtins, "input", answer)
        monkeypatch.setattr(sys, "argv", ["linux-wow-recorder", *argv])
        with pytest.raises(SystemExit) as done:
            cli.main()
        return done.value.code, capsys.readouterr().out
    yield run
    config.CONFIG_FILE.unlink(missing_ok=True)


@pytest.fixture
def cfg(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    (tmp_path / "logs").mkdir()
    (tmp_path / "out" / "clips").mkdir(parents=True)
    data["paths"] = {"combat_log_dir": str(tmp_path / "logs"), "output_dir": str(tmp_path / "out")}
    data["recorder"]["command"] = "sh"   # a program that's there
    config.save(data)
    return config.load()


def set_config(**sections):
    data = config.load_raw()
    for section, values in sections.items():
        data[section].update(values)
    config.save(data)


def test_status_says_ok_or_what_is_wrong(run, cfg):
    code, out = run("status")
    assert code == 0 and out.rstrip().endswith("OK")
    assert "Webhooks : off" in out and "Daemon   : not running" in out
    set_config(paths={"output_dir": ""})
    code, out = run("status")
    assert code == 1 and "Problem  :" in out and "OK" not in out.splitlines()
    config.CONFIG_FILE.write_text("[recorder]\nfps = 0\n")
    code, out = run("status")
    assert code == 1 and "Config error:" in out


def test_a_command_with_a_bad_config_says_so_and_stops(run, cfg):
    config.CONFIG_FILE.write_text("not toml at all [")
    code, out = run("process")                # the message is the exit value
    assert str(code).startswith("Error: ") and out == ""


def test_keep_and_undo_by_name_or_path(run, cfg):
    clip = cfg.clips_dir / "a.mp4"
    clip.write_bytes(b"x")
    assert run("keep", "a.mp4") == (0, "Kept forever: a.mp4\n")
    assert read_sidecar(clip)["keep"] is True
    assert run("keep", "--undo", str(clip)) == (0, "Not kept: a.mp4\n")
    assert read_sidecar(clip)["keep"] is False
    code, out = run("keep", "a.mp4", "gone.mp4")
    assert code == 1 and "No such clip: gone.mp4" in out and "Kept forever: a.mp4" in out


def test_process_cuts_only_with_wow_closed(run, cfg, monkeypatch):
    code, out = run("process")
    assert code == 0 and out.startswith("Nothing waiting in ")
    cfg.sessions_dir.mkdir()
    (cfg.sessions_dir / "2026-10-08_19-00-00.mkv").write_bytes(b"x")   # a failed start: no clips
    code, out = run("process")
    assert code == 0 and "2026-10-08_19-00-00.mkv: 0 clip(s)" in out
    monkeypatch.setattr(cli, "find_wow", lambda: "Retail")
    code, out = run("process")
    assert code == 1 and out.startswith("WoW is running")
    monkeypatch.setattr(cli, "find_wow", lambda: None)
    monkeypatch.setattr(cli, "process_pending", lambda cfg, wait: None)   # the service is at it
    assert run("process") == (1, "Clips are already being cut.\n")


def test_add_casts_says_what_it_did(run, cfg, monkeypatch):
    assert run("add-casts") == (0, "Added your casts to 0 clip(s).\n")
    monkeypatch.setattr(cli, "add_casts", lambda cfg: (2, 1))
    code, out = run("add-casts")
    assert code == 0 and "2 clip(s)" in out and "1 clip(s) have no combat log" in out
    monkeypatch.setattr(cli, "add_casts", lambda cfg: None)
    assert run("add-casts")[0] == 1


@pytest.mark.parametrize("command", ["pause", "resume", "cut-now"])
def test_session_commands_need_the_service_and_wow(run, cfg, monkeypatch, command):
    assert run(command) == (1, "The recorder service isn't running.\n")
    monkeypatch.setattr(state, "read", lambda path=None: {"state": state.WAITING})
    assert run(command) == (1, "WoW isn't running.\n")


def test_pause_and_cut_now_ask_the_service(run, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "CONTROL_FILE", tmp_path / "control.json")
    monkeypatch.setattr(state, "read", lambda path=None: {
        "state": state.RECORDING, "session_start": 5.0, "wow_pid": 7})
    code, out = run("pause")
    assert code == 0 and out.startswith("Paused") and state.pause_requested(5.0)
    assert run("resume") == (0, "Recording again.\n") and not state.pause_requested(5.0)
    code, out = run("cut-now")
    assert code == 0 and out.startswith("Cutting the clips so far")


def test_logs_shows_where_they_are_and_the_last_lines(run, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cli.logs, "LOG_DIR", tmp_path / "logdir")
    code, out = run("logs")
    assert code == 0 and "never leave this computer" in out and "hasn't logged anything" in out
    (tmp_path / "logdir").mkdir()
    (tmp_path / "logdir" / "recorder.log").write_text("one\ntwo\nthree\n")
    code, out = run("logs", "-n", "2")
    assert "two\nthree" in out and "one" not in out


def test_report_writes_a_file_and_sends_nothing(run, cfg, tmp_path):
    set_config(discord={"webhook_urls": ["https://chat.example/api/webhooks/1/SECRETTOKEN"]})
    code, out = run("report", str(tmp_path / "reports"))
    [made] = (tmp_path / "reports").iterdir()
    assert code == 0 and str(made) in out and "Nothing has been sent" in out
    assert "SECRETTOKEN" not in made.read_text()


def test_test_webhooks_names_the_ones_that_failed(run, cfg, monkeypatch):
    assert run("test-webhooks") == (1, "No webhook_urls in the config.\n")
    set_config(discord={"webhook_urls": ["https://a/1", "https://a/2"]})
    posted = []
    monkeypatch.setattr(notify, "post", lambda url, message, label="": posted.append(url) or True)
    assert run("test-webhooks") == (0, "Sent.\n") and posted == ["https://a/1", "https://a/2"]
    monkeypatch.setattr(notify, "post", lambda url, message, label="": url.endswith("1"))
    code, out = run("test-discord")   # the old name
    assert code == 1 and out.startswith("Failed for webhook 2;")


def test_install_and_uninstall_service(run, cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(service, "install", lambda *a, **k: calls.append("install"))
    monkeypatch.setattr(service, "uninstall", lambda: calls.append("uninstall"))
    code, out = run("install-service")
    assert code == 0 and "starts at every login" in out
    code, out = run("uninstall-service")
    assert code == 0 and "left alone" in out and calls == ["install", "uninstall"]

    def broken(*a, **k):
        raise RuntimeError("no systemd here")
    monkeypatch.setattr(service, "install", broken)
    assert run("install-service") == (1, "Couldn't install the service: no systemd here\n")


def test_install_addon_needs_a_wow_folder(run, cfg, tmp_path):
    code, out = run("install-addon")
    assert code == 1 and out.strip()            # says why: the log folder isn't in a WoW folder
    game = tmp_path / "_retail_"
    (game / "Logs").mkdir(parents=True)
    (game / "WTF").mkdir()
    set_config(paths={"combat_log_dir": str(game / "Logs")})
    code, out = run("install-addon")
    assert code == 0 and (game / "Interface" / "AddOns" / cli.addon.NAME).is_dir()
    assert "WoW addon: installed" in run("status")[1]


class FakeRecorder:
    works = True

    def __init__(self, cfg):
        pass

    def start(self, out):
        self.out = out
        return self.works

    def stop(self):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_bytes(b"video")

    def log_tail(self):
        return "gsr: no such monitor"


def test_test_recorder(run, cfg, monkeypatch):
    monkeypatch.setattr(cli, "Recorder", FakeRecorder)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    code, out = run("test-recorder")
    assert code == 0 and (cfg.output_dir / "test-recording.mkv").exists() and "Saved " in out
    monkeypatch.setattr(FakeRecorder, "works", False)
    code, out = run("test-recorder")
    assert code == 1 and "gsr: no such monitor" in out
    monkeypatch.setattr(state, "read", lambda path=None: {"state": "recording", "session_start": 1.0})
    assert run("test-recorder") == (1, "WoW is running; test the recorder once it's closed.\n")
    monkeypatch.setattr(state, "read", lambda path=None: None)
    set_config(paths={"output_dir": ""})
    code, out = run("test-recorder")
    assert code == 1 and out.startswith("No output folder is set")


def test_run_refuses_a_setup_with_problems(run, cfg, monkeypatch):
    from linux_wow_recorder import daemon
    monkeypatch.setattr(daemon.Daemon, "run", lambda self: pytest.fail("started anyway"))
    set_config(paths={"output_dir": ""})
    assert run("run")[0] == 1


@pytest.fixture
def no_uninstall(monkeypatch):
    """Stand-ins for everything uninstall would remove; returns what it was asked to do."""
    asked = []
    monkeypatch.setattr(uninstall, "program", lambda: uninstall.Program(
        "uv", ["uv", "tool", "uninstall", "linux-wow-recorder"], False))
    monkeypatch.setattr(uninstall, "remove_user_files",
                        lambda cfg, delete: asked.append(("files", delete)) or ["/the/config"])
    monkeypatch.setattr(uninstall, "remove_program",
                        lambda prog: asked.append(("program",)) or (True, "Removed the program."))
    return asked


def test_uninstall_asks_first_and_no_is_the_default(run, cfg, no_uninstall):
    (cfg.clips_dir / "a.mp4").write_bytes(b"x")
    assert run("uninstall", answers=[""])[0] == 1          # Enter: no
    assert no_uninstall == []
    code, out = run("uninstall", answers=["y", "", "n"])   # yes; keep recordings; keep the program
    assert code == 0 and no_uninstall == [("files", False)]
    assert "Removed /the/config" in out and "Kept your recordings and clips" in out


def test_uninstall_yes_asks_nothing_and_keeps_recordings_unless_told(run, cfg, no_uninstall):
    (cfg.clips_dir / "a.mp4").write_bytes(b"x")
    code, out = run("uninstall", "--yes")
    assert code == 0 and no_uninstall == [("files", False), ("program",)]
    assert "Removed the program." in out
    no_uninstall.clear()
    run("uninstall", "--yes", "--delete-recordings", "--keep-program")
    assert no_uninstall == [("files", True)]


def test_setup_asks_and_saves(run, tmp_path, monkeypatch):
    config.CONFIG_FILE.unlink(missing_ok=True)
    monkeypatch.setattr(cli, "find_recorder_command", lambda: "sh")
    logs, out_dir = tmp_path / "Logs", tmp_path / "new" / "out"
    logs.mkdir()
    code, out = run("setup", answers=[
        "", str(tmp_path / "nope"), str(logs),   # a folder is needed; not there; the real one
        str(out_dir), "y",                       # the output folder, made when asked
        "DP-1", "2", "y",                        # capture, all desktop sound, the microphone
        "discord.example/hook",                  # not https: asked again
        "https://chat.example/api/webhooks/1/x", "",
        "1", "",                                 # "WoW started" on, done
        "n",                                     # no update checks
        "n", "n", "n"])                          # no test message, no test recording, not started
    assert code == 0 and out_dir.is_dir()
    assert "A folder is needed." in out and "doesn't exist." in out
    assert "A webhook URL starts with https://." in out and "Not started." in out
    saved = config.load()
    assert (saved.combat_log_dir, saved.output_dir, saved.capture) == (logs, out_dir, "DP-1")
    assert saved.audio == ["default_output", "default_input"]
    assert saved.webhook_urls == ["https://chat.example/api/webhooks/1/x"]
    assert saved.notify["session_started"] is True and saved.check_updates is False


def test_setup_keeps_what_is_there_on_enter_and_can_start_the_service(run, cfg, monkeypatch):
    monkeypatch.setattr(cli, "find_recorder_command", lambda: "sh")
    started = []
    monkeypatch.setattr(service, "install", lambda *a, **k: started.append(1))
    before = config.load_raw()
    code, out = run("setup", answers=["", "", "", "", "", "", "", "n", "y"])
    assert code == 0 and started == [1] and "Installed and started" in out
    assert config.load_raw() == before


def test_setup_stops_when_the_terminal_closes(run, cfg):
    assert run("setup", answers=[""])[0] == 1
