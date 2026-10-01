"""VoxCPM2 backend: contract, voice consistency logic, reference cache.

Unit tests use a fake model that records its calls; one integration test runs
the real 8-bit MLX model when it is in the HF cache."""

from __future__ import annotations

import os

import numpy as np
import pytest

from thundertalk.core.tts import SR as CLONE_SR
from thundertalk.core.tts import ClonePrompt, TtsModelMissing
from thundertalk.core.tts_backends import voxcpm2 as vx
from thundertalk.core.tts_backends.base import TtsBackend


class _Result:
    def __init__(self, audio):
        self.audio = audio


class FakeModel:
    """Returns a 220 Hz tone whose length follows the text length."""
    sample_rate = vx.SR

    def __init__(self):
        self.calls: list[dict] = []

    def generate(self, text, **kw):
        self.calls.append({"text": text, **kw})
        n = int(vx.SR * vx._expected_s(text))
        t = np.arange(n) / vx.SR
        yield _Result((0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))


@pytest.fixture
def backend(tmp_path):
    b = vx.VoxCPM2Backend(cache_dir=tmp_path / "vc", use_shipped=False)
    b._model = FakeModel()
    return b


def test_contract_and_info():
    b = vx.VoxCPM2Backend()
    assert isinstance(b, TtsBackend)
    assert b.sample_rate == 48000
    assert b.info.id == "voxcpm2" and b.info.supports_clone and b.info.supports_presets
    assert b.info.needs_gpu
    (d,) = b.info.downloads
    assert (d.kind, d.source, d.size_mb) == ("hf", "mlx-community/VoxCPM2-8bit", 3230)


def test_voices_unique_and_described():
    vs = vx.VoxCPM2Backend().voices()
    assert len(vs) >= 8
    ids = [v.id for v in vs]
    assert len(set(ids)) == len(ids)
    for v in vs:
        assert v.id.startswith("voxcpm2:")
        assert v.name and v.blurb_en and v.blurb_zh
        assert v.gender in ("f", "m") and v.language in ("chinese", "english", "multi")
    assert {v.language for v in vs} >= {"chinese", "english"}


def test_built_in_voices_are_the_shipped_clips_shared_with_indextts():
    from thundertalk.core.tts_backends.indextts import IndexTTSBackend
    from thundertalk.core.tts_backends.presets import load_presets
    clips = {p.slug: p for p in load_presets()}
    vs = vx.VoxCPM2Backend().voices()
    assert [v.id.split(":")[1] for v in vs] == list(clips)
    assert [v.id.split(":")[1] for v in IndexTTSBackend().voices()] == list(clips)
    for p in clips.values():
        assert p.wav.is_file() and p.text.strip() and p.description.strip()


def test_not_ready_raises(tmp_path, monkeypatch):
    b = vx.VoxCPM2Backend(cache_dir=tmp_path, repo="nobody/not-a-real-repo")
    assert not b.is_ready()
    with pytest.raises(TtsModelMissing):
        b.load()
    with pytest.raises(TtsModelMissing):
        b.generate("你好。", "voxcpm2:warm-female-zh", "chinese")


def test_designed_voice_is_pinned_to_one_reference(backend):
    m = backend._model
    ctx: dict = {}
    vid = "voxcpm2:warm-female-zh"
    a1 = backend.generate("第一段内容。", vid, "chinese", seed=3, context=ctx)
    a2 = backend.generate("第二段内容，稍微长一些。", vid, "chinese", seed=3, context=ctx)
    assert a1.dtype == np.float32 and a1.ndim == 1 and len(a2) > len(a1)

    design, piece1, piece2 = m.calls
    # the reference is spoken once from the description, in the voice's language
    assert design["instruct"] == vx._BY_ID[vid].instruct
    assert design["text"] == vx._REF_TEXT["chinese"]
    assert "ref_audio" not in design
    # every piece is an ultimate clone of that same reference
    for call, text in ((piece1, "第一段内容。"), (piece2, "第二段内容，稍微长一些。")):
        assert call["text"] == text
        assert "instruct" not in call
        assert call["prompt_text"] == vx._REF_TEXT["chinese"]
        assert call["ref_audio"] is call["prompt_audio"]
        assert call["max_tokens"] <= 2000
    assert piece1["ref_audio"] is piece2["ref_audio"]
    assert len(piece1["ref_audio"]) <= vx._MAX_REF_S * vx.SR


def test_reference_cache_survives_new_instance(tmp_path):
    cache = tmp_path / "vc"
    b1 = vx.VoxCPM2Backend(cache_dir=cache, use_shipped=False)
    b1._model = FakeModel()
    b1.generate("Hello there.", "voxcpm2:male-en", "english", context={})
    assert (cache / "male-en.wav").exists() and (cache / "male-en.json").exists()
    ref1 = b1._model.calls[1]["ref_audio"]

    b2 = vx.VoxCPM2Backend(cache_dir=cache, use_shipped=False)
    b2._model = FakeModel()
    b2.generate("Second session.", "voxcpm2:male-en", "english", context={})
    (only,) = b2._model.calls                       # no new design call
    assert "instruct" not in only
    assert only["prompt_text"].strip() == vx._REF_TEXT["english"]
    assert np.abs(only["ref_audio"] - ref1).max() < 1e-3   # PCM16 round trip


