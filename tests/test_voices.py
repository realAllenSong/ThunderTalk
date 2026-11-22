"""Saved cloning voices and reference preparation."""

from __future__ import annotations

import numpy as np
import pytest

from thundertalk.core import voices
from thundertalk.core.tts import SR


def _speechlike(seconds, sr=SR, amp=0.2, hz=140.0):
    t = np.arange(int(seconds * sr)) / sr
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t)          # syllable-ish modulation
    return (amp * env * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _padded(x, lead=0.8, tail=1.0, sr=SR):
    return np.concatenate([np.zeros(int(lead * sr), np.float32), x, np.zeros(int(tail * sr), np.float32)])


def test_prepare_trims_silence_and_resamples():
    raw = _padded(_speechlike(6.0, 16000), sr=16000)
    chk = voices.prepare_reference(raw, 16000)
    assert chk.ok
    assert 5.8 < chk.duration < 7.0
    assert len(chk.audio) == pytest.approx(chk.duration * SR, abs=2)
    assert "short" not in chk.warnings


def test_prepare_flags_short_and_rejects_too_short():
    ok_short = voices.prepare_reference(_speechlike(3.5), SR)
    assert ok_short.ok and "short" in ok_short.warnings
    too_short = voices.prepare_reference(_speechlike(1.5), SR)
    assert not too_short.ok


def test_prepare_rejects_near_silence():
    chk = voices.prepare_reference(_speechlike(6.0, amp=0.0008), SR)
    assert "too_quiet" in chk.warnings and not chk.ok


def test_prepare_warns_on_clipping():
    x = np.clip(_speechlike(6.0, amp=1.5), -1.0, 1.0)
    assert "clipping" in voices.prepare_reference(x, SR).warnings


def test_prepare_cuts_long_reference_at_a_pause():
    a = _speechlike(14.0)
    gap = np.zeros(int(0.5 * SR), np.float32)
    x = np.concatenate([a, gap, _speechlike(14.0)])
    chk = voices.prepare_reference(x, SR)
    assert "trimmed_long" in chk.warnings
    assert chk.duration <= voices.MAX_REF_SECONDS + 0.01
    assert chk.duration > voices.MAX_REF_SECONDS - 3.5


def test_prepare_normalises_level_without_clipping():
    quiet = voices.prepare_reference(_speechlike(6.0, amp=0.02), SR).audio
    loud = voices.prepare_reference(_speechlike(6.0, amp=0.6), SR).audio
    assert np.max(np.abs(quiet)) <= 0.951 and np.max(np.abs(loud)) <= 0.951
    ratio = np.sqrt(np.mean(quiet ** 2)) / np.sqrt(np.mean(loud ** 2))
    assert 0.6 < ratio < 1.6


def test_prepare_accepts_stereo():
    st = np.stack([_speechlike(5.0), _speechlike(5.0)], axis=1)
    assert voices.prepare_reference(st, SR).ok


# ── library ──────────────────────────────────────────────────────────────

def test_library_lifecycle(isolated_home):
    lib = voices.VoiceLibrary()
    assert lib.list() == []
    ref = voices.prepare_reference(_speechlike(6.0), SR).audio
    v = lib.add("My Voice", ref, "  hello there  ")
    assert v.id == "my-voice" and v.ref_text == "hello there"
    assert (isolated_home / ".thundertalk" / "voices" / "my-voice" / "ref.wav").is_file()

    again = lib.add("My Voice", ref, "second")
    assert again.id == "my-voice-2"                   # no overwrite
    assert [x.id for x in lib.list()] == ["my-voice", "my-voice-2"]

    lib.update_text("my-voice", "new words")
    lib.rename("my-voice", "Renamed")
    got = lib.get("my-voice")
    assert got.ref_text == "new words" and got.name == "Renamed"

    p = lib.prompt("my-voice")
    assert p.text == "new words" and p.name == "Renamed"
    assert p.audio.dtype == np.float32 and abs(len(p.audio) - len(ref)) < 5
    assert np.max(np.abs(p.audio - ref)) < 1e-3

    lib.delete("my-voice")
    assert lib.get("my-voice") is None and len(lib.list()) == 1


def test_library_survives_a_corrupt_entry(isolated_home):
    lib = voices.VoiceLibrary()
    lib.add("good", voices.prepare_reference(_speechlike(5.0), SR).audio, "x")
    bad = voices.voices_dir() / "bad"
    bad.mkdir()
    (bad / "voice.json").write_text("{not json", encoding="utf-8")
    (bad / "ref.wav").write_bytes(b"")
    assert [v.id for v in lib.list()] == ["good"]


def test_prompt_for_unknown_voice_raises(isolated_home):
    with pytest.raises(KeyError):
        voices.VoiceLibrary().prompt("ghost")


def test_delete_refuses_paths_outside_the_library(isolated_home):
    outside = isolated_home / "keep"
    outside.mkdir()
    voices.VoiceLibrary().delete("../keep")
    assert outside.is_dir()


def test_non_ascii_names_get_usable_ids(isolated_home):
    v = voices.VoiceLibrary().add("我的声音", voices.prepare_reference(_speechlike(5.0), SR).audio, "你好")
    assert v.id and "/" not in v.id
    assert voices.VoiceLibrary().get(v.id).name == "我的声音"


# ── what a recording will pass on to its clones ──────────────────────────

def _spoken(seconds=6.0, sr=SR, amp=0.2):
    """Words with gaps between them and silence at both ends, like a real recording."""
    word = _speechlike(0.45, sr, amp)
    k = int(0.05 * sr)
    word[:k] *= np.linspace(0, 1, k, dtype=np.float32)
    word[-k:] *= np.linspace(1, 0, k, dtype=np.float32)
    gap = np.zeros(int(0.15 * sr), np.float32)
    n = int(seconds / 0.6)
    return _padded(np.concatenate([np.concatenate([word, gap]) for _ in range(n)]), 0.3, 0.3, sr)


def test_a_clean_recording_raises_nothing():
    assert voices.recording_issues(_spoken()) == []
    assert not {"noisy", "cut_end"} & set(voices.prepare_reference(_spoken(), SR).warnings)


def test_room_noise_is_reported_from_22_db_under_the_voice():
    rng = np.random.default_rng(0)
    x = _spoken()
    for snr, noisy in ((30, False), (26, False), (18, True), (10, True)):
        y = x + rng.normal(0, 0.2 * 0.7 / 10 ** (snr / 20), len(x)).astype(np.float32)
        assert ("noisy" in voices.recording_issues(y)) is noisy, snr


def test_a_recording_that_stops_mid_word_is_reported():
    x = _spoken()
    cut = x[: len(x) - int(0.3 * SR) - int(0.15 * SR) - int(0.2 * SR)]      # ends inside the last word
    assert voices.recording_issues(cut) == ["cut_end"]
    assert "cut_end" in voices.prepare_reference(cut, SR).warnings


def test_too_short_to_judge():
    assert voices.recording_issues(np.zeros(int(0.5 * SR), np.float32)) == []


def test_every_warning_has_words_in_both_languages():
    from thundertalk.core import i18n
    for key in ("noisy", "cut_end", "clipping", "trimmed_long", "too_quiet", "short"):
        entry = i18n._STRINGS[f"studio.clone.warn.{key}"]
        assert entry["en"].strip() and entry["zh"].strip()


# ── ways of speaking ─────────────────────────────────────────────────────

def test_a_voice_keeps_one_recording_per_way_of_speaking(isolated_home):
    lib = voices.VoiceLibrary()
    v = lib.add("Me", _speechlike(6.0), "main words")
    assert v.scenes == ()
    lib.add_scene(v.id, "chat", _speechlike(5.0, hz=200.0), "chat words")
    lib.add_scene(v.id, "mixed", _speechlike(4.0, hz=180.0), "mixed words")
    lib.add_scene(v.id, "chat", _speechlike(5.5, hz=210.0), "chat words again")          # replaces
    v = lib.get(v.id)
    assert v.scenes == ("chat", "mixed")                                                  # in SCENES order
    assert lib.prompt(v.id, "chat").text == "chat words again"
    assert lib.prompt(v.id, "main").text == "main words"
    assert lib.prompt(v.id, "story").text == "main words"                                 # not recorded: the main one
    lib.remove_scene(v.id, "chat")
    assert lib.get(v.id).scenes == ("mixed",)
    with pytest.raises(ValueError):
        lib.add_scene(v.id, "singing", _speechlike(4.0), "x")


def test_auto_uses_the_mixed_recording_only_for_mixed_text(isolated_home):
    lib = voices.VoiceLibrary()
    v = lib.add("Me", _speechlike(6.0), "main words")
    assert lib.prompt(v.id, "auto", "我用 Claude Code 写了一个工具，放到 GitHub 上").text == "main words"
    lib.add_scene(v.id, "mixed", _speechlike(4.0), "mixed words")
    assert lib.prompt(v.id, "auto", "我用 Claude Code 写了一个工具，放到 GitHub 上").text == "mixed words"
    assert lib.prompt(v.id, "auto", "今天天气不错，我们出去走走。").text == "main words"
    assert lib.prompt(v.id, "auto", "Hello there, plain English only.").text == "main words"
    assert voices.scene_for("只提到一次 iPhone 而已", ("mixed",)) is None                  # one word is not a mix


def test_every_scene_has_a_name_and_a_line_to_read_in_both_languages():
    from thundertalk.core import i18n
    for s in voices.SCENES:
        for key in (f"studio.scene.{s}", f"studio.scene.line.{s}"):
            assert i18n._STRINGS[key]["en"].strip() and i18n._STRINGS[key]["zh"].strip()
