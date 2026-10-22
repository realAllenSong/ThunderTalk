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


def test_notes_from_transcript_and_provider_override(run, monkeypatch, tmp_path):
    from thundertalk.core import llm_providers, meeting_notes
    transcript = tmp_path / "meeting.txt"
    transcript.write_text("We agreed to ship Friday.")
    provider = SimpleNamespace(id="fake", models=["model"], is_ready=lambda: True)
    monkeypatch.setattr(llm_providers, "detect", lambda **kw: [provider])
    seen = []
    def generate(tr, p, model, **kw):
        seen.append((tr.to_text(), p, model))
        kw["progress"](50, "notes:1:1")
        return "## Summary\nShip Friday"
    monkeypatch.setattr(meeting_notes, "generate", generate)
    dest = tmp_path / "notes.md"
    rc, events = run("notes", str(transcript), "--provider", "fake", "--model", "model", "--out", str(dest))
    assert rc == 0, events
    assert seen == [("We agreed to ship Friday.", provider, "model")]
    assert dest.read_text().startswith("## Summary")


def test_notes_no_configured_provider(run, monkeypatch, tmp_path):
    from thundertalk.core import llm_providers
    monkeypatch.setattr(llm_providers, "detect", lambda **kw: [])
    transcript = tmp_path / "a.txt"
    transcript.write_text("hello")
    rc, events = run("notes", str(transcript), "--out", str(tmp_path / "notes.md"))
    assert rc == 1 and events[-1]["code"] == "no_provider"


@pytest.mark.parametrize("soft", [False, True])
def test_burn_subtitle_input(run, monkeypatch, tmp_path, soft):
    from thundertalk.core import audio_io, burn
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: "/ffmpeg")
    src, subs, dest = tmp_path / "v.mp4", tmp_path / "s.vtt", tmp_path / "out.mp4"
    src.touch()
    subs.write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nHello\n")
    def fake(src, tr, out, **kw):
        assert tr.segments[0].text == "Hello" and kw["soft"] == soft
        Path(out).touch()
        return burn.BurnResult(out, 1, 10, 1)
    monkeypatch.setattr(burn, "burn", fake)
    # The renderer alone needs an offscreen Qt application, not soft muxing.
    rc, events = run("burn", str(src), "--subtitles", str(subs), "--out", str(dest), *(["--soft"] if soft else []))
    assert rc == 0 and events[-1]["cues"] == 1, events


def test_burn_missing_ffmpeg(run, monkeypatch, tmp_path):
    from thundertalk.core import audio_io
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: None)
    rc, events = run("burn", "video.mp4", "--out", str(tmp_path / "o.mp4"))
    assert rc == 1 and events[-1]["code"] == "ffmpeg_missing"


def test_translate_text_and_speech(run, monkeypatch, tmp_path):
    from thundertalk.core import audio_io, models, translate_process
    import numpy as np
    monkeypatch.setattr(models, "is_downloaded", lambda *a: True)
    monkeypatch.setattr(models, "get_model_path", lambda *a: "/model")
    unloaded = []
    def translated(*a, **kw):
        return SimpleNamespace(text="你好", duration_secs=2, inference_ms=5, model="fake")
    monkeypatch.setattr(translate_process, "TranslationProcess", lambda: SimpleNamespace(
        load_model=lambda *a: None, translate_text=translated, translate=translated,
        unload=lambda: unloaded.append(True)))
    monkeypatch.setattr(audio_io, "decode_audio", lambda *a: np.zeros(32000, dtype=np.float32))
    media = tmp_path / "a.wav"
    media.touch()
    for args in (["--text", "hello", "--source-language", "eng"], [str(media)]):
        dest = tmp_path / "translated.txt"
        rc, events = run("translate", *args, "--target", "cmn", "--out", str(dest))
        assert rc == 0 and dest.read_text().strip() == "你好", events
    assert len(unloaded) == 2


