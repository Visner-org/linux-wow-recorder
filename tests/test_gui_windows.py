"""Smoke tests for the clip browser and the settings window, offscreen (tests/conftest.py).

They need the gui extra and the gui-test group: uv run --extra gui --group gui-test pytest
"""

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PySide6.QtMultimedia")

from PySide6.QtCore import QFile, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from linux_wow_recorder import config, service  # noqa: E402
from linux_wow_recorder.clips import read_sidecar, write_sidecar  # noqa: E402
from linux_wow_recorder.gui import browser, model, settings  # noqa: E402

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

    assert w.filter_button.text() == "Showing: All"
    w.filter_button.click()
    assert w.filter_button.text() == "Showing: Completed" and w.cards.count() == 1
    w.filter_button.click()
    assert w.filter_button.text() == "Showing: Abandoned"
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

    # A bigger window grows the video, not the list.
    w.show()
    left = w.left.width()
    w.resize(w.width() + 600, w.height())
    QApplication.processEvents()
    assert w.left.width() == left

    # Cards or table: one button that switches, remembered.
    assert w.view_button.text() == "Table"
    w.view_button.click()
    assert w.list_stack.currentWidget() is w.tree and w.view_button.text() == "Cards"
    assert browser.ClipBrowser().list_stack.currentWidget().__class__.__name__ == "QTreeWidget"
    w.view_button.click()
    assert w.list_stack.currentWidget() is w.cards

    w.search.setText("stone")
    assert w.tree.topLevelItemCount() == 0
    assert w.title.text() == "No clips match"



def test_upload_to_youtube_copies_a_description_and_opens_the_page(qtbot, cfg, monkeypatch):
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", level=2, result="completed", label="+2 Completed")
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    opened, shown, told = [], [], []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()))
    monkeypatch.setattr(w, "show_in_folder", lambda: shown.append(w.current.path))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    assert w.youtube_button.isVisibleTo(w)
    w.upload_to_youtube()
    assert QGuiApplication.clipboard().text().startswith("Ara-Kara +2 Completed, 8m00s, ")
    assert opened == [model.YOUTUBE_UPLOAD_URL] and shown == [w.current.path]
    assert told and "paste" in told[0]


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


def test_shift_and_ctrl_pick_several_clips_to_trash_at_once(qtbot, cfg, monkeypatch):
    clips = [make_clip(cfg, f"{n}.mp4", dungeon="Ara-Kara", result="completed",
                       label="+2 Completed", run_start=RUN_START + i * 3600)
             for i, n in enumerate("abc")]
    monkeypatch.setattr(browser.ClipBrowser, "next_thumbnail", lambda self: None)   # shown: no ffmpeg
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    w.show()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    trashed = []
    monkeypatch.setattr(QFile, "moveToTrash", lambda path: trashed.append(path) or Path(path).unlink() or True)

    # Newest first: c, b, a. Click c, then Ctrl+click a.
    def card(row):
        return w.cards.visualItemRect(w.cards.item(row)).center()
    qtbot.mouseClick(w.cards.viewport(), Qt.LeftButton, pos=card(0))
    qtbot.mouseClick(w.cards.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=card(2))
    assert [c.path for c in w.selected_clips()] == [clips[2], clips[0]]
    assert "2 selected" in w.count.text()
    assert w.current.path == clips[0]    # the last one clicked is shown

    w.delete()
    assert len(asked) == 1 and asked[0].startswith("Move 2 clips to the trash?")
    assert not clips[0].exists() and not clips[2].exists() and clips[1].exists()
    assert w.cards.count() == 1


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
    monkeypatch.setattr(service, "install",   # records restarts only
                        lambda at_login=True, restart=True: restart and installed.append(at_login))
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


def test_switching_to_a_picked_window_asks_again(qtbot, cfg, fake_recorder, monkeypatch):
    data = config.load_raw()
    data["recorder"]["capture"] = "DP-2"
    config.save(data)
    monkeypatch.setattr(service, "state", lambda: "active")
    monkeypatch.setattr(service, "install", lambda at_login=True, restart=True: None)
    monkeypatch.setattr(settings.desktop, "install_autostart", lambda: None)
    monkeypatch.setattr(settings.desktop, "remove_autostart", lambda: None)
    forgotten = []
    monkeypatch.setattr(settings.recorder, "forget_pick", lambda: forgotten.append(1))
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    w.save()                                   # still a monitor: the old pick stays
    assert forgotten == []
    w.capture.setCurrentIndex(w.capture.findData("portal"))
    w.save()
    assert forgotten == [1] and config.load().capture == "portal"


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
    assert w.filter_button.text() == "Showing: All"

    w.mode_buttons["sessions"].click()
    assert w.current.path == rec and w.cards.count() == 1
    assert (w.title.text(), w.result_chip.text()) == ("The Stonevault", "1 run")
    assert w.filter_button.isHidden()
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


