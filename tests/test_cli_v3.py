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
    from thundertalk.core import settings, asr
    monkeypatch.setattr(asr.AsrEngine, "load_model", lambda *a, **kw: pytest.fail("Unexpected real model load"))
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
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda *a: True)
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


@pytest.fixture
def fake_music(monkeypatch):
    from thundertalk.core import music, music_process
    monkeypatch.setattr(music_process, "total_memory_mb", lambda: 36 * 1024)
    monkeypatch.setattr(music, "missing", lambda *a: [])
    monkeypatch.setattr(music, "supported", lambda *a: True)
    requests = []
    def run(self, request, event, cancel, **kw):
        requests.append(request)
        event(("phase", "plan", 0))
        event(("tokens", 16, 32))
        event(("phase", "compose", 0))
        event(("step", 1, 8))
        for i, seed in enumerate(request.seeds):
            wav = Path(request.job_dir) / f"take-{i + 1}.wav"
            wav.touch()
            meta = dict(seed=seed, instrumental=request.instrumental, model=request.model_folder,
                        duration=request.duration, caption=request.caption, lyrics=request.lyrics,
                        shown_lyrics=request.shown_lyrics, vocal_language=request.vocal_language)
            wav.with_suffix(".json").write_text(__import__("json").dumps(meta))
            event(("take", i, str(wav), meta))
        return "done"
    monkeypatch.setattr(music_process.MusicProcess, "run", run)
    return requests


def test_compose_instrumental_progress_and_takes(run, fake_music, tmp_path):
    rc, events = run("compose", "--instrumental", "--style", "jazz", "--length", "10", "--takes", "2",
                     "--seed", "42", "--model", "turbo", "--planner", "off", "--out-dir", str(tmp_path / "takes"))
    assert rc == 0, events
    assert fake_music[0].seeds == [42, 43] and not fake_music[0].planner
    assert any(e.get("label") == "planning" and e.get("done") == 16 for e in events)
    assert any(e.get("label") == "diffusion" and e.get("total") == 8 for e in events)
    takes = [e for e in events if e["type"] == "result" and "take" in e]
    assert len(takes) == 2 and all(e["lyric_check"]["status"] == "instrumental" for e in takes)


def test_compose_song_sung_overrides_and_warning(run, fake_music, monkeypatch, tmp_path):
    from thundertalk import cli_music
    monkeypatch.setattr(cli_music, "check_lyrics", lambda *a: dict(status="checked", clear=1, total=1))
    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("[Verse]\nThunderTalk")
    overrides = tmp_path / "sung.json"
    overrides.write_text('{"ThunderTalk":"thunder talk"}')
    rc, events = run("compose", "--style", "in the style of Adele", "--lyrics-file", str(lyrics),
                     "--sung-lyrics", str(overrides), "--language", "en", "--length", "10")
    assert rc == 0, events
    req = fake_music[0]
    assert "[Verse]" in req.lyrics and "thunder talk" in req.lyrics
    assert req.artist_warnings and events[-1]["warning"]
    assert events[-1]["takes"][0]["lyric_check"]["clear"] == 1


@pytest.mark.parametrize("code", ["model_busy", "music:memory:38", "music:memory_low:14", "music:stalled",
                                  "worker_stopped", "music:load_failed", "music:failed"])
def test_compose_errors(run, fake_music, monkeypatch, code):
    from thundertalk.core import music_process
    def fail(*a, **kw):
        raise music_process.MusicError(code, "detail")
    monkeypatch.setattr(music_process.MusicProcess, "run", fail)
    rc, events = run("compose", "--instrumental", "--style", "jazz", "--length", "10")
    assert rc == 1 and events[-1]["code"] == code.split(":")[0] + (":" + code.split(":")[1] if code.startswith("music:") else ""), events


def test_compose_validation_and_missing_model(run, fake_music, monkeypatch):
    from thundertalk.core import music
    for args in (["--length", "9"], ["--takes", "0"], ["--bpm", "1"], ["--language", "bogus"]):
        rc, events = run("compose", "--instrumental", "--style", "jazz", *args)
        assert rc == 1 and events[-1]["code"] == "bad_args", events
    monkeypatch.setattr(music, "missing", lambda *a: ["weights"])
    rc, events = run("compose", "--instrumental", "--style", "jazz")
    assert rc == 1 and events[-1]["code"] == "no_model"


def test_status_v3_capabilities(run):
    rc, events = run("status")
    assert rc == 0
    assert events[-1]["cli_version"] == 3
    assert {"notes", "compose", "burn", "translate", "history", "models"} <= set(events[-1]["capabilities"])


