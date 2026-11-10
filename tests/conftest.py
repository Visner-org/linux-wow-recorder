"""Runs before any test module imports the app: its config, state, log, autostart and status
paths are worked out at import time from these variables, so the tests never touch the real
ones. Windows open offscreen, so the GUI tests need no display."""

import atexit
import os
import shutil
import tempfile

import pytest

_root = tempfile.mkdtemp(prefix="lwr-tests-")   # mode 0700, as XDG_RUNTIME_DIR must be
for _name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
    os.environ[_name] = os.path.join(_root, _name.lower())
    os.makedirs(os.environ[_name], mode=0o700)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
atexit.register(shutil.rmtree, _root, ignore_errors=True)   # nothing left in the temp folder


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    # --no-qt-log, when pytest-qt is there (the core tests run without it): its Qt message
    # catcher is Python code, and Qt Multimedia's threads can call it while the run shuts
    # down, which crashes the interpreter. No test reads Qt's messages.
    if config.pluginmanager.hasplugin("pytest-qt"):
        config.option.qt_log = False


@pytest.fixture(autouse=True)
def _no_wow_running(monkeypatch):
    """Moment clips wait for WoW to close (clips.cut_moments): a WoW running on this computer
    mustn't change what the tests do."""
    from linux_wow_recorder import clips
    monkeypatch.setattr(clips, "find_wow", lambda: None)
