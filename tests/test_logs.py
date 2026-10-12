import ast
import logging
import os
import re
import time
from pathlib import Path

import pytest

from linux_wow_recorder import config, logs

HOOK = "https://discord.com/api/webhooks/123456789/secretTOKENabcd"
FLUXER = "https://chat.example.org/api/v1/webhooks/42/tokenWXYZ"


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setattr(logs, "LOG_DIR", d)
    monkeypatch.setattr(logs, "SESSIONS_DIR", d / "sessions")
    monkeypatch.setattr(logs, "CRASH_LOG", d / "crash.log")
    monkeypatch.setattr(logs, "_IDENTITY", [
        (re.compile(r"/home/alice(?=/|\b|$)"), "~"),
        (re.compile(r"(?<![\w.-])alice-pc(?![\w-])", re.IGNORECASE), "<computer>"),
        (re.compile(r"(?<![\w.-])alice(?![\w-])", re.IGNORECASE), "<user>"),
    ])
    yield d
    logging.getLogger().handlers.clear()


def test_clean_replaces_secrets_and_identity(logdir):
    text = (f"posting to {HOOK} and {FLUXER}; saved /home/alice/Videos/wow.mkv on ALICE-PC "
            "as alice from 192.168.1.20 via 100.64.0.7, local 127.0.0.1")
    out = logs.clean(text, [HOOK])
    assert "secretTOKEN" not in out and "tokenWXYZ" not in out
    assert "webhook 1 (...abcd)" in out and "<webhook URL>" in out
    assert "~/Videos/wow.mkv" in out and "/home/alice" not in out
    assert "<computer>" in out and "<user>" in out
    assert "192.168" not in out and "100.64" not in out and "127.0.0.1" in out


def test_log_file_is_cleaned(logdir):
    path = logs.setup("recorder", webhooks=[HOOK], to_stderr=False)
    logging.getLogger("x").warning("Webhook %s failed for /home/alice/a", HOOK)
    try:
        raise ValueError(f"boom in /home/alice/b")
    except ValueError:
        logging.getLogger("x").exception("Processing failed")
    text = path.read_text()
    assert "secretTOKEN" not in text and "/home/alice" not in text
    assert "~/a" in text and "~/b" in text and "Traceback" in text


def test_detailed_switches_debug_on(logdir):
    path = logs.setup("app", detailed=False, to_stderr=False)
    logging.getLogger("x").debug("hidden")
    logs.setup("app", detailed=True, to_stderr=False)
    logging.getLogger("x").debug("shown")
    text = path.read_text()
    assert "shown" in text and "hidden" not in text


def test_prune_deletes_old_logs_and_extra_sessions(logdir):
    (logdir / "sessions").mkdir(parents=True)
    old = logdir / "recorder.log.3"
    old.write_text("x")
    long_ago = time.time() - 40 * 86400
    os.utime(old, (long_ago, long_ago))
    for i in range(14):
        (logdir / "sessions" / f"2026-10-{i + 1:02d} 20-00-00.log").write_text("x")
    logs.prune()
    assert not old.exists()
    assert len(list((logdir / "sessions").glob("*.log"))) == logs.KEEP_SESSIONS


def test_session_logs_never_overwrite(logdir):
    a, b = logs.new_session_log(), logs.new_session_log()
    assert a != b and a.exists() and b.exists()


def test_report_has_no_secrets(logdir, tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    data = config._merge(config.DEFAULTS, {})
    data["paths"]["output_dir"] = "/home/alice/Videos/WoW"
    data["discord"]["webhook_urls"] = [HOOK]
    data["hooks"]["after_clips"] = "rsync -a --to alice@10.0.0.5:/clips"
    cfg.write_text(config.render(data))
    logdir.mkdir(parents=True)
    (logdir / "recorder.log").write_text(f"posted to {HOOK}\nsaved /home/alice/x.mkv\n")
    out = logs.write_report(tmp_path / "out", config_path=cfg)
    text = out.read_text()
    assert out.name.startswith("linux-wow-recorder-report-")
    assert "has not been sent anywhere" in text
    for secret in ("secretTOKEN", "/home/alice", "10.0.0.5", "rsync"):
        assert secret not in text, secret
    assert "webhook_urls = <1 removed>" in text and "after_clips = <set, removed>" in text


def test_logs_module_has_no_network_code():
    tree = ast.parse(Path(logs.__file__).read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert not imported & {"urllib", "http", "requests", "httpx", "ftplib", "smtplib"}
    assert "notify" not in Path(logs.__file__).read_text().split('"""', 2)[2]


def test_webhook_messages_carry_no_logs_or_paths():
    """Every notifier.send(...) in the daemon: no recorder output, hook output or folder paths."""
    src = Path(__file__).parents[1] / "src/linux_wow_recorder/daemon.py"
    tree = ast.parse(src.read_text())
    sends = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "send"]
    assert sends
    for call in sends:
        names = {getattr(x, "id", None) or getattr(x, "attr", None) for x in ast.walk(call)}
        assert not names & {"log_tail", "tail", "output", "output_dir", "clips_dir",
                            "sessions_dir"}, ast.unparse(call)


def test_only_notify_talks_to_the_network():
    """The app's one outside connection is webhooks (notify.py). The desktop app's QtNetwork use
    is QLocalServer/QLocalSocket, a Unix socket on this computer."""
    src = Path(__file__).parents[1] / "src/linux_wow_recorder"
    network = {"urllib", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib"}
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text())
        modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                   for a in n.names}
        modules |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom)}
        if path.name != "notify.py":
            assert not modules & network, path
        qt_net = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                  and (n.module or "").endswith("QtNetwork") for a in n.names}
        assert qt_net <= {"QLocalServer", "QLocalSocket"}, path
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)}
        assert not calls & {"connect_ex", "create_connection", "sendto"}, path
