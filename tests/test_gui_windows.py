"""Smoke tests for the clip browser and the settings window, offscreen (tests/conftest.py).

They need the gui extra and the gui-test group: uv run --extra gui --group gui-test pytest
"""

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtMultimedia")

from PySide6.QtCore import QFile  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from linux_wow_recorder import config, service  # noqa: E402
from linux_wow_recorder.clips import write_sidecar  # noqa: E402
from linux_wow_recorder.gui import browser, settings  # noqa: E402

RUN_START = 1_800_000_000.0


@pytest.fixture
def no_config(monkeypatch):
    """No config file yet (the first run), and no systemd: the service isn't installed."""
    config.CONFIG_FILE.unlink(missing_ok=True)
    monkeypatch.setattr(service, "state", lambda: "not installed")
    yield
    config.CONFIG_FILE.unlink(missing_ok=True)


@pytest.fixture
def cfg(tmp_path, no_config):
    data = config.load_raw(tmp_path / "missing.toml")
    data["paths"] = {"combat_log_dir": str(tmp_path / "logs"), "output_dir": str(tmp_path / "out")}
    (tmp_path / "logs").mkdir()
    config.save(data)
    return config.load()


def make_clip(cfg, name, **details):
    cfg.clips_dir.mkdir(parents=True, exist_ok=True)
    clip = cfg.clips_dir / name
    clip.write_bytes(b"not really a video")
    write_sidecar(clip, {"version": 1, "run_start": RUN_START, "run_length": "8m00s",
                         "clip_length": 500.0, "run_offset": 30.0, "keep": False, **details})
    return clip


def test_clip_browser_lists_filters_and_shows_details(qtbot, cfg):
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara, City of Echoes", level=10, label="+10 Abandoned",
              result="abandoned", deaths_total=1, bosses_killed=1,
              encounters=[{"name": "Avanoxx", "start": 60.0, "end": 120.0, "killed": True}],
              deaths=[{"name": "Tank-Realm-US", "at": 90.0}],
              lusts=[{"name": "Bloodlust", "caster": "Shammy-Realm-US", "at": 61.0}])
    make_clip(cfg, "b.mp4", dungeon="The Stonevault", level=12, label="+12 Completed",
              result="completed", run_start=RUN_START + 3600)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)

    assert w.tree.topLevelItemCount() == 2 and w.cards.count() == 2
    assert w.current.dungeon == "The Stonevault"   # the newest is picked
    assert (w.title.text(), w.result_chip.text()) == ("The Stonevault +12", "Completed")

    abandoned = next(b for b in w.filter_group.buttons() if b.property("key") == "abandoned")
    abandoned.click()
    assert w.tree.topLevelItemCount() == 1 and w.cards.count() == 1
    assert (w.title.text(), w.result_chip.text()) == ("Ara-Kara, City of Echoes +10", "Abandoned")
    assert w.tree.topLevelItem(0).text(3) == "Abandoned"
    # The pull holds what happened during it; the run's start is on its own.
    top = [w.events.topLevelItem(i) for i in range(w.events.topLevelItemCount())]
    assert [(i.text(0), i.text(1), i.text(2)) for i in top] == [
        ("0:30", "Run starts", ""), ("1:00", "Avanoxx", "killed in 1:00 · 1 death")]
    assert [top[1].child(i).text(1) for i in range(top[1].childCount())] == [
        "Bloodlust (Shammy)", "Tank died", "Killed"]
    assert top[1].isExpanded()                     # a pull with a death is opened
    assert [(s.kind, s.start, s.end) for s in w.timeline.bosses] == [("kill", 60.0, 120.0)]
    assert len(w.timeline.markers) == 5

    # Clicking an event plays from a few seconds before it.
    w.seek_to_event(top[1].child(1))
    assert w.timeline.position == 87.0

    # Cards or table, remembered.
    w.view_group.button(1).click()
    assert w.list_stack.currentWidget() is w.tree
    assert browser.ClipBrowser().list_stack.currentWidget().__class__.__name__ == "QTreeWidget"
    w.view_group.button(0).click()

    w.search.setText("stone")
    assert w.tree.topLevelItemCount() == 0
    assert w.title.text() == "No clips match"


