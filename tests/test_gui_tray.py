import pytest

pytest.importorskip("PySide6.QtDBus")


@pytest.fixture
def qapp_or_skip():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

from linux_wow_recorder import service  # noqa: E402
from linux_wow_recorder.gui import tray  # noqa: E402


def test_unit_path_escapes_like_systemd():
    assert tray._unit_path("linux-wow-recorder.service") == \
        "/org/freedesktop/systemd1/unit/linux_2dwow_2drecorder_2eservice"


@pytest.mark.parametrize("live", [True, False])
def test_service_watcher_runs_systemctl_only_when_needed(tmp_path, monkeypatch, live):
    unit = tmp_path / "unit.service"
    unit.write_text("")
    calls = []
    monkeypatch.setattr(service, "UNIT_FILE", unit)
    monkeypatch.setattr(service, "state", lambda: calls.append(1) or "active")
    monkeypatch.setattr(tray.ServiceWatcher, "_subscribe", lambda self: live)
    clock = [1000.0]
    monkeypatch.setattr(tray.time, "monotonic", lambda: clock[0])

    w = tray.ServiceWatcher()
    assert len(calls) == 1
    clock[0] += tray.SERVICE_POLL_S
    assert w.current() == "active"
    assert len(calls) == (1 if live else 2)    # with D-Bus, the signal keeps it current

    w._changed("org.freedesktop.systemd1.Unit", {"ActiveState": "failed"}, [])
    w._changed("org.freedesktop.systemd1.Service", {}, [])
    assert w.state == "failed"
    clock[0] += tray.SERVICE_RECHECK_S
    assert w.current() == "active"             # the once-a-minute check, live or not
    assert w.current(force=True) == "active" and len(calls) == (3 if live else 4)

    unit.unlink()
    assert w.current() == "not installed"


def test_tray_menu_follows_the_service(tmp_path, monkeypatch, qapp_or_skip):
    from linux_wow_recorder import state
    status = state.Status(tmp_path / "status.json")
    monkeypatch.setattr(state, "STATUS_FILE", status.path)
    monkeypatch.setattr(state, "read", lambda path=None: dict(status.data))
    monkeypatch.setattr(service, "state", lambda: "active")
    monkeypatch.setattr(tray.ServiceWatcher, "_subscribe", lambda self: False)
    t = tray.Tray(qapp_or_skip)
    texts = lambda: [a.text() for a in t.menu.actions() if a.isVisible()]   # noqa: E731

    status.update(state=state.WAITING)
    t.refresh()
    assert "Pause recording this session" not in texts()
    status.update(state=state.RECORDING, session_start=1.0)
    t.refresh()
    assert "Pause recording this session" in texts()
    assert "Cut the clips so far" in texts() and t.cut_action.isEnabled()
    status.update(cut_waiting=True)
    t.refresh()
    assert "Clips get cut when this run ends" in texts() and not t.cut_action.isEnabled()
    status.update(state=state.PAUSED)
    t.refresh()
    assert "Resume recording" in texts() and t.title.text() == "Paused for this session"
    assert "Folders and logs" in texts() and "Quit and stop recording" in texts()
    t.tray.hide()


def test_the_app_restarts_for_a_new_version_only_with_no_window_open(qapp_or_skip, monkeypatch):
    t = tray.Tray.__new__(tray.Tray)   # just what check_update uses
    t.process = None
    restarted = []
    t.restart = lambda: restarted.append(1)

    class Window:
        def __init__(self, visible, busy=False):
            self.visible, self.working = visible, busy

        def isVisible(self):
            return self.visible

        def busy(self):
            return self.working
    t.clips_window, t.settings_window, t.log_window = Window(True), None, Window(False)
    t.updates = type("W", (), {"check": lambda self: "0.6.0"})()
    t.check_update()
    assert restarted == []             # the clips window is open
    t.clips_window.visible, t.clips_window.working = False, True
    t.check_update()
    assert restarted == []             # closed, but a copy with the chat hidden is being made
    t.clips_window.working, t.process = False, object()
    t.check_update()
    assert restarted == []             # "Cut now" is running
    t.process = None
    t.check_update()
    assert restarted == [1]
    t.updates = type("W", (), {"check": lambda self: None})()
    t.check_update()
    assert restarted == [1]


def test_a_new_release_is_told_once_and_stays_in_the_menu(tmp_path, monkeypatch, qapp_or_skip):
    import types
    from linux_wow_recorder import config, updates
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(service, "state", lambda: "active")
    monkeypatch.setattr(tray.ServiceWatcher, "_subscribe", lambda self: False)
    cfg = types.SimpleNamespace(check_updates=True)
    monkeypatch.setattr(tray.Tray, "_config", lambda self, quiet=False: cfg)
    asked = []

    class Check:   # answers at once, without the network
        def __init__(self):
            asked.append(1)
            self.done, self.version = True, "99.0.0"
    monkeypatch.setattr(updates, "Check", Check)
    t = tray.Tray(qapp_or_skip)
    popups = []
    monkeypatch.setattr(t.tray, "showMessage", lambda *a: popups.append(a[1]))

    t.check_release()
    t.refresh()
    assert asked == [1] and popups == ["Version 99.0.0 is out."]
    assert t.release_action.isVisible() and t.release_action.text() == "Version 99.0.0 is out..."
    t.check_release()                  # a day hasn't passed: GitHub isn't asked again
    t.refresh()
    assert asked == [1] and len(popups) == 1   # and it's said once

    t2 = tray.Tray(qapp_or_skip)       # after a restart: the menu line without asking
    t2.refresh()
    assert asked == [1] and t2.release_action.isVisible()

    cfg.check_updates = False          # turned off: no line, no checks
    t.check_release()
    t.refresh()
    assert not t.release_action.isVisible() and asked == [1]


@pytest.mark.parametrize("at_login", [False, True])
def test_the_recorder_runs_while_the_app_is_open(tmp_path, monkeypatch, qapp_or_skip, at_login):
    states = ["inactive"]
    started, stopped = [], []
    monkeypatch.setattr(service, "state", lambda: states[0])
    monkeypatch.setattr(service, "at_login", lambda: at_login)
    monkeypatch.setattr(service, "start", lambda: started.append(1) or states.__setitem__(0, "active"))
    monkeypatch.setattr(tray.subprocess, "run", lambda cmd, **kw: stopped.append(cmd))
    monkeypatch.setattr(tray.ServiceWatcher, "_subscribe", lambda self: False)
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    monkeypatch.setattr(tray.config, "CONFIG_FILE", cfg)
    t = tray.Tray(qapp_or_skip)
    assert started == [1]                      # opening the app starts it
    t.refresh()
    hide = [a for a in t.menu.actions() if a.text().startswith("Hide tray icon")][0]
    assert hide.isVisible() == at_login        # it keeps recording only if it starts at login
    t.stop_with_app()
    assert bool(stopped) != at_login
    if stopped:
        assert stopped[0][:4] == ["systemctl", "--user", "stop", "--no-block"]


@pytest.mark.parametrize("unit", ["not installed", "inactive"])   # inactive: config gone
def test_nothing_starts_before_setup(tmp_path, monkeypatch, qapp_or_skip, unit):
    started = []
    monkeypatch.setattr(service, "state", lambda: unit)
    monkeypatch.setattr(tray.config, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(service, "start", lambda: started.append(1))
    monkeypatch.setattr(tray.ServiceWatcher, "_subscribe", lambda self: False)
    monkeypatch.setattr(tray.Tray, "show_settings", lambda self: None)
    tray.Tray(qapp_or_skip)
    assert started == []
