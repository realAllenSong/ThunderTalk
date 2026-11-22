"""LongCat backend: what fits next to a reference, and what the model is asked for (no model loaded)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk.core import speech, tts
from thundertalk.core.tts_backends import longcat as lc
from thundertalk.core.tts_backends.presets import load_presets


def test_info_and_downloads():
    b = lc.LongCatBackend()
    assert b.info.id == "longcat" and b.info.supports_clone and b.info.needs_gpu
    assert b.info.languages == ("chinese", "english") and b.sample_rate == 24000
    main, tok = b.info.downloads
    assert main.source == lc.REPO and main.required == ("config.json", "model.safetensors")
    # Only the tokenizer of umt5-base: its weights are a gigabyte nobody needs.
    assert tok.kind == "hf-dir" and tok.dest and not any("safetensors" in g or g.endswith(".bin") for g in tok.allow)


def test_it_is_registered_between_the_cloning_engines_and_kokoro():
    assert speech.BACKEND_ORDER.index("indextts") < speech.BACKEND_ORDER.index("longcat") < speech.BACKEND_ORDER.index("kokoro")
    assert isinstance(speech._make("longcat"), lc.LongCatBackend)
    assert speech.backend_id_for("longcat:whatever") == "longcat"


def test_voices_are_the_shared_presets():
    assert [v.id for v in lc.LongCatBackend().voices()] == [f"longcat:{c.slug}" for c in load_presets()]


def test_not_ready_raises_model_missing():
    b = lc.LongCatBackend(repo="nobody/nothing")
    assert not b.is_ready()
    with pytest.raises(tts.TtsModelMissing):
        b.load()


# ── length arithmetic ──
def test_the_length_estimate_is_the_models_own():
    assert lc.approx_seconds("你好世界") == pytest.approx(4 * 0.21)
    assert lc.approx_seconds("hello world") == pytest.approx(10 * 0.082)
    assert lc.approx_seconds("你好，世界。") == pytest.approx(6 * 0.21)              # punctuation counts with the majority
    assert lc.approx_seconds("用Claude写") == pytest.approx(2 * 0.21 + 6 * 0.082)   # more letters than characters


def test_a_slow_reference_stretches_the_estimate_up_to_half_again():
    assert lc.pace(4 * 0.21, "你好世界") == 1.0
    assert lc.pace(4 * 0.21 * 1.2, "你好世界") == pytest.approx(1.2)
    assert lc.pace(60.0, "你好世界") == 1.5 and lc.pace(0.1, "你好世界") == 1.0


def test_reference_and_speech_share_thirty_seconds():
    assert lc.budget_seconds(12.0) == pytest.approx(30 - 12 - lc.MARGIN_S)
    assert lc.budget_seconds(29.5) == 2.0                                          # never nothing


def test_split_to_fit_cuts_at_punctuation_near_the_middle():
    text = "第一句话说完了。第二句话也说完了。第三句话还在说。"
    parts = lc.split_to_fit(text, lambda s: len(s) <= 9)
    assert "".join(parts) == text and all(len(p) <= 9 for p in parts) and all(p[-1] == "。" for p in parts)
    assert lc.split_to_fit("short", lambda s: True) == ["short"]
    assert lc.split_to_fit("", lambda s: True) == []
    assert "".join(lc.split_to_fit("没有任何标点的一长串字", lambda s: len(s) <= 4)) == "没有任何标点的一长串字"


def _clone(seconds: float, text: str = "你好世界你好世界") -> tts.ClonePrompt:
    return tts.ClonePrompt((0.2 * np.sin(np.arange(int(24000 * seconds)) / 15)).astype(np.float32), text, "me")


def test_pieces_are_planned_shorter_for_a_longer_reference():
    b = lc.LongCatBackend()
    short, long_ = b.chunk_scale(_clone(5), "chinese", 2.5), b.chunk_scale(_clone(19), "chinese", 2.5)
    assert 0.3 <= long_ < short <= 2.5
    for ref_s, scale in ((5, short), (19, long_)):
        # the pipeline's hard limit for Chinese is 80 characters times the scale
        voice = _clone(ref_s)
        worst = int(80 * scale) * 0.21 * lc.pace(ref_s, voice.text)
        assert worst <= lc.budget_seconds(ref_s) + 0.21
    assert b.chunk_scale(_clone(5), "english", 0.4) == 0.4                          # never above what was asked


def test_the_reference_is_levelled_and_presets_resolve():
    b = lc.LongCatBackend()
    audio, text = b._reference(_clone(4))
    assert float(np.abs(audio).max()) == pytest.approx(lc.REF_PEAK, rel=1e-4) and text == "你好世界你好世界"
    c = load_presets()[0]
    audio, text = b._reference(f"longcat:{c.slug}")
    assert text == c.text and float(np.abs(audio).max()) == pytest.approx(lc.REF_PEAK, rel=1e-3)
    with pytest.raises(ValueError):
        b._reference("longcat:nope")


class _FakeModel:
    def __init__(self):
        self.calls = []

    def generate(self, **kw):
        self.calls.append(kw)
        yield SimpleNamespace(audio=np.full(2400, 0.1, dtype=np.float32))


@pytest.fixture
def loaded(monkeypatch):
    b = lc.LongCatBackend()
    b._model = _FakeModel()
    monkeypatch.setattr(b, "is_ready", lambda: True)
    return b


def test_generate_asks_for_cloning_guidance_with_the_preferred_seed(loaded):
    y = loaded.generate("今天天气不错。", _clone(5), "chinese", seed=0)
    (call,) = loaded._model.calls
    assert call["guidance_method"] == "apg" and call["steps"] == 16 and call["cfg_strength"] == 4.0
    assert call["seed"] == lc.BASE_SEED and call["ref_text"] == "你好世界你好世界" and call["text"] == "今天天气不错。"
    assert y.dtype == np.float32 and len(y) == 2400
    loaded.generate("今天天气不错。", _clone(5), "chinese", seed=7919)
    assert loaded._model.calls[-1]["seed"] == lc.BASE_SEED + 7919                   # a retry is a different take


def test_a_piece_that_would_not_fit_is_spoken_in_parts_never_cut_off(loaded):
    voice = _clone(19)                                                               # leaves 9.5 s
    text = "这一句话有十五个字的长度呢。" * 6                                           # about 17 s at once
    y = loaded.generate(text, voice, "chinese")
    said = [c["text"] for c in loaded._model.calls]
    limit = lc.budget_seconds(19) / lc.pace(19, voice.text)
    assert len(said) > 1 and "".join(said) == text and all(lc.approx_seconds(s) <= limit for s in said)
    assert len(y) == len(said) * 2400 + (len(said) - 1) * int(0.12 * 24000)         # joined with a short gap


def test_the_pipeline_asks_the_backend_how_long_a_piece_may_be(monkeypatch):
    seen = {}

    class Stub(lc.LongCatBackend):
        def is_ready(self):
            return True

        def load(self):
            pass

        def chunk_scale(self, voice, language, default):
            seen["default"] = default
            return 0.3

        def generate(self, text, voice, language, *, seed=0, speed=1.0, context=None):
            seen.setdefault("pieces", []).append(text)
            return np.full(int(24000 * max(1.0, len(text) * 0.21)), 0.1, dtype=np.float32)

    eng = speech.SpeechEngine()
    text = "这是一个用来检查分段的句子，它有一定的长度。" * 4
    eng._synthesize(Stub(), text, f"longcat:{load_presets()[0].slug}", "chinese", 1.0, None, 1, None, None, None)
    assert seen["default"] == tts.TtsParams().chunk_scale
    assert len(seen["pieces"]) >= 4 and max(len(p) for p in seen["pieces"]) <= int(80 * 0.3) + 1


def test_cli_and_cloned_voices_know_the_engine():
    from thundertalk import cli
    assert "longcat" in cli.TTS_ENGINES and "longcat" in cli.CLONE_ENGINES and "longcat" in cli.TTS_QUALITY
    assert cli.TTS_QUALITY.index("voxcpm2") < cli.TTS_QUALITY.index("longcat") < cli.TTS_QUALITY.index("kokoro")
