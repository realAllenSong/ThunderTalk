"""IndexTTS backend and the shipped preset voices (no model loaded)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from thundertalk.core import tts
from thundertalk.core.tts_backends import indextts as ix
from thundertalk.core.tts_backends.presets import load_presets, presets_dir


def test_info_and_downloads():
    b = ix.IndexTTSBackend()
    assert b.info.id == "indextts" and b.info.supports_clone and b.info.needs_gpu
    assert {d.source for d in b.info.downloads} == {ix.REPO, ix.W2V_REPO}
    assert b.sample_rate == 22050


def test_presets_ship_with_transcripts_and_audio():
    items = json.loads((presets_dir() / "voices.json").read_text("utf-8"))
    clips = load_presets()
    assert len(clips) == len(items) >= 6
    assert {c.language for c in clips} >= {"chinese", "english"} and {c.gender for c in clips} == {"f", "m"}
    for c in clips:
        assert c.wav.is_file() and c.text.strip() and c.description


def test_voices_are_the_shared_presets():
    ids = [v.id for v in ix.IndexTTSBackend().voices()]
    assert ids == [f"indextts:{c.slug}" for c in load_presets()]


def test_preset_reference_resolves_to_the_shipped_clip():
    c = load_presets()[0]
    assert ix.IndexTTSBackend()._reference_path(f"indextts:{c.slug}") == c.wav
    with pytest.raises(ValueError):
        ix.IndexTTSBackend()._reference_path("indextts:nope")


def test_clone_prompt_maps_to_one_stable_file(tmp_path):
    b = ix.IndexTTSBackend()
    b._tmp = tmp_path
    a = (0.1 * np.sin(np.arange(24000 * 4) / 20)).astype(np.float32)
    p1 = b._reference_path(tts.ClonePrompt(a, "hi", "me"))
    p2 = b._reference_path(tts.ClonePrompt(a.copy(), "hi", "me"))
    p3 = b._reference_path(tts.ClonePrompt(a * 0.5, "hi", "me"))
    assert p1 == p2 != p3 and p1.is_file()


def test_not_ready_raises_model_missing(monkeypatch):
    b = ix.IndexTTSBackend(repo="nobody/nothing")
    assert not b.is_ready()
    with pytest.raises(tts.TtsModelMissing):
        b.load()
