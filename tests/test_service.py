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