def test_clip_browser_picks_up_the_first_clip(qtbot, cfg):
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert w.tree.topLevelItemCount() == 0
    assert w.watcher.directories() == [str(cfg.output_dir.parent)]   # no output folder yet
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    qtbot.waitUntil(lambda: w.tree.topLevelItemCount() == 1, timeout=10_000)


def test_delete_moves_to_the_trash_and_asks_before_deleting_for_good(qtbot, cfg, monkeypatch,
                                                                    tmp_path):
    clip = make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    asked = []

    def answer(parent, title, text, buttons=None, default=None):
        asked.append(default)
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", answer)

    trash, trashed = tmp_path / "trash", []
    trash.mkdir()

    def to_trash(path):
        trashed.append(path)
        Path(path).rename(trash / Path(path).name)
        return True
    monkeypatch.setattr(QFile, "moveToTrash", to_trash)
    clip.with_suffix(".jpg").write_bytes(b"x")   # its picture goes too
    w.delete()
    assert trashed == [str(clip), str(clip.with_suffix(".json")), str(clip.with_suffix(".jpg"))]
    assert not clip.exists() and (trash / clip.name).exists()
    assert asked == [QMessageBox.No]    # No is the default

    clip = make_clip(cfg, "b.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    w.reload()
    monkeypatch.setattr(QFile, "moveToTrash", lambda path: False)   # no trash for this folder
    w.delete()
    assert len(asked) == 3              # asked again before deleting for good
    assert not clip.exists() and not clip.with_suffix(".json").exists()


@pytest.fixture
def fake_recorder(tmp_path, monkeypatch):
    """A recorder command that lists capture options after a moment, like the real one."""
    script = tmp_path / "fake-recorder"
    script.write_text("#!/bin/sh\nsleep 0.3\nprintf 'portal\\nDP-1|2560x1440\\nHDMI-A-1|1920x1080\\n'\n")
    script.chmod(0o755)
    monkeypatch.setattr(settings, "find_recorder_command", lambda: str(script))
    return script


def test_settings_window_first_run(qtbot, no_config, fake_recorder, tmp_path):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.first_run
    assert w.command.text() == str(fake_recorder)

    # The capture list starts with the defaults and fills in once the recorder answers.
    assert [w.capture.itemData(i) for i in range(w.capture.count())] == ["portal", "screen"]
    qtbot.waitUntil(lambda: w.capture.count() == 4, timeout=5000)
    assert w.capture_value() == "portal"

    # Folder hints wait for a pause in typing.
    logs = tmp_path / "Logs"
    logs.mkdir()
    (logs / "WoWCombatLog-100826_190000.txt").write_text("x")
    assert "Needed" in w.log_hint.text()
    # A first run is a wizard: Next waits for both folders.
    assert w.wizard and w.stack.currentIndex() == 0
    w.next_button.click()
    assert w.stack.currentIndex() == 0 and w.insist

    w.log_dir.setText(str(logs))
    assert "Needed" in w.log_hint.text()   # not updated yet: checks wait for a pause in typing
    qtbot.waitUntil(lambda: "Newest: WoWCombatLog-" in w.log_hint.text(), timeout=2000)

    w.out_dir.setText(str(tmp_path / "out"))
    qtbot.waitUntil(lambda: "Will be created" in w.out_hint.text(), timeout=2000)
    w.next_button.click()
    assert w.stack.currentIndex() == 1
    data = w.collect()
    assert data["paths"] == {"combat_log_dir": str(logs), "output_dir": str(tmp_path / "out")}
    assert w.write()
    assert config.load().output_dir == tmp_path / "out"


def test_settings_window_keeps_a_capture_chosen_before_the_list_arrives(qtbot, cfg, fake_recorder):
    data = config.load_raw()
    data["recorder"]["command"] = str(fake_recorder)
    data["recorder"]["capture"] = "HDMI-A-1"
    config.save(data)
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.capture_value() == "HDMI-A-1"   # not listed yet: added as typed
    qtbot.waitUntil(lambda: w.capture.count() == 4, timeout=5000)
    assert w.capture_value() == "HDMI-A-1"
    assert w.capture.currentText().startswith("Monitor HDMI-A-1")


def test_saving_restarts_the_service_only_when_the_config_changed(qtbot, cfg, fake_recorder,
                                                                  monkeypatch):
    monkeypatch.setattr(service, "state", lambda: "active")
    installed = []
    monkeypatch.setattr(service, "install", lambda: installed.append(1))
    monkeypatch.setattr(settings.desktop, "install_autostart", lambda: None)
    monkeypatch.setattr(settings.desktop, "remove_autostart", lambda: None)
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert not w.wizard and w.sidebar.count() == 6
    qtbot.waitUntil(lambda: w.save_button.text() == "Save", timeout=3000)

    w.tray_at_login.toggle()                  # not in the config file: no restart
    qtbot.waitUntil(lambda: not w.check_timer.isActive(), timeout=2000)
    assert w.save_button.text() == "Save"

    w.pad_after.setValue(w.pad_after.value() + 5)
    qtbot.waitUntil(lambda: w.save_button.text() == "Save and restart service", timeout=2000)
    w.pad_after.setValue(w.pad_after.value() - 5)   # back as it was
    qtbot.waitUntil(lambda: w.save_button.text() == "Save", timeout=2000)

    w.other_folders.addItem(str(cfg.output_dir))   # read by the desktop app only
    qtbot.waitUntil(lambda: not w.check_timer.isActive(), timeout=2000)
    assert w.save_button.text() == "Save"

    w.save()
    assert installed == []
    assert config.load().other_clip_dirs == [cfg.output_dir]   # saved all the same


def test_a_problem_marks_its_page(qtbot, cfg, fake_recorder):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    w.command.setText("no-such-recorder")
    qtbot.waitUntil(lambda: w.sidebar.item(4).text().endswith("⚠"), timeout=2000)
    assert "isn't installed" in w.checks["recorder_command"].text()


def test_the_live_preview_waits_while_the_window_isnt_looked_at(qtbot, cfg, monkeypatch):
    from PySide6.QtGui import QPixmap
    from linux_wow_recorder import state
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    monkeypatch.setattr(state, "read", lambda path=None: {
        "state": state.RECORDING, "recording_file": str(cfg.output_dir / "now.mkv")})
    decoded = []
    monkeypatch.setattr(browser.live, "preview_input", lambda path: decoded.append(path))
    w.live_pixmap = QPixmap(64, 36)
    w.live_pixmap.fill()

    monkeypatch.setattr(w, "preview_paused", lambda: True)    # minimized, or in the background
    w.update_live()
    assert w.live_box.isVisibleTo(w) and decoded == []
    assert w.live_shown_paused and "Click this window" in w.live_caption.text()

    monkeypatch.setattr(w, "preview_paused", lambda: False)   # looked at again
    w.update_live()
    assert decoded and not w.live_shown_paused


def test_a_recording_that_starts_in_the_background_gets_its_room_and_first_frame(qtbot, cfg, monkeypatch):
    from linux_wow_recorder import state
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    monkeypatch.setattr(state, "read", lambda path=None: {
        "state": state.RECORDING, "recording_file": str(cfg.output_dir / "now.mkv")})
    decoded = []
    monkeypatch.setattr(browser.live, "preview_input", lambda path: decoded.append(path))
    monkeypatch.setattr(w, "preview_paused", lambda: True)    # another window has focus

    w.update_live()
    assert decoded   # no frame yet: the first is decoded anyway, so the page is laid out now
    width = max(160, w.live_box.parentWidget().contentsRect().width() - 40)
    assert w.live_image.minimumHeight() == width * 9 // 16


def test_window_titles_show_the_version(qtbot, cfg, fake_recorder):
    from linux_wow_recorder import __version__
    w = settings.SettingsWindow()
    b = browser.ClipBrowser(service_state=lambda: "active")
    qtbot.addWidget(w)
    qtbot.addWidget(b)
    assert __version__ in w.windowTitle() and __version__ in b.windowTitle()


def test_settings_has_an_about_page_with_the_license(qtbot, cfg, fake_recorder):
    from PySide6.QtWidgets import QLabel, QPlainTextEdit

    from linux_wow_recorder import __version__
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.sidebar.item(w.sidebar.count() - 1).text() == "About"
    page = w.stack.widget(w.stack.count() - 1)
    assert any(f"Version {__version__}" == label.text() for label in page.findChildren(QLabel))
    license_box = page.findChild(QPlainTextEdit, "license")
    assert license_box is not None and "GNU GENERAL PUBLIC LICENSE" in license_box.toPlainText()


def test_full_sessions_list_the_recordings(qtbot, cfg):
    make_clip(cfg, "a.mp4", dungeon="The Stonevault", level=12, label="+12 Completed",
              result="completed")
    cfg.processed_dir.mkdir(parents=True)
    rec = cfg.processed_dir / "2026-10-12_16-39-07.mkv"
    rec.write_bytes(b"x" * 1_100_000)
    write_sidecar(rec, {"recording_start": RUN_START - 600, "length": 7200.0, "keep": False,
                        "runs": [{"dungeon": "The Stonevault", "label": "+12 Completed",
                                  "run_offset": 600.0, "run_length": "8m00s", "deaths_total": 0,
                                  "encounters": [{"name": "E.D.N.A.", "start": 700.0,
                                                  "end": 760.0, "killed": True}]}]})
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert w.mode == "keys" and w.current.path.name == "a.mp4"
    assert [b.text() for b in w.filter_group.buttons()] == ["All", "Completed", "Abandoned"]

    w.mode_buttons["sessions"].click()
    assert w.current.path == rec and w.cards.count() == 1
    assert (w.title.text(), w.result_chip.text()) == ("The Stonevault", "1 run")
    assert all(b.isHidden() for b in w.filter_group.buttons())
    run = w.events.topLevelItem(0)
    assert run.text(1) == "The Stonevault +12 Completed" and run.isExpanded()
    assert run.child(0).text(1) == "E.D.N.A."
    assert w.tree.headerItem().text(1) == "Dungeons"
    assert browser.ClipBrowser().mode == "sessions"   # remembered

    w.mode_buttons["keys"].click()
    assert w.current.path.name == "a.mp4"


def test_a_clip_moved_to_another_folder(qtbot, cfg, tmp_path):
    name = "2026-10-12 14-14 Temple of Sethraliss +10 Completed (27m46s)"
    clip = make_clip(cfg, name + ".mp4", dungeon="Temple of Sethraliss", level=10,
                     label="+10 Completed", result="completed", deaths_total=2)
    moved = tmp_path / "server"
    moved.mkdir()
    clip.rename(moved / (name.replace(" ", "-") + ".mp4"))   # moved, its .json left in clips/
    data = config.load_raw()
    data["clips"]["other_folders"] = [str(moved)]
    config.save(data)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert w.current.elsewhere and w.current.deaths == 2
    assert w.title.text() == "Temple of Sethraliss +10"
    assert f"in {moved}" in w.facts.text()
    assert w.keep_button.isHidden() and w.hook_button.isHidden()


def test_deleting_a_clip_in_a_read_only_folder_says_why_instead(qtbot, cfg, tmp_path, monkeypatch):
    moved = tmp_path / "read-only"
    moved.mkdir()
    clip = moved / "2026-10-12-16-39-Voidscar-Arena-+10-Abandoned-(13m21s).mp4"
    clip.write_bytes(b"x")
    data = config.load_raw()
    data["clips"]["other_folders"] = [str(moved)]
    config.save(data)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert w.current.path == clip
    told, asked = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    monkeypatch.setattr(browser.os, "access", lambda path, mode: Path(path) != moved)
    w.delete()
    assert asked == [] and len(told) == 1 and "read only" in told[0]
    assert clip.exists()