def test_translate_missing_model(run, monkeypatch, tmp_path):
    from thundertalk.core import models
    monkeypatch.setattr(models, "is_downloaded", lambda *a: False)
    rc, events = run("translate", "--text", "hello", "--source-language", "eng", "--target", "cmn",
                     "--out", str(tmp_path / "out.txt"))
    assert rc == 1 and events[-1]["code"] == "no_model"


def test_voice_lifecycle_and_clone_synthesis(run, monkeypatch, tmp_path):
    import numpy as np
    from thundertalk.core import audio_io, speech
    monkeypatch.setattr(audio_io, "decode_audio", lambda *a: 0.1 * np.sin(np.arange(144000) * 0.03))
    media = tmp_path / "public.wav"
    media.touch()
    rc, events = run("voices", "add", str(media), "--name", "Demo", "--transcript", "hello")
    assert rc == 0, events
    vid = events[-1]["id"]
    rc, events = run("voices", "rename", vid, "--name", "Public demo")
    assert rc == 0
    rc, events = run("voices", "edit", vid, "--transcript", "hi")
    assert rc == 0
    rc, events = run("voices", "list", "--engine", "apple")
    assert any(v["id"] == vid and v["name"] == "Public demo" for v in events[-1]["my_voices"])
    b = SimpleNamespace(info=SimpleNamespace(id="voxcpm2", needs_ref_text=True), is_ready=lambda: True)
    monkeypatch.setattr(speech, "backend", lambda *a: b)
    def synth(text, voice, **kw):
        assert voice.text == "hi" and kw["clone_backend"] == "voxcpm2"
        return SimpleNamespace(audio=np.zeros(24000), sample_rate=24000, duration=1, warnings=[])
    monkeypatch.setattr(speech, "get_engine", lambda: SimpleNamespace(synthesize=synth, unload=lambda: None))
    rc, events = run("speak", "--text", "hello", "--voice", vid, "--engine", "voxcpm2", "--out", str(tmp_path / "o.wav"))
    assert rc == 0, events
    rc, events = run("voices", "remove", vid)
    assert rc == 0
    rc, events = run("voices", "remove", vid)
    assert rc == 1 and events[-1]["code"] == "not_found"


def test_history_list_show_export_rename_edit_delete(run, fake_asr, tmp_path):
    media = tmp_path / "a.wav"
    media.touch()
    rc, events = run("transcribe", str(media), "--out", str(tmp_path / "o.txt"), "--save-history")
    assert rc == 0
    ident = events[-1]["history_id"]
    for args in (("list", "--query", "Hello"), ("show", ident), ("rename", ident, "--title", "Meeting"),
                 ("edit", ident, "--speaker-name", "S01=Alice"),
                 ("export", ident, "--out", str(tmp_path / "h.md")), ("delete", ident)):
        rc, events = run("history", *args)
        assert rc == 0, events
    rc, events = run("history", "show", ident)
    assert rc == 1 and events[-1]["code"] == "not_found"
    rc, events = run("history", "show", "../bad")
    assert rc == 1 and events[-1]["code"] == "bad_args"


def test_models_catalog_download_activate(run, monkeypatch):
    from thundertalk.core import models
    rc, events = run("models", "list")
    assert rc == 0, events
    assert {m["kind"] for m in events[-1]["models"]} == {"asr", "tts", "music", "translation"}
    have = set()
    monkeypatch.setattr(models, "is_downloaded", lambda mid: mid in have)
    def download(m, progress_cb=None, cancel=None):
        progress_cb(50, "Downloading")
        have.add(m.id)
    monkeypatch.setattr(models, "download_model", download)
    mid = "qwen3-asr-06b-int8"
    rc, events = run("models", "activate", mid)
    assert rc == 1 and events[-1]["code"] == "no_model"
    rc, events = run("models", "download", mid)
    assert rc == 0 and any(e.get("value") == 0.5 for e in events), events
    rc, events = run("models", "activate", mid)
    assert rc == 0
    assert cli._settings().active_model_id == mid
    rc, events = run("models", "download", "bogus")
    assert rc == 1 and events[-1]["code"] == "bad_args"