def test_settings_remembers_the_monitor_to_switch_to(qtbot, cfg, fake_recorder):
    data = config.load_raw()
    data["recorder"]["command"] = str(fake_recorder)
    data["recorder"]["monitor"] = "HDMI-A-1"
    config.save(data)
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.capture.count() == 4, timeout=5000)
    assert w.capture_value() == "portal" and w.monitor_field.isVisibleTo(w)
    assert [w.monitor.itemData(i) for i in range(w.monitor.count())] == ["", "DP-1", "HDMI-A-1"]
    assert w.collect()["recorder"]["monitor"] == "HDMI-A-1"
    w.monitor.setCurrentIndex(w.monitor.findData("DP-1"))
    assert w.collect()["recorder"]["monitor"] == "DP-1"
    w.capture.setCurrentIndex(w.capture.findData("DP-1"))   # a monitor itself: nothing to switch to
    assert not w.monitor_field.isVisibleTo(w)


def test_the_preview_says_when_the_screen_share_dialog_is_waiting(qtbot, cfg, monkeypatch):
    from linux_wow_recorder import state
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    status = {"state": state.NOT_RECORDING, "session_start": 1.0, "picking": True,
              "capture": "portal", "problem": "Pick WoW's window <here>."}
    monkeypatch.setattr(state, "read", lambda path=None: status)
    asked = []
    monkeypatch.setattr(state, "request_monitor", lambda monitor: asked.append(monitor))
    w.update_live()
    assert w.live_box.isVisibleTo(w) and w.live_box.title() == "Not recording"
    assert "Pick WoW's window &lt;here&gt;." in w.live_image.text()
    assert w.live_switch.isVisibleTo(w) and w.live_switch.text() == "Record monitor instead"
    w.live_switch.click()
    assert asked == [True]
    status.update(state=state.RECORDING, picking=False, problem=None, monitor_switch=True,
                  capture="DP-1", recording_file=str(cfg.output_dir / "now.mkv"))
    monkeypatch.setattr(browser.live, "preview_input", lambda path: None)
    w.update_live()
    assert w.live_box.title() == "Recording now" and "Pick" not in w.live_image.text()
    assert w.live_switch.text() == "Record the window again"
    assert not w.live_repick.isVisibleTo(w)   # recording a monitor
    status.update(monitor_switch=False, capture="portal")
    w.update_live()
    assert w.live_repick.isVisibleTo(w)
    repicks = []
    monkeypatch.setattr(state, "request_repick", lambda: repicks.append(1))
    w.live_repick.click()
    assert repicks == [1]
    status.update(state=state.WAITING, session_start=None, recording_file=None)
    w.update_live()
    assert not w.live_box.isVisibleTo(w)


def test_a_clip_shows_the_poster_until_it_plays(qtbot, cfg):
    from PySide6.QtMultimedia import QMediaPlayer
    clip = make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.tree.topLevelItemCount() == 1, timeout=10_000)
    w.select(str(clip))
    assert w.video_stack.currentWidget() is w.poster
    w.on_state(QMediaPlayer.PlayingState)
    assert w.video_stack.currentWidget() is w.video


def test_settings_raid_clips_choice(qtbot, cfg, fake_recorder):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.collect()["clips"]["raids"] == "all"
    w.raids.setCurrentIndex(w.raids.findData("kills"))
    assert w.collect()["clips"]["raids"] == "kills"


