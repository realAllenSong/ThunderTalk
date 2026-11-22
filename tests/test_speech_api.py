"""POST /v1/audio/speech and GET /v1/audio/voices with a fake backend (no model loaded)."""
from __future__ import annotations

import io
import threading
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk.api import speech_stream
from thundertalk.api.speech_stream import SpeechError, TtsHost, plan_pieces, split_first, wav_header
from thundertalk.core.tts_backends.base import BackendVoice


class FakeBackend:
    sample_rate = 24000

    def __init__(self, bid, ready=True, fail=False):
        self.bid, self.ready, self.fail = bid, ready, fail
        self.info = SimpleNamespace(id=bid, name=bid.title(), needs_gpu=False)
        self.calls, self.loaded = [], 0

    def is_ready(self):
        return self.ready

    def voices(self):
        return [BackendVoice(id=f"{self.bid}:0", name="En", language="english", gender="f"),
                BackendVoice(id=f"{self.bid}:5", name="Zh", language="chinese", gender="f")]

    def load(self):
        self.loaded += 1

    def generate(self, text, voice, language, *, seed=0, speed=1.0, context=None):
        if self.fail:
            raise RuntimeError("synth broke")
        self.calls.append((text, voice, language, speed))
        if context is not None:
            context["native_speed"] = True
        return np.full(int(0.05 * len(text) * self.sample_rate), 0.25, np.float32)


def host(**kw):
    made = {}

    def factory(bid):
        made[bid] = FakeBackend(bid, ready=bid in ("kokoro", "voxcpm2"), **kw)
        return made[bid]
    h = TtsHost("kokoro", backend_factory=factory)
    return h, made


# ── pieces ───────────────────────────────────────────────────────────────

def test_short_first_sentence_is_one_piece():
    assert split_first("Hello there, how are you?", "english") == ["Hello there, how are you?"]


def test_long_first_sentence_is_cut_at_its_first_clause():
    s = "The weather today looks sunny, so let's take a long walk in the park."
    assert split_first(s, "english") == ["The weather today looks sunny,", "so let's take a long walk in the park."]


def test_long_sentence_without_comma_is_cut_before_a_connective():
    s = "I checked the calendar this morning and there is nothing booked for Friday afternoon."
    head, rest = split_first(s, "english")
    assert head == "I checked the calendar this morning" and rest.startswith("and ")
    head, rest = split_first("I checked your calendar and there is nothing booked for Friday afternoon.", "english")
    assert head == "I checked your calendar" and rest.startswith("and there")


def test_chinese_is_cut_only_at_punctuation():
    assert split_first("今天天气不错，我们一起去公园散步吧，顺便买点水果回来。", "chinese")[0] == "今天天气不错，"
    s = "今天天气不错我们一起去公园散步吧顺便买点水果回来"
    assert split_first(s, "chinese") == [s]


def test_plan_pieces_keeps_every_word_and_ends_without_a_pause():
    text = "The weather today looks sunny, so let's take a long walk in the park. Bring water! See you soon."
    pieces = plan_pieces(text, "english")
    assert " ".join(p for p, _ in pieces).split() == text.split()
    assert pieces[-1][1] == 0.0 and all(pause > 0 for _, pause in pieces[:-1])


