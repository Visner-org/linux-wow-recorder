import pytest

from linux_wow_recorder.detect import find_wow_process, wow_alive, wow_flavor, wow_process


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