def test_changed_description_regrows_reference(tmp_path):
    cache = tmp_path / "vc"
    b = vx.VoxCPM2Backend(cache_dir=cache, use_shipped=False)
    b._model = FakeModel()
    b.generate("你好。", "voxcpm2:calm-male-zh", "chinese")
    meta = cache / "calm-male-zh.json"
    meta.write_text(meta.read_text("utf-8").replace("calm", "angry"), "utf-8")
    b2 = vx.VoxCPM2Backend(cache_dir=cache, use_shipped=False)
    b2._model = FakeModel()
    b2.generate("你好。", "voxcpm2:calm-male-zh", "chinese")
    assert "instruct" in b2._model.calls[0]


def test_clone_uses_ultimate_cloning_at_48k(backend):
    ref24 = (0.1 * np.sin(2 * np.pi * 180 * np.arange(CLONE_SR * 3) / CLONE_SR)).astype(np.float32)
    prompt = ClonePrompt(audio=ref24, text="这是我的声音。", name="me")
    ctx: dict = {}
    backend.generate("第一句。", prompt, "chinese", context=ctx)
    backend.generate("第二句。", prompt, "chinese", context=ctx)
    c1, c2 = backend._model.calls
    for c in (c1, c2):
        assert c["prompt_text"] == "这是我的声音。"
        assert "instruct" not in c
        assert c["ref_audio"] is c["prompt_audio"]
        assert abs(len(c["ref_audio"]) - 3 * vx.SR) <= 2       # resampled 24k → 48k
    assert c1["ref_audio"] is c2["ref_audio"]                   # resampled once


def test_english_prompt_gets_separator(backend):
    prompt = ClonePrompt(audio=np.zeros(CLONE_SR, np.float32), text="My voice.")
    backend.generate("Next.", prompt, "english")
    assert backend._model.calls[0]["prompt_text"] == "My voice. "


def test_unknown_voice(backend):
    with pytest.raises(ValueError):
        backend.generate("hi", "voxcpm2:nope", "english")


# ── integration (real model) ─────────────────────────────────────────────

def _median_f0(a: np.ndarray, sr: int) -> float:
    """Median F0 of voiced 40 ms frames by normalised autocorrelation (60–400 Hz)."""
    x = a[:: sr // 16000].astype(np.float64)        # 48k → 16k by decimation is fine for F0
    fs, frame, hop = 16000, 640, 320
    lo, hi = fs // 400, fs // 60
    f0s = []
    for i in range(0, len(x) - frame, hop):
        w = x[i:i + frame] - x[i:i + frame].mean()
        e = float(np.dot(w, w))
        if e < 1e-4:
            continue
        ac = np.correlate(w, w, "full")[frame - 1:]
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / e > 0.5:
            f0s.append(fs / lag)
    return float(np.median(f0s)) if len(f0s) > 10 else 0.0


@pytest.mark.skipif(not os.environ.get("TT_MODEL_TESTS") or not vx.VoxCPM2Backend().is_ready(),
                    reason="loads a 4 GB model: set TT_MODEL_TESTS=1 (and have the weights) to run")
def test_integration_two_pieces_one_voice(tmp_path):
    b = vx.VoxCPM2Backend(cache_dir=tmp_path / "vc")
    ctx: dict = {}
    vid = "voxcpm2:warm-female-zh"
    p1 = "春天来了，公园里的花都开了，空气里有淡淡的香味。"
    p2 = "孩子们在草地上奔跑，老人们坐在长椅上晒太阳，聊着家常。"
    a1 = b.generate(p1, vid, "chinese", seed=7, context=ctx)
    a2 = b.generate(p2, vid, "chinese", seed=7, context=ctx)
    for a, t in ((a1, p1), (a2, p2)):
        assert a.dtype == np.float32
        dur = len(a) / b.sample_rate
        assert 0.5 * vx._expected_s(t) < dur < 2.5 * vx._expected_s(t), dur
        assert vx._rms(a) > 0.01
    f1, f2 = _median_f0(a1, b.sample_rate), _median_f0(a2, b.sample_rate)
    assert f1 > 0 and f2 > 0
    assert abs(np.log2(f2 / f1)) < 0.25, (f1, f2)     # within ~3 semitones
    assert (tmp_path / "vc" / "warm-female-zh.wav").exists()
    b.unload()


def test_shipped_reference_is_used_before_designing(tmp_path):
    """A built-in voice uses the clip shipped with the app: no design call is made."""
    from thundertalk.core.tts_backends.presets import load_presets
    if not load_presets():
        pytest.skip("no shipped voices")
    b = vx.VoxCPM2Backend(cache_dir=tmp_path)
    d = vx._BY_ID["voxcpm2:calm-male-zh"]
    ref = b._read_shipped(d)
    assert ref is not None and len(ref[0]) > vx.SR * 3 and ref[1].startswith("你好")
    assert not list(tmp_path.iterdir())                       # nothing designed or cached
