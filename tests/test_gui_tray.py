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
    status.update(state=state.PAUSED)
    t.refresh()
    assert "Resume recording" in texts() and t.title.text() == "Paused for this session"
    assert "Folders and logs" in texts() and "Quit and stop recording" in texts()
    t.tray.hide()
