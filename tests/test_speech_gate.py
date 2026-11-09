"""Silero VAD gate: noise never reaches the decoder, speech always does."""

from pathlib import Path

import numpy as np
import pytest

from thundertalk.core import speech_gate
from thundertalk.core.audio_io import decode_audio
from thundertalk.core.dictation import recover_final

pytestmark = pytest.mark.real_vad
SR = 16000
SPEECH = Path(__file__).parent / "fixtures/speech-zh-kokoro.wav"


def noises():
    rng = np.random.default_rng(7)
    white = rng.normal(0, .05, SR * 5).astype(np.float32)
    pink = np.cumsum(rng.normal(0, .01, SR * 5)).astype(np.float32)
    pink = (pink - np.convolve(pink, np.ones(400) / 400, "same")) * 3
    t = np.arange(SR * 5) / SR
    hum = (.05 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    keys = np.zeros(SR * 5, np.float32)          # decaying key-press bursts
    for pos in rng.integers(0, SR * 5 - 1600, 30):
        keys[pos:pos + 1600] += rng.normal(0, .05, 1600) * np.exp(-np.arange(1600) / 130)
    return {"white": white, "pink": pink.astype(np.float32), "hum": hum, "keys": keys.astype(np.float32)}


class Engine:
    current_model, active_backend, max_clip_seconds, uses_gpu = "fake", "onnx", 20, False

    def __init__(self):
        self.calls = 0

    def recognize(self, samples, sr=SR):
        from thundertalk.core.asr import AsrResult
        self.calls += 1
        return AsrResult("在生产过程中，无毒无害的食品", len(samples) / sr, 5, "fake", "onnx")


def test_bundled_model_runs():
    assert speech_gate.speech_seconds(decode_audio(str(SPEECH), SR)) > 2


@pytest.mark.parametrize("name", ["white", "pink", "hum", "keys"])
def test_noise_is_not_decoded_or_pasted(name):
    engine = Engine()
    r = recover_final(engine, noises()[name], preview="嗯。")
    assert (r.text, engine.calls) == ("", 0)


def test_speech_is_decoded():
    engine = Engine()
    r = recover_final(engine, decode_audio(str(SPEECH), SR))
    assert engine.calls == 1 and r.text


def test_speech_inside_noise_is_kept():
    x = decode_audio(str(SPEECH), SR)
    bed = noises()["white"][: len(x)] * .3
    assert speech_gate.has_speech(np.concatenate([noises()["white"][:SR], x + bed]))


def test_gate_failure_falls_back_to_energy(monkeypatch):
    monkeypatch.setattr(speech_gate, "_config", None)
    monkeypatch.setattr(speech_gate, "_unavailable", False)
    monkeypatch.setattr(speech_gate, "_model_path", lambda: "/nonexistent/silero.onnx")
    assert speech_gate.speech_seconds(noises()["white"]) is None
    assert speech_gate.has_speech(noises()["white"])           # energy decides: never swallow


def test_gate_is_cheap():
    import time
    x = np.tile(decode_audio(str(SPEECH), SR), 14)[: SR * 60]
    t0 = time.perf_counter()
    speech_gate.speech_seconds(x)
    assert time.perf_counter() - t0 < 1.5