def test_raid_pulls_have_their_own_view(qtbot, cfg):
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    make_clip(cfg, "b.mp4", dungeon="Vexie and the Geargrinders", result="wipe", label="LFR Wipe",
              kind="raid", raid="Liberation of Undermine", difficulty="LFR", pull=3)
    make_clip(cfg, "c.mp4", dungeon="Vexie and the Geargrinders", result="kill", label="LFR Kill",
              kind="raid", raid="Liberation of Undermine", difficulty="LFR", pull=4)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    w.set_mode(model.KEYS)
    qtbot.waitUntil(lambda: w.tree.topLevelItemCount() == 1, timeout=10_000)
    assert w.tree.topLevelItem(0).text(1) == "Ara-Kara"
    w.set_mode(model.RAIDS)
    assert w.tree.topLevelItemCount() == 2
    assert w.tree.headerItem().text(1) == "Boss" and w.count.text() == "2 of 2 clips"
    assert w.filter_button.text() == "Showing: All"
    rows = {w.tree.topLevelItem(i).text(2): w.tree.topLevelItem(i).text(3) for i in range(2)}
    assert rows == {"#3": "LFR Wipe", "#4": "LFR Kill"}
    w.filter_button.click()
    assert w.filter_button.text() == "Showing: Kills"
    assert w.tree.topLevelItemCount() == 1
    w.search.setText("undermine")             # the raid's name finds its bosses
    assert w.tree.topLevelItemCount() == 1
    w.select(str(cfg.clips_dir / "c.mp4"))
    assert w.title.text() == "Vexie and the Geargrinders, pull 4"
    assert "Liberation of Undermine" in w.facts.text()
    w.set_mode(model.KEYS)                   # the dungeon filters come back
    assert w.filter_button.text() == "Showing: All"   # Kills isn't a dungeon filter


def test_setup_asks_about_update_checks(qtbot, no_config, fake_recorder):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.first_run and w.check_updates.isChecked()   # shown ticked: a choice made at setup
    assert w.collect()["updates"]["check"] is True
    w.check_updates.setChecked(False)
    assert w.collect()["updates"]["check"] is False


def test_an_existing_config_keeps_update_checks_off(qtbot, cfg, fake_recorder):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert not w.check_updates.isChecked()   # never asked before: stays off
    assert not w.service_changed({**w.collect(), "updates": {"check": True}})   # no restart


