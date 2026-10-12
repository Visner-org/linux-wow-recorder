from linux_wow_recorder import config, notify


def test_send_posts_to_every_webhook_and_reports_failures(tmp_path, monkeypatch):
    data = config.load_raw(tmp_path / "missing.toml")
    data["discord"]["webhook_urls"] = ["https://a/1", "https://a/2", "https://a/3"]
    cfg = config.from_dict(data)
    posted = []
    monkeypatch.setattr(notify, "post",
                        lambda url, message, label="": posted.append(url) or url != "https://a/2")
    n = notify.Notifier(cfg)
    assert n.send("clips_ready", "hi") == [2]
    assert posted == data["discord"]["webhook_urls"]
    posted.clear()
    assert n.send("cleanup", "off by default") == [] and posted == []
