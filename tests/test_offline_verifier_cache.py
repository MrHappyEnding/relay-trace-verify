import json
import runpy
from pathlib import Path

import pytest
import requests


@pytest.fixture
def cache(tmp_path, monkeypatch):
    calls = []

    def online(session, request, **kwargs):
        calls.append(request.url)
        response = requests.Response()
        response.status_code = 200
        response._content = b"Django==2.2\n"
        response.encoding = "utf-8"
        return response

    monkeypatch.setattr(requests.sessions.Session, "send", online)
    module = runpy.run_path(
        str(Path(__file__).parents[1] / "infra/m4/offline-verifier/sitecustomize.py")
    )
    module["cached_send"].__globals__["CACHE_ROOT"] = tmp_path
    return tmp_path, calls


def test_offline_grader_replays_identical_bytes_without_network(cache, monkeypatch):
    root, calls = cache
    url = "https://raw.githubusercontent.com/django/django/" + "a" * 40 + "/requirements.txt"
    monkeypatch.setenv("RTV_CACHE_RECORD", "1")
    assert requests.get(url).text == "Django==2.2\n"
    monkeypatch.delenv("RTV_CACHE_RECORD")
    assert requests.get(url).text == "Django==2.2\n"
    assert calls == [url]
    path = next(root.glob("*.json"))
    value = json.loads(path.read_text())
    value["sha256"] = "bad"
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="integrity"):
        requests.get(url)
    with pytest.raises(FileNotFoundError):
        requests.get(url + "-uncached")
    assert calls == [url]


@pytest.mark.parametrize(
    "url", ["https://example.com", "https://raw.githubusercontent.com/django/django/main/x"]
)
def test_cache_rejects_unpinned_or_unrelated_urls(cache, monkeypatch, url):
    monkeypatch.setenv("RTV_CACHE_RECORD", "1")
    with pytest.raises(RuntimeError, match="immutable"):
        requests.get(url)
    assert not cache[1]
