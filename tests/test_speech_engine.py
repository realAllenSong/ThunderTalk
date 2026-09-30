"""SpeechEngine: the shared pipeline over pluggable backends, with fake backends."""

from __future__ import annotations

import threading

import numpy as np
import pytest

from thundertalk.core import speech, tts
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend


def _tone(seconds, sr, hz=200.0):
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


class FakeBackend(TtsBackend):
    def __init__(self, bid="fake", sr=24000, stochastic=True, gpu=False, clone=True, native_speed=False,
                 seconds_per_char=0.24, bad_first=0):
        self.info = BackendInfo(id=bid, name=bid, blurb_en="", blurb_zh="", languages=("chinese", "english"),
                                supports_presets=True, supports_clone=clone, needs_gpu=gpu,
                                downloads=(Download(kind="hf", source=f"org/{bid}", size_mb=10),))
        self.sample_rate = sr
        self.stochastic = stochastic
        self.ready = True
        self.calls: list[tuple] = []
        self.loaded = 0
        self.native = native_speed
        self.spc = seconds_per_char
        self.bad_first = bad_first

    def is_ready(self):
        return self.ready

    def voices(self):
        return [BackendVoice(id=f"{self.info.id}:a", name="A", language="chinese", gender="f")]

    def load(self):
        self.loaded += 1

    def unload(self):
        self.loaded = 0

    def generate(self, text, voice, language, *, seed=0, speed=1.0, context=None):
        self.calls.append((text, voice, language, seed, speed, dict(context or {})))
        secs = len(text) * self.spc
        if self.native and context is not None:
            context["native_speed"] = True
            secs /= speed
        if len(self.calls) <= self.bad_first:
            secs *= 0.2                                   # ends early
        return _tone(secs, self.sample_rate)


@pytest.fixture
def fakes(monkeypatch):
    made = {}

    def make(bid, **kw):
        made[bid] = FakeBackend(bid, **kw)
        return made[bid]

    monkeypatch.setattr(speech, "BACKEND_ORDER", ("gpu1", "cpu1", "gpu2"))
    monkeypatch.setattr(speech, "_BACKENDS", {})
    monkeypatch.setattr(speech, "_make", lambda bid: made[bid])
    make("gpu1", gpu=True, sr=48000)
    make("cpu1", stochastic=False, clone=False, native_speed=True)
    make("gpu2", gpu=True)
    return made


TEXT = "这是第一句话，用来测试。这是第二句话，也用来测试。最后是第三句话。"


def test_routes_voice_to_its_backend_and_uses_its_sample_rate(fakes):
    r = speech.SpeechEngine().synthesize(TEXT, "gpu1:a", language="chinese", seed=1)
    assert r.sample_rate == 48000 and r.duration > 2
    assert fakes["gpu1"].calls and not fakes["cpu1"].calls


def test_unknown_voice_and_empty_text(fakes):
    e = speech.SpeechEngine()
    with pytest.raises(ValueError):
        e.synthesize(TEXT, "nope:a")
    with pytest.raises(ValueError):
        e.synthesize(TEXT, "gpu1:missing")
    with pytest.raises(ValueError):
        e.synthesize("  ", "gpu1:a")


def test_clone_goes_to_the_chosen_clone_backend(fakes):
    prompt = tts.ClonePrompt(_tone(5, 24000), "hello", "me")
    speech.SpeechEngine().synthesize(TEXT, prompt, language="chinese", seed=1, clone_backend="gpu2")
    assert fakes["gpu2"].calls and fakes["gpu2"].calls[0][1] is prompt


def test_missing_model_is_reported_with_its_source(fakes):
    fakes["gpu1"].ready = False
    with pytest.raises(tts.TtsModelMissing) as ei:
        speech.SpeechEngine().synthesize(TEXT, "gpu1:a")
    assert ei.value.repo == "org/gpu1"


def test_only_one_gpu_backend_stays_loaded(fakes):
    e = speech.SpeechEngine()
    e.synthesize(TEXT, "gpu1:a", seed=1)
    e.synthesize(TEXT, "cpu1:a", seed=1)
    prompt = tts.ClonePrompt(_tone(5, 24000), "hi", "me")
    e.synthesize(TEXT, prompt, seed=1, clone_backend="gpu2")
    assert fakes["gpu1"].loaded == 0 and fakes["gpu2"].loaded > 0 and fakes["cpu1"].loaded > 0


def test_context_is_shared_across_pieces_of_one_synthesis(fakes):
    b = fakes["gpu1"]
    orig = b.generate

    def gen(text, voice, language, *, seed=0, speed=1.0, context=None):
        context.setdefault("first", text)
        return orig(text, voice, language, seed=seed, speed=speed, context=context)

    b.generate = gen
    long = "。".join(["这是一句比较长的测试文字，用来确保会被切成好几段" for _ in range(8)]) + "。"
    speech.SpeechEngine().synthesize(long, "gpu1:a", language="chinese", seed=1)
    firsts = {c[5].get("first") for c in b.calls[1:]}
    assert len(b.calls) > 1 and len(firsts) == 1 and None not in firsts