def test_download_cleanup_after_decode_error_and_interrupt(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk.core import audio_io
    for exc in (audio_io.AudioDecodeError("bad audio"), KeyboardInterrupt()):
        work = tmp_path / "download"
        work.mkdir()
        path = work / "a.wav"
        path.touch()
        monkeypatch.setattr(links, "fetch_audio", lambda *a, **kw: links.Fetched(str(path), "title", 2, "url", str(work)))
        def fail(*a, **kw):
            raise exc
        monkeypatch.setattr(transcribe, "transcribe_file", fail)
        rc, events = run("transcribe", "https://example.com/video", "--out", str(tmp_path / "out.txt"))
        assert rc == 1 and events[-1]["code"] in ("decode", "cancelled")
        assert not work.exists()


@pytest.mark.parametrize("name,content", [("x.json", "{}"), ("x.json", "[]"),
                                         ("x.srt", "not subtitles"), ("x.txt", "")])
def test_bad_transcript(run, monkeypatch, tmp_path, name, content):
    from thundertalk import cli_studio
    monkeypatch.setattr(cli_studio, "notes_provider", lambda *a: (SimpleNamespace(id="fake"), "model"))
    path = tmp_path / name
    path.write_text(content)
    rc, events = run("notes", str(path), "--out", str(tmp_path / "notes.md"))
    assert rc == 1 and events[-1]["code"] == "bad_transcript", events


@pytest.mark.parametrize("code", ["empty", "too_long", "invalid", "failed", "no_provider"])
def test_notes_failures(run, monkeypatch, tmp_path, code):
    from thundertalk import cli_studio
    from thundertalk.core import meeting_notes
    monkeypatch.setattr(cli_studio, "notes_provider", lambda *a: (SimpleNamespace(id="fake"), "model"))
    def fail(*a, **kw):
        raise meeting_notes.NotesError(code)
    monkeypatch.setattr(meeting_notes, "generate", fail)
    path = tmp_path / "a.txt"
    path.write_text("hello")
    rc, events = run("notes", str(path), "--out", str(tmp_path / "notes.md"))
    assert rc == 1 and events[-1]["code"] == code


@pytest.mark.parametrize("code,expected", [("no_ffmpeg", "ffmpeg_missing"), ("no_video", "burn_no_video"), ("failed", "burn_failed")])
def test_burn_failures_preserve_existing_output(run, monkeypatch, tmp_path, code, expected):
    from thundertalk.core import audio_io, burn
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: "/ffmpeg")
    def fail(*a, **kw):
        raise burn.BurnError(code, "detail")
    monkeypatch.setattr(burn, "burn", fail)
    video, subs, dest = tmp_path / "v.mp4", tmp_path / "s.srt", tmp_path / "dest.mp4"
    video.touch()
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    dest.write_bytes(b"keep me")
    rc, events = run("burn", str(video), "--subtitles", str(subs), "--soft", "--out", str(dest))
    assert rc == 1 and events[-1]["code"] == expected
    assert dest.read_bytes() == b"keep me"


@pytest.mark.parametrize("args", [[], ["--text", ""], ["--text", "hi", "--source-language", "bad"],
                                  ["--text", "hi", "--source-language", "eng", "x.wav"]])
def test_translation_bad_args(run, args, tmp_path):
    rc, events = run("translate", *args, "--target", "cmn", "--out", str(tmp_path / "out.txt"))
    assert rc == 1 and events[-1]["code"] == "bad_args"


@pytest.mark.parametrize("action,args", [("add", []), ("add", ["missing.wav", "--name", "demo"]),
                                          ("rename", ["bad", "--name", "x"]), ("edit", ["bad"]), ("remove", ["../bad"])])
def test_voice_invalid_inputs(run, action, args):
    rc, events = run("voices", action, *args)
    assert rc == 1 and events[-1]["code"] in ("bad_args", "not_found")


def test_voice_bad_reference(run, monkeypatch, tmp_path):
    import numpy as np
    from thundertalk.core import audio_io
    monkeypatch.setattr(audio_io, "decode_audio", lambda *a: np.zeros(24000))
    media = tmp_path / "silent.wav"
    media.touch()
    rc, events = run("voices", "add", str(media), "--name", "silent")
    assert rc == 1 and events[-1]["code"] == "bad_reference"


