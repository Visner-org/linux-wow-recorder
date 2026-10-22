from pathlib import Path

import pytest

from linux_wow_recorder import config, service, state, uninstall
from linux_wow_recorder.config import CONFIG_DIR, STATE_DIR
from linux_wow_recorder.gui import desktop


@pytest.fixture
def unit(tmp_path, monkeypatch):
    """A stand-in for the systemd unit: the real one is in ~/.config/systemd/user, which the
    test folders don't cover, so it must never be the one removed."""
    unit = tmp_path / "linux-wow-recorder.service"
    unit.write_text("[Unit]\n")
    removed = []
    monkeypatch.setattr(service, "UNIT_FILE", unit)
    monkeypatch.setattr(service, "uninstall", lambda: (removed.append(1), unit.unlink()))
    return removed


def setup_everything(tmp_path):
    out = tmp_path / "out"
    data = config.load_raw(tmp_path / "missing.toml")
    data["paths"] = {"combat_log_dir": str(tmp_path), "output_dir": str(out)}
    config.save(data)
    cfg = config.load()
    for folder in (cfg.sessions_dir, cfg.processed_dir, cfg.clips_dir):
        folder.mkdir(parents=True)
        (folder / "x.mkv").write_bytes(b"x" * 1000)
    (out / "my own notes.txt").write_text("mine")
    (STATE_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "logs" / "recorder.log").write_text("log")
    state.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    state.STATUS_FILE.write_text("{}")
    for f in (desktop.AUTOSTART_FILE, desktop.MENU_FILE, desktop.ICON_FILE):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    return cfg


def test_uninstall_keeps_recordings_unless_asked(tmp_path, unit):
    cfg = setup_everything(tmp_path)
    done = uninstall.remove_user_files(cfg, delete_recordings=False)
    assert unit == [1] and not service.UNIT_FILE.exists()
    for gone in (CONFIG_DIR, STATE_DIR, state.STATUS_FILE.parent, desktop.AUTOSTART_FILE,
                 desktop.MENU_FILE, desktop.ICON_FILE):
        assert not gone.exists() and str(gone) in done
    assert all(f.is_dir() for f in (cfg.sessions_dir, cfg.processed_dir, cfg.clips_dir))


def test_uninstall_deletes_only_the_apps_own_recording_folders(tmp_path, unit):
    cfg = setup_everything(tmp_path)
    assert uninstall.size_gb(uninstall.recording_folders(cfg)) == pytest.approx(3e-6)
    uninstall.remove_user_files(cfg, delete_recordings=True)
    assert not any(f.exists() for f in (cfg.sessions_dir, cfg.processed_dir, cfg.clips_dir))
    assert (cfg.output_dir / "my own notes.txt").read_text() == "mine"   # and its folder stays


def test_only_the_apps_own_folders_are_ever_removed(tmp_path):
    with pytest.raises(ValueError):
        uninstall._rmtree(tmp_path, [])   # e.g. a wrong XDG variable pointing at $HOME
    assert tmp_path.exists()


@pytest.mark.parametrize("marker, kind", [("uv-receipt.toml", "uv"), ("pipx_metadata.json", "pipx"),
                                          (None, "other")])
def test_how_the_program_was_installed(tmp_path, marker, kind):
    venv = tmp_path / "anywhere" / "linux-wow-recorder"   # uv's and pipx's folders can be moved
    package = venv / "lib" / "python3.14" / "site-packages" / "linux_wow_recorder"
    package.mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("")
    if marker:
        (venv / marker).write_text("")
    prog = uninstall.program(package / "uninstall.py")
    assert (prog.kind, prog.needs_root) == (kind, False)


def test_a_system_package_is_left_to_the_user(monkeypatch):
    monkeypatch.setattr(uninstall, "_owned_by", lambda tool, path: tool[0] == "rpm")
    prog = uninstall.program(Path("/usr/lib/python3.14/site-packages/linux_wow_recorder/uninstall.py"))
    assert prog.kind == "rpm" and prog.needs_root and prog.text == "sudo dnf remove linux-wow-recorder"
    assert uninstall.remove_program(prog) == (False, f"Remove the program with: {prog.text}")


def test_a_deb_is_left_to_the_user(monkeypatch):
    monkeypatch.setattr(uninstall, "_owned_by", lambda tool, path: tool[0] == "dpkg")
    prog = uninstall.program(Path("/usr/lib/python3/dist-packages/linux_wow_recorder/uninstall.py"))
    assert prog.kind == "deb" and prog.needs_root and prog.text == "sudo apt remove linux-wow-recorder"