def test_check_now_says_what_github_has(qtbot, cfg, fake_recorder, monkeypatch):
    from linux_wow_recorder import updates

    class Check:
        def __init__(self):
            self.done, self.version = True, "99.0.0"
    monkeypatch.setattr(updates, "Check", Check)
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    w.check_now.click()
    qtbot.waitUntil(lambda: "99.0.0" in w.update_result.text(), timeout=3000)
    assert w.check_now.isEnabled()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_hide_chat_makes_a_copy_with_a_black_box(qtbot, cfg, monkeypatch):
    from linux_wow_recorder.gui import hidechat as hidechat_gui
    clip = make_clip(cfg, "a.mp4", dungeon="Ara-Kara", level=2, result="completed",
                     label="+2 Completed", clip_length=2.0, thumbnail="a.jpg", keep=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:size=320x180:rate=10",
                    "-t", "2", "-pix_fmt", "yuv420p", "-f", "mp4", str(clip)], check=True)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    shown = []

    def pick(dialog):
        shown.append(dialog.picker.frame.width())
        dialog.picker.box = (0.0, 0.5, 0.5, 0.5)
        return hidechat_gui.HideChatDialog.Accepted
    monkeypatch.setattr(hidechat_gui.HideChatDialog, "exec", pick)
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    assert w.hide_chat_button.isVisibleTo(w)
    w.hide_chat()
    qtbot.waitUntil(lambda: w.frame_process is None and w.chat_job is None and bool(shown),
                    timeout=60_000)
    copy = cfg.clips_dir / "a (chat hidden).mp4"
    assert shown == [320] and copy.exists() and told
    assert not copy.with_name(copy.name + ".part").exists()
    details = read_sidecar(copy)
    assert details["chat_hidden"] and not details["keep"] and "thumbnail" not in details
    assert w.current.path == copy and "chat hidden" in w.facts.text()
    assert w.current.chat_hidden
    assert sorted(w.tree.topLevelItem(i).text(1) for i in range(2)) == ["Ara-Kara", "Ara-Kara (chat hidden)"]
    assert w.settings.value("hide_chat_box") == "0,0.5,0.5,0.5"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_highlight_is_saved_as_a_clip_of_its_own(qtbot, cfg, monkeypatch):
    clip = make_clip(cfg, "a.mp4", dungeon="Ara-Kara", level=2, result="completed",
                     label="+2 Completed", clip_length=10.0, run_offset=0.0, keep=True,
                     encounters=[{"name": "Avanoxx", "start": 3.0, "end": 6.0, "killed": True}],
                     deaths=[{"name": "Tank-Realm-US", "at": 5.0}])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
                    "-t", "10", "-g", "10", "-pix_fmt", "yuv420p", "-f", "mp4", str(clip)], check=True)
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert w.highlight_button.isVisibleTo(w) and not w.highlight_bar.isVisibleTo(w)

    # Esc stops, I opens it again at the playhead
    w.start_highlight()
    assert w.highlight_bar.isVisibleTo(w) and w.timeline.marks == (0.0, 10.0)
    w.show()
    QApplication.setActiveWindow(w)   # shortcuts need the window active
    qtbot.keyClick(w, Qt.Key_Escape)
    assert not w.highlight_bar.isVisibleTo(w) and w.timeline.marks is None
    w.cards.setFocus()   # a list with focus doesn't take I for its type-ahead search
    qtbot.keyClick(w.cards, Qt.Key_I)
    assert w.highlight_bar.isVisibleTo(w) and w.timeline.marks == (0.0, 10.0)
    w.timeline.set_length(10.0, w.current.details)   # the player finding the length keeps them
    assert w.timeline.marks == (0.0, 10.0)
    w.clear_marks()
    w.search.setFocus()
    qtbot.keyClick(w.search, Qt.Key_I)   # typed in the search box: a letter, not a mark
    qtbot.keyClick(w.search, Qt.Key_Space)   # and a space, not play
    assert w.search.text() == "i " and w.timeline.marks is None
    w.search.clear()
    w.start_highlight()

    # The boss row's menu entry: from just before the pull to just after the kill
    boss = w.events.topLevelItem(1)
    assert boss.text(1) == "Avanoxx"
    kind, end = boss.data(0, Qt.UserRole + 1)
    w.highlight_event(kind, boss.data(0, Qt.UserRole), end)
    assert w.timeline.marks == (0.0, 10.0) and "0m10s" in w.marks_label.text()
    w.timeline.set_marks((2.0, 7.0))
    w.show_marks()
    w.save_highlight()
    qtbot.waitUntil(lambda: w.highlight_job is None, timeout=60_000)
    out = cfg.clips_dir / "a highlight 0m02s-0m07s.mp4"
    assert out.exists() and not out.with_name(out.name + ".part").exists()
    details = read_sidecar(out)
    assert details["highlight"]["of"] == "a.mp4" and not details["keep"]
    assert details["deaths"] == [{"name": "Tank-Realm-US", "at": 3.0}]
    assert w.current.path == out and w.current.highlight and "highlight 0m05s" in w.facts.text()
    # It flashes until it's clicked, to show where it went
    assert str(out) in w.fresh and w.flash_timer.isActive()
    w.cards.itemPressed.emit(w.cards.currentItem())
    assert not w.fresh
    assert not w.highlight_bar.isVisibleTo(w)
    assert sorted(w.tree.topLevelItem(i).text(1) for i in range(2)) == ["Ara-Kara", "Ara-Kara (highlight)"]


def test_the_highlight_button_follows_settings(qtbot, cfg):
    data = config.load_raw()
    data["clips_window"]["highlights"] = False
    config.save(data)
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    assert not w.highlight_button.isVisibleTo(w)
    w.start_highlight()
    assert not w.highlight_bar.isVisibleTo(w)


def test_the_chat_box_is_drawn_moved_and_resized(qtbot):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap
    from linux_wow_recorder.gui.hidechat import BoxPicker
    frame = QPixmap(640, 360)
    picker = BoxPicker(frame, (0.0, 0.0, 0.1, 0.1))
    qtbot.addWidget(picker)
    picker.resize(640, 360)
    qtbot.mousePress(picker, Qt.LeftButton, pos=QPoint(320, 180))   # outside: a new box
    qtbot.mouseMove(picker, QPoint(480, 270))
    qtbot.mouseRelease(picker, Qt.LeftButton, pos=QPoint(480, 270))
    assert picker.box == (0.5, 0.5, 0.25, 0.25)
    qtbot.mousePress(picker, Qt.LeftButton, pos=QPoint(400, 200))   # inside: moved
    qtbot.mouseMove(picker, QPoint(80, 20))
    qtbot.mouseRelease(picker, Qt.LeftButton, pos=QPoint(80, 20))
    assert picker.box == (0.0, 0.0, 0.25, 0.25)
    qtbot.mousePress(picker, Qt.LeftButton, pos=QPoint(160, 90))    # the corner: resized
    qtbot.mouseMove(picker, QPoint(320, 180))
    qtbot.mouseRelease(picker, Qt.LeftButton, pos=QPoint(320, 180))
    assert picker.box == (0.0, 0.0, 0.5, 0.5)


