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
