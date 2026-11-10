import threading

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


def test_background_send_returns_at_once_and_keeps_the_order(tmp_path, monkeypatch):
    data = config.load_raw(tmp_path / "missing.toml")
    data["discord"]["webhook_urls"] = ["https://a/1", "https://a/2"]
    cfg = config.from_dict(data)
    release, posted = threading.Event(), []

    def slow_post(url, message, label=""):
        assert release.wait(5)
        posted.append((url, message))
        return True
    monkeypatch.setattr(notify, "post", slow_post)
    n = notify.Notifier(cfg, background=True)
    assert n.send("clips_ready", "one") == []      # doesn't wait for the slow webhook
    assert n.send("clips_ready", "two") == []
    assert posted == []
    release.set()
    n.flush(timeout=5)
    assert posted == [("https://a/1", "one"), ("https://a/2", "one"),
                      ("https://a/1", "two"), ("https://a/2", "two")]


def test_a_rate_limited_message_is_sent_once_more(monkeypatch):
    import email.message
    import urllib.error

    tries, waits = [], []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        tries.append(1)
        if len(tries) == 1:
            headers = email.message.Message()
            headers["Retry-After"] = "2.5"
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", headers, None)
        return Response()
    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(notify.time, "sleep", waits.append)
    assert notify.post("https://a/1", "hi") and len(tries) == 2 and waits == [2.5]

    def always_429(req, timeout):
        tries.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, None)
    tries.clear()
    monkeypatch.setattr(notify.urllib.request, "urlopen", always_429)
    assert not notify.post("https://a/1", "hi") and len(tries) == 2   # once more, no more


def test_a_url_that_isnt_one_fails_its_message_only(tmp_path, monkeypatch, caplog):
    data = config.load_raw(tmp_path / "missing.toml")
    data["discord"]["webhook_urls"] = ["discord.example/api/webhooks/1/SECRET"]   # no https://
    cfg = config.from_dict(data)
    assert notify.post(cfg.webhook_urls[0], "hi") is False
    assert "SECRET" not in caplog.text                 # the URL stays out of the log
    assert notify.Notifier(cfg).send("clips_ready", "hi") == [1]

    posted = []

    def post(url, message, label=""):
        posted.append(message)
        if message == "one":
            raise RuntimeError("unexpected")
        return True
    monkeypatch.setattr(notify, "post", post)
    n = notify.Notifier(cfg, background=True)
    n.send("clips_ready", "one")
    n.send("clips_ready", "two")                       # the thread outlived the first
    n.flush(timeout=5)
    assert posted == ["one", "two"]


def test_only_https_and_no_pings(monkeypatch):
    import json
    sent = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        sent.append(json.loads(req.data)["content"])
        return Response()
    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    assert notify.post("http://a/1", "hi") is False and sent == []   # never in the clear
    assert notify.post("https://a/1", "Killed @everyone the Boss")
    assert "@everyone" not in sent[0] and "@\u200beveryone the Boss" in sent[0]
