from linux_wow_recorder import service


def watch_with(monkeypatch, versions):
    """An UpdateWatch whose installed_version() returns versions one after another."""
    seen = iter(versions)
    monkeypatch.setattr(service, "installed_version", lambda: next(seen))
    return service.UpdateWatch()


def test_a_new_version_counts_once_two_checks_see_it(monkeypatch):
    w = watch_with(monkeypatch, ["0.5.0", "0.5.0", "0.6.0", "0.6.0"])
    assert w.check() is None          # the same as at the start
    assert w.check() is None          # new, seen once: maybe still being installed
    assert w.check() == "0.6.0"


def test_a_version_seen_once_then_gone_doesnt_count(monkeypatch):
    w = watch_with(monkeypatch, ["0.5.0", "0.6.0", "0.5.0", "0.6.0"])
    assert w.check() is None and w.check() is None and w.check() is None


def test_no_metadata_never_restarts(monkeypatch):
    w = watch_with(monkeypatch, [None, "0.6.0", "0.6.0"])   # started from a source folder
    assert w.check() is None and w.check() is None
    w = watch_with(monkeypatch, ["0.5.0", None, None])      # metadata gone mid-upgrade
    assert w.check() is None and w.check() is None


def test_a_unit_whose_program_is_gone_is_pointed_at_this_one(tmp_path, monkeypatch):
    unit = tmp_path / "linux-wow-recorder.service"
    monkeypatch.setattr(service, "UNIT_FILE", unit)
    calls = []
    monkeypatch.setattr(service, "_systemctl", lambda *args: calls.append(args))
    assert not service.refresh_unit()                       # not installed
    unit.write_text(f"[Service]\nExecStart={tmp_path / 'gone' / 'linux-wow-recorder'} run\n")
    assert service.refresh_unit() and calls == [("daemon-reload",)]
    assert unit.read_text() == service.unit_text()
    exe = tmp_path / "linux-wow-recorder"
    exe.write_text("")
    unit.write_text(f"[Service]\nExecStart={exe} run\n")    # another copy, still there: kept
    assert not service.refresh_unit() and unit.read_text() == f"[Service]\nExecStart={exe} run\n"