def test_compose_take_management_and_repaint(run, fake_music, tmp_path):
    rc, events = run("compose", "--instrumental", "--style", "jazz", "--length", "10")
    assert rc == 0
    take = events[-1]["takes"][0]["output"]
    wav = Path(take)
    meta = __import__("json").loads(wav.with_suffix(".json").read_text())
    meta["repaint_source_latents_file"] = wav.with_suffix(".latents.npy").name
    meta["seed"] = 1
    wav.with_suffix(".json").write_text(__import__("json").dumps(meta))
    wav.with_suffix(".latents.npy").touch()
    for action in ("show", "check", "export", "repaint"):
        options = (["--out", str(tmp_path / "export.wav")] if action == "export" else
                   ["--start", "1", "--end", "3"] if action == "repaint" else [])
        rc, events = run("compose", action, take, *options)
        assert rc == 0, events
    assert fake_music[-1].task_type == "repaint"
    assert fake_music[-1].src_audio == take
    rc, events = run("compose", "list")
    assert rc == 0 and take in events[-1]["takes"]
    rc, events = run("compose", "delete", take)
    assert rc == 0 and not wav.exists() and not wav.with_suffix(".json").exists()


def test_compose_preserves_take_on_later_failure(run, fake_music, monkeypatch):
    from thundertalk.core import music_process
    original = music_process.MusicProcess.run
    def fail(self, *a, **kw):
        original(self, *a, **kw)
        raise music_process.MusicError("music:failed", "later take failed")
    monkeypatch.setattr(music_process.MusicProcess, "run", fail)
    rc, events = run("compose", "--instrumental", "--style", "jazz", "--length", "10")
    assert rc == 1 and events[-1]["code"] == "music:failed"
    assert len(events[-1]["takes"]) == 1
    assert Path(events[-1]["takes"][0]["output"]).is_file()