def test_full_screen_keeps_the_player_and_timeline(qtbot, cfg):
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    w.show()
    QApplication.setActiveWindow(w)   # shortcuts need the window active
    w.full_button.click()
    assert w.full_screen and w.isFullScreen()
    assert not w.left.isVisible() and not w.details_panel.isVisible()
    assert w.timeline.isVisible() and w.play_button.isVisible() and w.video_stack.isVisible()
    qtbot.keyClick(w, Qt.Key_Escape)
    assert not w.full_screen and not w.isFullScreen()
    assert w.left.isVisible() and w.details_panel.isVisible()
    w.tree.setFocus()   # the clip table with focus doesn't take F for its type-ahead search
    qtbot.keyClick(w.tree, Qt.Key_F)
    assert w.full_screen
    w.close()
    assert not w.full_screen


def test_the_speaker_mutes(qtbot, cfg):
    w = browser.ClipBrowser()
    qtbot.addWidget(w)
    w.mute_button.click()
    assert w.audio.isMuted() and not w.volume.isEnabled()
    w.mute_button.click()
    assert not w.audio.isMuted() and w.volume.isEnabled()


def test_settings_can_hide_parts_of_the_clips_window(qtbot, cfg, fake_recorder, monkeypatch):
    monkeypatch.setattr(service, "state", lambda: "active")
    installed = []
    monkeypatch.setattr(service, "install",   # records restarts only
                        lambda at_login=True, restart=True: restart and installed.append(at_login))
    monkeypatch.setattr(settings.desktop, "install_autostart", lambda: None)
    monkeypatch.setattr(settings.desktop, "remove_autostart", lambda: None)
    make_clip(cfg, "a.mp4", dungeon="Ara-Kara", result="completed", label="+2 Completed")
    b = browser.ClipBrowser()
    qtbot.addWidget(b)
    b.set_mode(model.SESSIONS)
    assert b.mode_buttons[model.SESSIONS].isVisibleTo(b)

    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w.save_button.text() == "Save", timeout=3000)
    for key in ("full_sessions", "youtube", "raids", "hide_chat"):
        w.window_parts[key].setChecked(False)
    qtbot.waitUntil(lambda: not w.check_timer.isActive(), timeout=2000)
    assert w.save_button.text() == "Save"   # the desktop app's: no restart
    w.save()
    assert installed == []

    b.reload()
    assert b.mode == model.KEYS and b.current is not None   # back to Keystones
    assert not any(btn.isVisibleTo(b) for btn in b.mode_buttons.values())
    assert not b.youtube_button.isVisibleTo(b) and not b.hide_chat_button.isVisibleTo(b)


def test_first_run_starts_nothing_at_login(qtbot, no_config, fake_recorder, monkeypatch):
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert w.wizard
    assert not w.service_at_login.isChecked() and not w.tray_at_login.isChecked()


def test_saving_can_turn_recording_at_login_on_and_off(qtbot, cfg, fake_recorder, monkeypatch):
    monkeypatch.setattr(service, "state", lambda: "active")
    calls = []
    monkeypatch.setattr(service, "install",
                        lambda at_login=True, restart=True: calls.append((at_login, restart)))
    monkeypatch.setattr(service, "at_login", lambda: False)
    monkeypatch.setattr(settings.desktop, "install_autostart", lambda: None)
    monkeypatch.setattr(settings.desktop, "remove_autostart", lambda: None)
    w = settings.SettingsWindow()
    qtbot.addWidget(w)
    assert not w.service_at_login.isChecked()
    w.service_at_login.setChecked(True)
    qtbot.waitUntil(lambda: not w.check_timer.isActive(), timeout=2000)
    assert w.save_button.text() == "Save"   # enabling alone doesn't restart it
    w.save()
    assert calls == [(True, False)]
