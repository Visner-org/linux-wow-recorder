import json
import os

from linux_wow_recorder import state


def test_status_file_round_trip(tmp_path):
    path = tmp_path / "run" / "status.json"
    status = state.Status(path)
    status.update(state=state.RECORDING, flavor="Retail")
    data = state.read(path)
    assert data["state"] == "recording" and data["flavor"] == "Retail"
    assert data["pid"] == os.getpid()

    status.update(state=state.STOPPED)
    assert state.read(path) is None


def test_status_of_a_dead_daemon(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"pid": 2**22 + 12345, "state": "recording"}))
    assert state.read(path) is None
    assert state.read(tmp_path / "missing.json") is None


def test_requests_carry_over_only_for_the_same_wow(tmp_path):
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"session": 1.0, "wow_pid": 100, "pause": True}))
    state.carry_requests(2.0, 100, control)
    assert state.pause_requested(2.0, control)
    state.carry_requests(3.0, 200, control)
    assert not control.exists()
