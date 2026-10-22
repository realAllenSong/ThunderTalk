"""Headless Studio contract, with no network and no model loading."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from thundertalk import cli
from thundertalk.core import links, transcribe


class Events:
    def __init__(self):
        self.events = []

    def emit(self, **event):
        self.events.append(event)

    def stage(self, label):
        self.emit(type="stage", label=label)

    def progress(self, value):
        self.emit(type="progress", value=value)


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from thundertalk.core import settings
    monkeypatch.setattr(settings, "_PATH", tmp_path / ".thundertalk/settings.json")
    def invoke(*args):
        out = Events()
        monkeypatch.setattr(cli, "_Out", lambda: out)
        rc = cli.main(list(args))
        return rc, out.events
    return invoke


@pytest.fixture
def fake_asr(monkeypatch):
    from thundertalk.core import asr
    monkeypatch.setattr(cli, "resolve_asr", lambda *a: "qwen3-asr-06b-int8")
    monkeypatch.setattr(asr, "AsrEngine", lambda: SimpleNamespace(
        set_language=lambda *a: None, set_hotwords=lambda *a: None,
        load_model=lambda *a, **kw: None, unload=lambda: None))
    def fake(path, engine, **kw):
        kw["progress"](50, "decode")
        return transcribe.Transcript([transcribe.Segment(0, 2, "Hello world")], 2, "fake")
    monkeypatch.setattr(transcribe, "transcribe_file", fake)


def test_url_title_progress_and_cleanup(run, fake_asr, monkeypatch, tmp_path):
    work = tmp_path / "download"
    work.mkdir()
    media = work / "audio.m4a"
    media.touch()
    def fetch(url, progress=None, **kw):
        progress(50, 100, 200)
        return links.Fetched(str(media), "A title", 2, url, str(work))
    monkeypatch.setattr(links, "fetch_audio", fetch)
    dest = tmp_path / "out.md"
    rc, events = run("transcribe", "https://b23.tv/test", "--out", str(dest))
    assert rc == 0, events[-1]
    assert events[-1]["title"] == "A title"
    assert "# A title" in dest.read_text()
    assert any(e.get("label") == "download" for e in events)
    assert any(e.get("bytes_done") == 100 for e in events)
    assert not work.exists()


@pytest.mark.parametrize("code", ["login", "unavailable", "blocked", "network", "playlist", "live",
                                  "private", "geo", "no_audio", "missing", "unsupported", "other"])
def test_link_error_codes(run, fake_asr, monkeypatch, tmp_path, code):
    def fetch(*a, **kw):
        raise links.LinkError(code, "site detail")
    monkeypatch.setattr(links, "fetch_audio", fetch)
    rc, events = run("transcribe", "https://example.com/video", "--out", str(tmp_path / "x.txt"))
    assert rc == 1 and events[-1]["code"] == code


def test_batch_continues_and_avoids_duplicate_names(run, fake_asr, tmp_path):
    media = tmp_path / "audio.wav"
    media.touch()
    rc, events = run("transcribe", str(media), "/missing.wav", str(media),
                     "--out-dir", str(tmp_path / "out"), "--format", "json")
    assert rc == 1
    items = [e for e in events if e["type"] == "result" and "item" in e]
    assert [e["ok"] for e in items] == [True, False, True]
    assert items[0]["output"] != items[2]["output"]
    assert all(Path(e["output"]).exists() for e in items if e["ok"])
    assert events[-1]["succeeded"] == 2 and events[-1]["failed"] == 1


@pytest.mark.parametrize("args", [[], ["--max-seconds", "-1"], ["--start", "nan"],
                                  ["--start", "3", "--end", "2"]])
def test_invalid_transcribe_args(run, tmp_path, args):
    inputs = [] if not args else [str(tmp_path / "x.wav")]
    rc, events = run("transcribe", *inputs, "--out", str(tmp_path / "x.txt"), *args)
    assert rc == 1 and events[-1]["code"] == "bad_args"
