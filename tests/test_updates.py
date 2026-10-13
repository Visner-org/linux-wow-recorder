import io
import json

from linux_wow_recorder import updates


def test_versions():
    assert updates.version_tuple("0.6.0") == (0, 6, 0)
    assert updates.version_tuple("v1.2") == (1, 2)
    assert updates.version_tuple("0.7.0-rc1") is None
    assert updates.is_newer("0.7.0", "0.6.0")
    assert updates.is_newer("0.10.0", "0.9.3")          # numbers, not text
    assert not updates.is_newer("0.6.0", "0.6.0")
    assert not updates.is_newer("0.5.0", "0.6.0")
    assert not updates.is_newer("", "0.6.0")


def test_latest_version_reads_the_release_tag(monkeypatch):
    asked = []

    def urlopen(request, timeout):
        asked.append(request)
        return io.BytesIO(json.dumps({"tag_name": "0.7.0", "name": "0.7.0"}).encode())
    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    assert updates.latest_version() == "0.7.0"
    [request] = asked
    assert request.full_url == updates.LATEST_API and request.get_method() == "GET"
    assert request.data is None                              # nothing sent along
    assert request.get_header("User-agent") == "linux-wow-recorder"   # no version in it


def test_latest_version_when_github_cant_be_asked(monkeypatch):
    def offline(request, timeout):
        raise OSError("no network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", offline)
    assert updates.latest_version() is None
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda request, timeout: io.BytesIO(b"<html>rate limited</html>"))
    assert updates.latest_version() is None
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda request, timeout: io.BytesIO(b'{"tag_name": "nightly"}'))
    assert updates.latest_version() is None
