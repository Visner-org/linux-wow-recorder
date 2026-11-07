import types

import pytest

from linux_wow_recorder import detect
from linux_wow_recorder.detect import find_wow_process, wow_alive, wow_flavor, wow_process, wow_window


@pytest.mark.parametrize("args, flavor", [
    (["C:\\Program Files (x86)\\World of Warcraft\\_retail_\\Wow.exe"], "Retail"),
    (["/home/u/Games/battlenet/drive_c/Program Files (x86)/World of Warcraft/_retail_/Wow.exe",
      "-launcherlogin"], "Retail"),
    (["Z:\\games\\wow\\_classic_\\WowClassic.exe -launcherlogin -uid wow_classic"], "Classic"),
    (["C:\\World of Warcraft\\_ptr_\\WowT.exe"], "Retail PTR"),
    (["C:\\World of Warcraft\\_beta_\\WowB.exe"], "Retail beta"),
    (["C:\\World of Warcraft\\_classic_ptr_\\WowClassicT.exe"], "Classic PTR"),
    (["wow.exe"], "Retail"),
    # Not known yet: named after the game folder.
    (["C:\\World of Warcraft\\_forever_\\WowForever.exe"], "Forever"),
    (["Z:\\wow\\_forever_beta_\\WowForeverB.exe -launcherlogin"], "Forever Beta"),
    # WoW Forever runs as Wow.exe in its own folder (seen 11-06).
    (["C:\\Program Files (x86)\\World of Warcraft\\_forever_\\Wow.exe"], "Forever"),
])
def test_finds_wow(args, flavor):
    assert wow_flavor(args) == flavor


@pytest.mark.parametrize("args", [
    ["C:\\World of Warcraft\\_retail_\\WowVoiceProxy.exe"],
    ["C:\\World of Warcraft\\_forever_\\WowError.exe"],
    ["WowForever.exe"],                                   # unknown, and not in a game folder
    ["C:\\Program Files\\WowUp\\WowUp.exe"],
    ["C:\\games\\_stuff_\\WowUp.exe"],
    ["C:\\World of Warcraft\\_retail_\\Wow.exe.bak"],
    ["C:\\ProgramData\\Battle.net\\Agent\\Agent.exe", "--game=Wow.exe"],
    ["C:\\Program Files (x86)\\Battle.net\\Battle.net.exe", "Wow.exe"],
    ["/usr/bin/python3", "wowhead.py"],
    ["/usr/bin/vim", "notes-about-wow.exe.txt"],
])
def test_ignores_others(args):
    assert wow_flavor(args) is None


@pytest.mark.parametrize("args, exe", [
    (["Z:\\games\\wow\\_classic_\\WowClassic.exe -launcherlogin"], "WowClassic.exe"),
    (["/home/u/Games/World of Warcraft/_retail_/Wow.exe"], "Wow.exe"),
    (["C:\\World of Warcraft\\_forever_\\WowForever.exe"], "WowForever.exe"),
])
def test_reports_the_exe(args, exe):
    assert wow_process(args)[1] == exe


def fake_proc(tmp_path, processes: dict[int, list[str]], zombie=()):
    for pid, args in processes.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in args) + b"\0")
        (d / "stat").write_text(f"{pid} (x) {'Z' if pid in zombie else 'S'} 1 2 3")
    return tmp_path


def test_the_game_wins_over_a_launcher_naming_its_exe(tmp_path):
    exe = r"C:\Program Files (x86)\World of Warcraft\_retail_\Wow.exe"
    proc = fake_proc(tmp_path, {10: ["umu-run", exe], 20: [exe, "-launcherlogin"], 30: ["bash"]})
    assert find_wow_process(proc) == ("Retail", "Wow.exe", 20)
    assert wow_alive(20, proc) and wow_alive(10, proc)
    assert not wow_alive(30, proc) and not wow_alive(99, proc)


def test_a_launcher_alone_still_counts(tmp_path):
    proc = fake_proc(tmp_path, {10: ["umu-run", r"C:\WoW\_retail_\Wow.exe"]}, zombie=(10,))
    assert find_wow_process(proc) == ("Retail", "Wow.exe", 10)
    assert not wow_alive(10, proc)              # a zombie is gone


def _fake_xprop(monkeypatch, windows):
    """xprop answering with these windows: {id: (pid, name)}."""
    def xprop(display, *args):
        if args[0] == "-root":
            return "_NET_CLIENT_LIST(WINDOW): window id # " + ", ".join(windows) + "\n"
        pid, name = windows[args[1]]
        return f'_NET_WM_PID(CARDINAL) = {pid}\n_NET_WM_NAME(UTF8_STRING) = "{name}"\n'
    monkeypatch.setattr(detect, "x_display", lambda: {"DISPLAY": ":0"})
    monkeypatch.setattr(detect.shutil, "which", lambda name: "/usr/bin/xprop")
    monkeypatch.setattr(detect, "_xprop", xprop)


def test_wow_window_by_process_or_name(monkeypatch):
    _fake_xprop(monkeypatch, {"0x1": (50, "Battle.net"), "0x2": (1234, "Wine window")})
    assert wow_window(1234) is True
    assert wow_window(123) is False   # not a prefix match
    _fake_xprop(monkeypatch, {"0x1": (7, "World of Warcraft")})
    assert wow_window(1234) is True   # a Flatpak's Wine: its own process IDs


def test_wow_window_cant_tell_without_x(monkeypatch):
    monkeypatch.setattr(detect, "x_display", lambda: {})
    assert wow_window(1234) is None


def test_the_display_comes_from_systemd_when_the_service_has_none(monkeypatch):
    # Started at boot, before the desktop told systemd.
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    out = "HOME=/home/me\nDISPLAY=:0\nXAUTHORITY=/run/user/1000/xauth_new\n"
    monkeypatch.setattr(detect.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=out))
    assert detect.x_display() == {"DISPLAY": ":0", "XAUTHORITY": "/run/user/1000/xauth_new"}