def test_wav_header_for_a_stream_is_readable():
    data = speech_stream.pcm16(np.full(2400, 0.5, np.float32))
    with wave.open(io.BytesIO(wav_header(24000) + data)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
        assert len(w.readframes(10**6)) == len(data)


# ── voices and requests ──────────────────────────────────────────────────

def test_resolve_defaults_and_openai_names():
    h, _ = host()
    assert h.resolve(None, None, "Hello there.") == ("kokoro", "kokoro:0", "english")
    assert h.resolve("tts-1", "alloy", "你好，今天怎么样？") == ("kokoro", "kokoro:5", "chinese")
    assert h.resolve("kokoro", "5", "Hi.")[1] == "kokoro:5"
    assert h.resolve(None, "voxcpm2:0", "Hi.")[0] == "voxcpm2"


@pytest.mark.parametrize("model,voice,status", [
    ("nope", None, 400), ("kokoro", "kokoro:999", 400), ("indextts", None, 503), (None, "zz:1", 400)])
def test_resolve_errors(model, voice, status):
    h, _ = host()
    with pytest.raises(SpeechError) as e:
        h.resolve(model, voice, "Hello.")
    assert e.value.status == status


def test_voices_lists_only_ready_engines():
    h, _ = host()
    out = h.voices()
    assert [e["id"] for e in out["engines"]] == ["kokoro", "voxcpm2"]
    assert out["engines"][0]["default"] and out["engines"][0]["sample_rate"] == 24000
    assert {"id", "name", "engine", "language", "gender", "description"} == set(out["voices"][0])


def test_stream_yields_one_chunk_per_piece_and_stops_on_cancel():
    h, made = host()
    text = "First sentence here. Second one now. Third goes last."
    chunks = list(h.stream("kokoro", "kokoro:0", "english", text))
    assert len(chunks) == len(plan_pieces(text, "english")) and all(chunks)
    cancel = threading.Event()
    gen = h.stream("kokoro", "kokoro:0", "english", text, cancel=cancel)
    next(gen)
    cancel.set()
    assert list(gen) == []


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from thundertalk.api import server
    h, made = host()
    monkeypatch.setattr(server, "_tts", h)
    return TestClient(server.app), made, server


def test_speech_pcm_streams_with_sample_rate_header(client):
    c, made, _ = client
    r = c.post("/v1/audio/speech", json={"model": "kokoro", "input": "Hello there. How are you today?",
                                         "voice": "kokoro:0", "response_format": "pcm", "speed": 1.2})
    assert r.status_code == 200
    assert r.headers["x-sample-rate"] == "24000" and r.headers["content-type"].startswith("audio/pcm")
    assert len(r.content) > 0 and len(r.content) % 2 == 0
    assert made["kokoro"].calls[0][3] == 1.2


def test_speech_wav_is_a_playable_stream(client):
    c, _, _ = client
    r = c.post("/v1/audio/speech", json={"input": "Hello there.", "voice": "kokoro:0", "response_format": "wav"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 24000 and len(w.readframes(10**6)) > 0


def test_speech_request_errors(client):
    c, _, server = client
    assert c.post("/v1/audio/speech", json={"input": "  "}).status_code == 400
    assert c.post("/v1/audio/speech", json={"input": "Hi", "response_format": "mp3"}).status_code == 400
    assert c.post("/v1/audio/speech", json={"input": "Hi", "model": "indextts"}).status_code == 503
    h, _ = host(fail=True)
    server._tts = h
    r = c.post("/v1/audio/speech", json={"input": "Hi there.", "response_format": "pcm"})
    assert r.status_code == 500 and "synth broke" in r.text


def test_voices_endpoint(client):
    c, _, _ = client
    r = c.get("/v1/audio/voices")
    assert r.status_code == 200 and r.json()["voices"][0]["id"] == "kokoro:0"


def test_cli_serve_passes_its_options(monkeypatch):
    from thundertalk import cli
    from thundertalk.api import server
    seen = {}
    monkeypatch.setattr(server, "serve", lambda host, port, **kw: seen.update(host=host, port=port, **kw))
    events = []
    monkeypatch.setattr(cli, "_Out", lambda: SimpleNamespace(emit=lambda **e: events.append(e)))
    assert cli.main(["serve", "--port", "9999", "--asr", "qwen3-asr-06b-mlx", "--no-warm-up"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 9999, "asr": "qwen3-asr-06b-mlx", "tts": "kokoro", "warm": False}
    assert events[0]["label"] == "serve" and events[-1]["ok"]
