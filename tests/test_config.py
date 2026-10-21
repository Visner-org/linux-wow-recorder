import stat
import tomllib
from pathlib import Path

import pytest

from linux_wow_recorder import config


def test_render_round_trips(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    data["paths"]["combat_log_dir"] = '~/Games/World of "Warcraft"/_retail_/Logs'
    data["recorder"]["command"] = config.DEFAULTS["recorder"]["command"]
    data["recorder"]["extra_args"] = ["-q", "very_high"]
    data["discord"]["notify"]["cleanup"] = True

    path = tmp_path / "config.toml"
    config.save(data, path)
    assert tomllib.loads(path.read_text()) == data
    assert path.stat().st_mode & 0o777 == 0o600

    cfg = config.load(path)
    assert cfg.combat_log_dir.name == "Logs" and "~" not in str(cfg.combat_log_dir)
    assert cfg.output_dir is None   # empty, so problems() reports it
    assert cfg.notify["cleanup"] is True


def test_missing_keys_get_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[paths]\noutput_dir = "/tmp"\n[discord.notify]\nno_runs = true\n')
    cfg = config.load(path)
    assert cfg.fps == 30
    assert cfg.notify["no_runs"] is True and cfg.notify["clips_ready"] is True


def test_problems(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    data["paths"]["output_dir"] = str(tmp_path / "nope")
    data["recorder"]["command"] = "no-such-recorder"
    found = config.problems(config.from_dict(data))
    assert any("combat_log_dir isn't set" in p for p in found)
    assert any("doesn't exist" in p for p in found)
    assert any("no-such-recorder" in p for p in found)


def test_list_settings_given_as_strings(tmp_path):
    from linux_wow_recorder import config
    data = config.load_raw(tmp_path / "missing.toml")
    data["recorder"]["audio"] = "default_output"
    assert config.from_dict(data).audio == ["default_output"]


def test_bad_after_clips_quoting_is_a_config_error(tmp_path):
    import pytest
    from linux_wow_recorder import config
    data = config.load_raw(tmp_path / "missing.toml")
    data["hooks"]["after_clips"] = '~/bin/upload "unclosed'
    with pytest.raises(config.ConfigError):
        config.from_dict(data)


def test_webhooks_round_trip_and_old_single_url(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    data["discord"]["webhook_urls"] = ["https://discord.com/api/webhooks/1/a",
                                       "https://chat.example.org/api/webhooks/2/b"]
    path = tmp_path / "config.toml"
    config.save(data, path)
    assert config.load(path).webhook_urls == data["discord"]["webhook_urls"]

    path.write_text('[discord]\nwebhook_url = "https://discord.com/api/webhooks/1/a"\n')
    assert config.load(path).webhook_urls == ["https://discord.com/api/webhooks/1/a"]
    path.write_text('[discord]\nwebhook_url = ""\n')
    assert config.load(path).webhook_urls == []


def test_webhooks_blanks_repeats_and_limit(tmp_path):
    import pytest
    data = config.load_raw(tmp_path / "missing.toml")
    data["discord"]["webhook_urls"] = [" https://a/1 ", "", "https://a/1", "https://a/2"]
    assert config.from_dict(data).webhook_urls == ["https://a/1", "https://a/2"]
    data["discord"]["webhook_urls"] = [f"https://a/{i}" for i in range(config.MAX_WEBHOOKS + 1)]
    with pytest.raises(config.ConfigError):
        config.from_dict(data)


def test_other_folders(tmp_path):
    data = config.load_raw(tmp_path / "missing.toml")
    assert data["clips"]["other_folders"] == []
    data["clips"]["other_folders"] = ["~/server clips", ""]
    config.save(data, tmp_path / "c.toml")
    cfg = config.load(tmp_path / "c.toml")
    assert cfg.other_clip_dirs == [Path("~/server clips").expanduser()]


def test_clips_window_parts_are_on_unless_turned_off(tmp_path):
    path = tmp_path / "config.toml"
    data = config.load_raw(path)
    assert config.from_dict(data).clips_window == {k: True for k in config.CLIPS_WINDOW}
    data["clips_window"]["youtube"] = False
    config.save(data, path)
    assert "youtube = false" in path.read_text()
    assert config.load(path).clips_window["youtube"] is False
    assert config.load(path).clips_window["full_sessions"] is True


def test_saved_config_is_private_from_the_start(tmp_path):
    path = tmp_path / "config.toml"
    path.with_suffix(".tmp").write_text("left over")
    config.save(config.load_raw(tmp_path / "missing.toml"), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("section, key, value", [
    ("recorder", "fps", 0), ("storage", "session_retention_days", -1),
    ("storage", "stop_below_gb", -5), ("clips", "pad_before", -10)])
def test_values_below_their_minimum_are_config_errors(tmp_path, section, key, value):
    data = config.load_raw(tmp_path / "missing.toml")
    data[section][key] = value
    with pytest.raises(config.ConfigError, match=key):
        config.from_dict(data)