def test_stochastic_backend_retries_a_piece_that_ends_early(fakes):
    b = fakes["gpu1"]
    b.bad_first = 1
    r = speech.SpeechEngine().synthesize("一句正常长度的测试句子，看看会不会重试。", "gpu1:a", language="chinese", seed=3)
    assert r.segments[0].attempts == 2 and r.segments[0].ok


def test_deterministic_backend_is_not_retried_or_flagged_for_its_pace(fakes):
    b = fakes["cpu1"]
    b.spc = 0.03                                         # speaks much faster than the estimate
    r = speech.SpeechEngine().synthesize(TEXT, "cpu1:a", language="chinese", seed=3)
    assert all(s.attempts == 1 and s.ok for s in r.segments)


def test_native_speed_is_not_stretched_twice(fakes):
    e = speech.SpeechEngine()
    normal = e.synthesize(TEXT, "cpu1:a", seed=1)
    fast = e.synthesize(TEXT, "cpu1:a", seed=1, speed=1.5)
    assert fast.duration == pytest.approx(normal.duration / 1.5, rel=0.12)
    assert fakes["cpu1"].calls[-1][4] == 1.5


def test_speed_is_time_stretched_when_the_backend_cannot(fakes):
    e = speech.SpeechEngine()
    normal = e.synthesize(TEXT, "gpu1:a", seed=1)
    fast = e.synthesize(TEXT, "gpu1:a", seed=1, speed=1.5)
    assert fast.duration == pytest.approx(normal.duration / 1.5, rel=0.12)
    assert fakes["gpu1"].calls[-1][4] == 1.5


def test_cancel_between_pieces(fakes):
    cancel = threading.Event()
    long = "。".join(["这是一句比较长的测试文字，用来确保会被切成好几段" for _ in range(8)]) + "。"

    def progress(i, n, s):
        if i == 1:
            cancel.set()

    with pytest.raises(tts.TtsCancelled):
        speech.SpeechEngine().synthesize(long, "gpu1:a", seed=1, progress=progress, cancel=cancel)


def test_verifier_sees_24k_audio_whatever_the_backend_rate(fakes):
    seen = []
    speech.SpeechEngine().synthesize(TEXT, "gpu1:a", language="chinese", seed=1,
                                     verifier=lambda a, t, lang: seen.append(len(a)) or 0.0)
    assert seen and all(n > 0 for n in seen)
    assert fakes["gpu1"].sample_rate == 48000


def test_download_backend_reports_combined_progress(fakes, monkeypatch):
    from thundertalk.core import models
    info = BackendInfo(id="x", name="x", blurb_en="", blurb_zh="", languages=(), supports_presets=True,
                       supports_clone=False, needs_gpu=False,
                       downloads=(Download("hf", "org/a", size_mb=300), Download("hf", "org/b", size_mb=100)))
    got = []

    def fake_repo(repo, ready, cb, cancel):
        cb(50, "half")
        cb(100, "done")

    monkeypatch.setattr(models, "download_repo", fake_repo)
    speech.download_backend(info, lambda p, m: got.append(p))
    assert got == sorted(got) and got[0] == 37 and got[-1] == 100      # 50 % of the 300 MB part = 37 % overall


def test_fast_speaker_is_not_retried_when_the_read_back_is_correct(fakes):
    b = fakes["gpu1"]
    b.spc = 0.24 * 0.6                                  # 40 % faster than the estimate
    r = speech.SpeechEngine().synthesize(TEXT, "gpu1:a", language="chinese", seed=1,
                                         verifier=lambda a, t, lang: 0.0)
    assert all(s.attempts == 1 and s.ok for s in r.segments)


def test_fast_take_is_still_retried_without_a_verifier(fakes):
    b = fakes["gpu1"]
    b.spc = 0.24 * 0.6
    r = speech.SpeechEngine().synthesize("一句正常长度的测试句子，看看会不会重试。", "gpu1:a", language="chinese", seed=1)
    assert r.segments[0].attempts > 1


def test_short_take_that_reads_back_wrong_is_retried(fakes):
    b = fakes["gpu1"]
    b.spc = 0.24 * 0.6
    calls = []

    def verifier(a, t, lang):
        calls.append(1)
        return 0.5 if len(calls) == 1 else 0.0              # first take lost its ending

    r = speech.SpeechEngine().synthesize("一句正常长度的测试句子，看看会不会重试。", "gpu1:a", language="chinese",
                                         seed=1, verifier=verifier)
    assert r.segments[0].attempts == 2 and r.segments[0].ok
