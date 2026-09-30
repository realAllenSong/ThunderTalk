"""Kokoro (sherpa-onnx) TTS backend."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from thundertalk.core.tts_backends.kokoro import (
    MODEL_URL,
    KokoroBackend,
    all_voice_ids,
)

SCRATCH_MODEL = KokoroBackend().model_dir        # the app's own download (~/.thundertalk/models)


# ── unit (no model needed) ────────────────────────────────────────────────

def test_info_fields():
    info = KokoroBackend.info
    assert info.id == "kokoro"
    assert info.supports_clone is False
    assert info.supports_presets is True
    assert info.needs_gpu is False
    assert info.languages == ("chinese", "english")
    assert len(info.downloads) == 1
    d = info.downloads[0]
    assert (d.kind, d.source, d.dest, d.size_mb) == ("tar", MODEL_URL, "kokoro-multi-lang-v1_1", 364)
    assert info.size_mb == 364
    assert KokoroBackend.sample_rate == 24000


def test_voice_list_shape():
    voices = KokoroBackend(model_dir=Path("/nonexistent")).voices()
    assert len(voices) == 23
    ids = [v.id for v in voices]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"kokoro:\d+", i) for i in ids)
    en = [v for v in voices if v.language == "english"]
    zf = [v for v in voices if v.language == "chinese" and v.gender == "f"]
    zm = [v for v in voices if v.language == "chinese" and v.gender == "m"]
    assert [v.id for v in en] == ["kokoro:0", "kokoro:1", "kokoro:2"]
    assert [v.name for v in en] == ["Maple", "Sol", "Vale"]
    assert len(zf) == 10 and len(zm) == 10
    assert all(3 <= int(v.id.split(":")[1]) <= 57 for v in zf)
    assert all(58 <= int(v.id.split(":")[1]) <= 102 for v in zm)
    assert zf[0].id == "kokoro:3" and zf[-1].id == "kokoro:57"
    assert zm[0].id == "kokoro:58" and zm[-1].id == "kokoro:102"
    for v in voices:
        assert v.blurb_en and v.blurb_zh


def test_all_voice_ids():
    ids = all_voice_ids()
    assert len(ids) == 103 and len(set(ids)) == 103
    assert ids[0] == "kokoro:0" and ids[-1] == "kokoro:102"
    assert {v.id for v in KokoroBackend().voices()} <= set(ids)


def test_clone_rejected():
    from thundertalk.core.tts import ClonePrompt
    b = KokoroBackend(model_dir=Path("/nonexistent"))
    prompt = ClonePrompt(audio=np.zeros(24000, dtype=np.float32), text="hi")
    with pytest.raises(ValueError):
        b.generate("你好", prompt, "chinese")


@pytest.mark.parametrize("bad", ["kokoro:103", "kokoro:-1", "voxcpm2:0", "kokoro:x", ""])
def test_bad_voice_id_rejected(bad):
    with pytest.raises(ValueError):
        KokoroBackend(model_dir=Path("/nonexistent")).generate("hi", bad, "english")


def test_not_ready_raises_model_missing(tmp_path):
    from thundertalk.core.tts import TtsModelMissing
    b = KokoroBackend(model_dir=tmp_path)
    assert not b.is_ready()
    with pytest.raises(TtsModelMissing) as ei:
        b.load()
    assert ei.value.repo == MODEL_URL
    with pytest.raises(TtsModelMissing):
        b.generate("你好", "kokoro:3", "chinese")


def test_default_model_dir_is_under_models_dir():
    from thundertalk.core.models import get_models_dir
    assert KokoroBackend().model_dir == get_models_dir() / "kokoro-multi-lang-v1_1"


# ── integration (needs the extracted model) ───────────────────────────────

needs_model = pytest.mark.skipif(not (SCRATCH_MODEL / "model.onnx").is_file(),
                                 reason="Kokoro model not downloaded")


@pytest.fixture(scope="module")
def backend():
    b = KokoroBackend(model_dir=SCRATCH_MODEL)
    b.load()
    yield b
    b.unload()


def _check(audio: np.ndarray, lo_s: float, hi_s: float) -> float:
    assert audio.dtype == np.float32 and audio.ndim == 1
    dur = len(audio) / 24000
    assert lo_s <= dur <= hi_s, dur
    assert float(np.sqrt(np.mean(audio ** 2))) > 0.01      # not silent
    return dur


@needs_model
def test_generate_chinese(backend):
    assert backend.is_ready() and backend.sample_rate == 24000
    text = "今天天气很好，我们一起去公园散步吧。"            # 16 chars (excl. punctuation)
    ctx: dict = {}
    audio = backend.generate(text, "kokoro:3", "chinese", context=ctx)
    assert ctx["native_speed"] is True
    _check(audio, 16 * 0.1, 16 * 0.4)


@needs_model
def test_generate_english(backend):
    text = "The quick brown fox jumps over the lazy dog near the river."   # 12 words
    _check(backend.generate(text, "kokoro:0", "english"), 12 * 0.2, 12 * 0.6)


@needs_model
def test_generate_mixed(backend):
    text = "我今天用 Python 写了一个 demo，效果很好。"
    # ~12 Chinese chars + 2 English words
    _check(backend.generate(text, "kokoro:60", "chinese"), 12 * 0.1 + 2 * 0.2, 12 * 0.4 + 2 * 0.6)


@needs_model
def test_speed_is_native(backend):
    text = "这是一个用来测试语速的句子，应该明显变快。"
    normal = backend.generate(text, "kokoro:20", "chinese", speed=1.0)
    fast = backend.generate(text, "kokoro:20", "chinese", speed=1.5)
    assert len(fast) < 0.8 * len(normal)