def test_transcribe_batch_notes_and_multi_export(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk import cli_studio
    from thundertalk.core import meeting_notes
    monkeypatch.setattr(cli_studio, "notes_provider", lambda *a: (SimpleNamespace(id="fake"), "model"))
    monkeypatch.setattr(meeting_notes, "generate", lambda *a, **kw: "## Summary\nMeeting summary")
    media = tmp_path / "a.wav"
    media.touch()
    rc, events = run("transcribe", str(media), "--out-dir", str(tmp_path / "out"),
                     "--formats", "md", "srt", "json", "--notes")
    assert rc == 0, events
    outputs = events[-1]["items"][0]["outputs"]
    assert len(outputs) == 3
    assert "Meeting summary" in Path(outputs[0]).read_text()
    data = __import__("json").loads(Path(outputs[2]).read_text())
    assert data["title"] == "a" and data["notes"].startswith("## Summary")


def test_download_explicitly_enables_network_and_restores_environment(run, monkeypatch):
    from thundertalk.core import models
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    seen = []
    def download(*a, **kw):
        import os
        from huggingface_hub import constants
        seen.append((os.environ["HF_HUB_OFFLINE"], constants.HF_HUB_OFFLINE))
    monkeypatch.setattr(models, "download_model", download)
    rc, events = run("models", "download", "qwen3-asr-06b-int8")
    assert rc == 0, events
    assert seen == [("0", False)]
    assert __import__("os").environ["HF_HUB_OFFLINE"] == "1"


def test_notes_from_media_uses_url_context(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk import cli_studio
    from thundertalk.core import meeting_notes
    monkeypatch.setattr(cli_studio, "notes_provider", lambda *a: (SimpleNamespace(id="fake"), "model"))
    monkeypatch.setattr(meeting_notes, "generate", lambda tr, *a, **kw: "## Summary\n" + tr.to_text())
    media = tmp_path / "a.wav"
    media.touch()
    rc, events = run("notes", str(media), "--out", str(tmp_path / "notes.md"))
    assert rc == 0 and events[-1]["title"] == "a", events


def test_compose_sung_dictionary_and_another_take(run, fake_music):
    rc, events = run("compose", "lyrics", "--lyrics", "[Verse]\nThunderTalk 很好", "--language", "zh",
                     "--remember", "ThunderTalk=桑德托克")
    assert rc == 0, events
    assert "桑德托克" in events[-1]["sung_lyrics"]
    rc, events = run("compose", "lyrics", "--forget", "ThunderTalk")
    assert rc == 0 and "thundertalk" not in events[-1]["remembered"]
    rc, events = run("compose", "--instrumental", "--style", "jazz", "--length", "10", "--seed", "12")
    take = events[-1]["takes"][0]["output"]
    rc, events = run("compose", "another", take)
    assert rc == 0, events
    assert fake_music[-1].task_type == "text2music" and fake_music[-1].caption == "jazz"
    assert fake_music[-1].duration == 10 and fake_music[-1].instrumental


def test_transcription_respects_shared_model_slot_and_headroom(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk.core.priority import EXTRA_MODEL
    media = tmp_path / "a.wav"
    media.touch()
    assert EXTRA_MODEL.acquire(blocking=False)
    try:
        rc, events = run("transcribe", str(media), "--out", str(tmp_path / "o.txt"))
        assert rc == 1 and events[-1]["code"] == "model_busy", events
    finally:
        EXTRA_MODEL.release()
    monkeypatch.setattr(transcribe, "_has_model_headroom", lambda *a: False)
    rc, events = run("transcribe", str(media), "--out", str(tmp_path / "o.txt"))
    assert rc == 1 and events[-1]["code"] == "memory", events
    assert EXTRA_MODEL.acquire(blocking=False)
    EXTRA_MODEL.release()


def test_music_model_catalog_reports_xl_hardware_requirement(run, monkeypatch):
    from thundertalk.core import music_process
    monkeypatch.setattr(music_process, "total_memory_mb", lambda: 16 * 1024)
    rc, events = run("models", "list", "--kind", "music")
    assert rc == 0
    rows = {row["id"]: row for row in events[-1]["models"]}
    assert rows["acestep-turbo"]["compatible"]
    assert not rows["acestep-xl"]["compatible"]


def test_trimming_never_overwrites_original(run, fake_asr, tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"original")
    rc, events = run("transcribe", str(path), "--max-seconds", "10", "--out", str(path))
    assert rc == 1 and events[-1]["code"] == "bad_args", events
    assert path.read_bytes() == b"original"


def test_notes_failure_keeps_transcript_and_renamed_speakers(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk import cli_studio
    from thundertalk.core import meeting_notes
    monkeypatch.setattr(cli_studio, "notes_provider", lambda *a: (SimpleNamespace(id="fake"), "model"))
    def fail(tr, *a, **kw):
        assert tr.label("S01") == "Alice"
        raise meeting_notes.NotesError("failed")
    monkeypatch.setattr(meeting_notes, "generate", fail)
    src, dest = tmp_path / "a.wav", tmp_path / "a.md"
    src.touch()
    rc, events = run("transcribe", str(src), "--out", str(dest), "--notes", "--save-history", "--speaker-name", "S01=Alice")
    assert rc == 1 and dest.is_file(), events
    assert events[-1]["notes_error"]["code"] == "failed"
    assert events[-1]["history_id"] and events[-1]["output"] == str(dest)


def test_clip_preview_detection_with_limits(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk import cli_media
    from contextlib import contextmanager
    @contextmanager
    def preview(*a, **kw):
        yield tmp_path / "preview.wav", "title", "https://example.com", 1800
    monkeypatch.setattr(cli_media, "source", preview)
    rc, events = run("transcribe", "https://example.com", "--out", str(tmp_path / "x.md"), "--max-seconds", "600")
    assert rc == 1 and events[-1]["code"] == "login", events


def test_settings_writers_merge_changes(run):
    # The GUI's long-lived Settings instance must not revert a CLI activation.
    first, second = cli._settings(), cli._settings()
    first.set("active_model_id", "qwen3-asr-06b-int8")
    second.set("studio_speed", 1.5)
    saved = cli._settings()
    assert saved.active_model_id == "qwen3-asr-06b-int8" and saved.get("studio_speed") == 1.5


def test_translate_text_obeys_shared_model_slot(run, monkeypatch, tmp_path):
    from thundertalk.core import models, translate_process
    from thundertalk.core.priority import EXTRA_MODEL
    monkeypatch.setattr(models, "is_downloaded", lambda *a: True)
    monkeypatch.setattr(translate_process, "TranslationProcess", lambda: pytest.fail("Must not load while busy"))
    assert EXTRA_MODEL.acquire(blocking=False)
    try:
        rc, events = run("translate", "--text", "hi", "--source-language", "eng", "--target", "cmn", "--out", str(tmp_path / "x.txt"))
        assert rc == 1 and events[-1]["code"] == "model_busy", events
    finally:
        EXTRA_MODEL.release()


def test_clone_auto_and_best_ignore_noncloning_preference(run, monkeypatch, tmp_path):
    from thundertalk.core import speech, voices, tts
    import numpy as np
    cli._settings().set("studio_engine", "kokoro")
    saved = SimpleNamespace(id="demo", ref_text="hello")
    monkeypatch.setattr(voices.VoiceLibrary, "get", lambda *a: saved)
    monkeypatch.setattr(voices.VoiceLibrary, "prompt", lambda *a: tts.ClonePrompt(np.zeros(24000), "hello"))
    monkeypatch.setattr(speech, "backend", lambda bid: SimpleNamespace(is_ready=lambda: True))
    used = []
    def synth(*a, **kw):
        used.append(kw["clone_backend"])
        return SimpleNamespace(audio=np.zeros(24000), sample_rate=24000, duration=1, warnings=[])
    monkeypatch.setattr(speech, "get_engine", lambda: SimpleNamespace(synthesize=synth, unload=lambda: None))
    for engine in ("auto", "best"):
        rc, events = run("speak", "--text", "hello", "--voice", "my:demo", "--engine", engine, "--out", str(tmp_path / "o.wav"))
        assert rc == 0, events
    assert used == ["voxcpm2", "indextts"]


def test_tts_music_model_downloads_progress_and_activation(run, monkeypatch):
    from thundertalk import cli_library
    from thundertalk.core import music, speech
    rows = [dict(id="voxcpm2", kind="tts", ready=True, compatible=True),
            dict(id="acestep-turbo", kind="music", ready=True, compatible=True)]
    monkeypatch.setattr(cli_library, "catalog", lambda: rows)
    downloaded = []
    def download(info, progress=None, **kw):
        downloaded.append(info)
        progress(25, "Downloading")
    monkeypatch.setattr(speech, "download_backend", download)
    for mid in ("voxcpm2", "acestep-turbo"):
        rc, events = run("models", "download", mid, *(["--planner"] if mid.startswith("acestep") else []))
        assert rc == 0 and any(e.get("value") == .25 for e in events), events
        rc, events = run("models", "activate", mid)
        assert rc == 0, events
    assert music.PLANNER in downloaded[-1].downloads
    assert cli._settings().get("studio_engine") == "voxcpm2"
    assert cli._settings().get("compose_model") == "acestep-turbo"


@pytest.mark.parametrize("kind", ["cancelled", "disk_full", "failed"])
def test_model_download_failures_are_coded(run, monkeypatch, kind):
    from thundertalk.core import models
    def fail(*a, **kw):
        if kind == "cancelled":
            raise models.DownloadCancelled()
        if kind == "disk_full":
            raise OSError(28, "Disk full")
        raise RuntimeError("network failure")
    monkeypatch.setattr(models, "download_model", fail)
    rc, events = run("models", "download", "qwen3-asr-06b-int8")
    assert rc == 1 and events[-1]["code"] == kind


def test_burn_transcribes_first(run, fake_asr, monkeypatch, tmp_path):
    from thundertalk.core import audio_io, burn
    monkeypatch.setattr(audio_io, "find_ffmpeg", lambda: "/ffmpeg")
    src = tmp_path / "v.mp4"
    src.touch()
    def fake(src, tr, dest, **kw):
        assert tr.to_text() == "Hello world"
        Path(dest).touch()
        return burn.BurnResult(dest, 1, 1, 1)
    monkeypatch.setattr(burn, "burn", fake)
    rc, events = run("burn", str(src), "--soft", "--out", str(tmp_path / "out.mp4"))
    assert rc == 0, events


def test_clone_requires_text_and_available_engine(run, monkeypatch, tmp_path):
    from thundertalk.core import voices, speech
    monkeypatch.setattr(voices.VoiceLibrary, "get", lambda *a: SimpleNamespace(id="demo", ref_text=""))
    for ready, expected in ((False, "tts_missing"), (True, "transcript_required")):
        monkeypatch.setattr(speech, "backend", lambda *a: SimpleNamespace(is_ready=lambda: ready))
        rc, events = run("speak", "--text", "hello", "--voice", "my:demo", "--engine", "voxcpm2", "--out", str(tmp_path / "x.wav"))
        assert rc == 1 and events[-1]["code"] == expected


def test_compose_unavailable_check_is_advisory(run, fake_music, monkeypatch):
    from thundertalk.core import models
    monkeypatch.setattr(models, "is_downloaded", lambda *a: False)
    rc, events = run("compose", "--lyrics", "[Verse]\nHello world", "--style", "pop", "--length", "10")
    assert rc == 0, events
    assert events[-1]["takes"][0]["lyric_check"] == dict(status="unavailable", code="music:no_asr")
