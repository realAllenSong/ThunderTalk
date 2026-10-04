"""File transcription: segmentation, exports and the two engine paths."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk.core import audio_io, transcribe
from thundertalk.core.transcribe import Segment, Transcript

SR = transcribe.SR


def _burst(seconds, amp=0.2, hz=150.0):
    t = np.arange(int(seconds * SR)) / SR
    env = 0.04 + 0.96 * np.sin(2 * np.pi * 3.0 * t) ** 2       # syllable-like dips
    return (amp * env * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _silence(seconds, noise=0.0008, seed=0):
    rng = np.random.default_rng(seed)
    return (noise * rng.standard_normal(int(seconds * SR))).astype(np.float32)


def _talk(pieces):
    """[(speech_seconds, pause_seconds), …] → audio"""
    out = []
    for i, (sp, pa) in enumerate(pieces):
        out += [_burst(sp), _silence(pa, seed=i)]
    return np.concatenate(out)


# ── segmentation ─────────────────────────────────────────────────────────

def test_short_audio_is_one_span():
    x = _talk([(5.0, 0.2)])
    assert transcribe.segment_speech(x) == [(0.0, pytest.approx(len(x) / SR))]


def test_silence_and_noise_yield_no_spans():
    assert transcribe.segment_speech(_silence(10.0)) == []
    assert transcribe.segment_speech(_silence(90.0)) == []


def test_steady_hum_is_not_speech():
    t = np.arange(20 * SR) / SR
    hum = (0.03 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    assert transcribe.segment_speech(hum) == []


def test_nearly_continuous_speech_is_still_speech():
    x = _talk([(12.0, 0.15)])                        # <2 % silence: p10 is speech level
    assert len(transcribe.segment_speech(x)) == 1


def test_long_audio_is_cut_at_pauses_and_covers_speech():
    x = _talk([(9.0, 0.6)] * 12)                     # ~115 s, a pause every 9.6 s
    spans = transcribe.segment_speech(x)
    assert len(spans) >= 5
    assert all(b - a <= 30.5 for a, b in spans)
    assert all(b > a for a, b in spans)
    assert all(spans[i][1] <= spans[i + 1][0] + 1e-6 for i in range(len(spans) - 1))
    # every cut should land inside a pause (silent region), not mid-speech
    for _, b in spans[:-1]:
        j = int(b * SR)
        assert np.sqrt(np.mean(x[max(0, j - 800): j + 800] ** 2)) < 0.01


def test_late_pause_never_produces_an_overlong_span():
    x = _talk([(41.0, 0.6), (9.0, 0.6), (9.0, 0.6)])       # first pause only after 41 s
    spans = transcribe.segment_speech(x)
    assert all(b - a <= 30.5 for a, b in spans), spans
    assert spans[0][0] == 0.0 and spans[-1][1] >= len(x) / SR - 1.0     # trailing silence may be dropped


def test_long_speech_without_pauses_is_still_bounded():
    x = _burst(100.0)
    spans = transcribe.segment_speech(x)
    assert all(b - a <= 30.5 for a, b in spans)
    assert spans[0][0] == 0.0 and spans[-1][1] == pytest.approx(100.0, abs=0.05)


def test_quiet_recording_is_handled_by_adaptive_threshold():
    x = _talk([(9.0, 0.6)] * 6) * 0.05
    x = x + _silence(len(x) / SR, noise=0.00005)[: len(x)]
    assert len(transcribe.segment_speech(x)) >= 2


# ── transcript model + exports ───────────────────────────────────────────

def _single():
    return Transcript([Segment(0.0, 2.5, "Hello there."), Segment(3.0, 6.2, "How are you?")],
                      duration=7.0, engine="Qwen3-ASR-0.6B", seconds_taken=0.5)


def _multi():
    t = Transcript([Segment(0.0, 2.0, "你好，", "S01"), Segment(2.0, 4.0, "今天怎么样？", "S01"),
                    Segment(4.5, 6.0, "还不错。", "S02")],
                   duration=6.0, engine="MOSS-Transcribe-Diarize", seconds_taken=1.0, has_speakers=True)
    return t


def test_realtime_factor():
    assert _single().realtime_factor == pytest.approx(14.0)
    assert Transcript([], 5.0, "x").realtime_factor == 0.0


def test_plain_text_joins_with_spaces_or_none_for_cjk():
    assert _single().to_text() == "Hello there. How are you?"
    t = Transcript([Segment(0, 1, "你好。"), Segment(1, 2, "再见。")], 2.0, "x")
    assert t.to_text() == "你好。再见。"


def test_timestamped_text():
    assert _single().to_text(timestamps=True).splitlines()[1] == "[0:03] How are you?"


def test_speaker_turns_merge_consecutive_segments():
    turns = _multi().turns()
    assert [(x.speaker, x.text) for x in turns] == [("S01", "你好，今天怎么样？"), ("S02", "还不错。")]


def test_rename_speaker_flows_into_every_export():
    t = _multi()
    t.rename_speaker("S01", "Allen")
    assert "Allen: 你好，今天怎么样？" in t.to_text()
    assert "Allen: 你好，" in t.to_srt()
    assert "<v Allen>" in t.to_vtt()
    assert "**Allen**" in t.to_markdown()
    assert json.loads(t.to_json())["speaker_names"] == {"S01": "Allen"}
    t.rename_speaker("S01", "  ")
    assert t.label("S01") == "S01"


def test_srt_format():
    srt = _single().to_srt()
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,500\nHello there.")
    assert "2\n00:00:03,000 --> 00:00:06,200\nHow are you?" in srt


def test_srt_never_emits_zero_length_cues():
    t = Transcript([Segment(1.0, 1.0, "blip")], 2.0, "x")
    assert "00:00:01,000 --> 00:00:01,500" in t.to_srt()


def test_vtt_header_and_dot_separator():
    vtt = _single().to_vtt()
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.500" in vtt


def test_json_round_trip():
    j = json.loads(_multi().to_json())
    assert j["speakers"] == ["S01", "S02"] and len(j["segments"]) == 3
    assert j["segments"][0]["text"] == "你好，"


@pytest.mark.parametrize("fmt", ["txt", "md", "srt", "vtt", "json"])
def test_export_dispatch(fmt):
    assert _multi().export(fmt).strip()


def test_time_formats():
    assert transcribe.fmt_time(65) == "1:05"
    assert transcribe.fmt_time(3725) == "1:02:05"
    assert transcribe.srt_time(3661.9996) == "01:01:02,000"     # rounds up cleanly, no 1000 ms


# ── transcription paths (fake engines) ───────────────────────────────────

class FakeEngine:
    is_loaded = True
    current_model = "fake-asr"

    def __init__(self):
        self.calls = 0

    def recognize(self, samples, sr):
        self.calls += 1
        return SimpleNamespace(text=f"chunk {self.calls}")


@pytest.fixture
def wav_file(tmp_path):
    x = _talk([(9.0, 0.6)] * 6)
    p = tmp_path / "talk.wav"
    audio_io.write_wav(str(p), x, SR)
    return str(p), len(x) / SR


def test_fast_path_segments_and_reports_speed(wav_file):
    path, dur = wav_file
    eng = FakeEngine()
    seen = []
    t = transcribe.transcribe_file(path, eng, progress=lambda p, m: seen.append(p))
    assert t.duration == pytest.approx(dur, abs=0.1)
    assert len(t.segments) == eng.calls >= 2
    assert t.engine == "fake-asr" and not t.has_speakers
    assert seen[0] <= seen[-1] == 100 and seen == sorted(seen)
    assert t.realtime_factor > 1
    starts = [s.start for s in t.segments]
    assert starts == sorted(starts)


def test_no_model_and_no_speech_errors(tmp_path, wav_file):
    path, _ = wav_file
    with pytest.raises(RuntimeError, match="no_model"):
        transcribe.transcribe_file(path, SimpleNamespace(is_loaded=False))
    quiet = tmp_path / "quiet.wav"
    audio_io.write_wav(str(quiet), _silence(20.0), SR)
    with pytest.raises(RuntimeError, match="no_speech"):
        transcribe.transcribe_file(str(quiet), FakeEngine())


def test_cancel_is_honoured_between_segments(wav_file):
    path, _ = wav_file
    ev = threading.Event()
    eng = FakeEngine()
    real = eng.recognize

    def recognize(samples, sr):
        r = real(samples, sr)
        ev.set()
        return r

    eng.recognize = recognize
    with pytest.raises(transcribe.TranscribeCancelled):
        transcribe.transcribe_file(path, eng, cancel=ev)
    assert eng.calls == 1


def test_undecodable_file_raises_typed_error(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"nope" * 100)
    with pytest.raises(audio_io.AudioDecodeError):
        transcribe.transcribe_file(str(bad), FakeEngine())


def test_speaker_path_uses_moss(monkeypatch, wav_file):
    from thundertalk.core import diarize
    path, _ = wav_file
    monkeypatch.setattr(diarize, "load_model", lambda: object())
    monkeypatch.setattr(diarize, "transcribe", lambda x, **kw: [
        SimpleNamespace(start=0.0, end=3.0, speaker="S01", text="hi"),
        SimpleNamespace(start=3.0, end=5.0, speaker="S02", text="hello")])
    t = transcribe.transcribe_file(path, None, speakers=True)
    assert t.has_speakers and t.speakers == ["S01", "S02"]
    assert t.engine == "MOSS-Transcribe-Diarize"


def test_moss_token_budget_scales_with_length():
    from thundertalk.core import diarize
    assert diarize.max_tokens_for(np.zeros(SR * 30, np.float32)) == 2048          # short: library default
    eight_min = diarize.max_tokens_for(np.zeros(SR * 518, np.float32))
    assert eight_min > 518 * 6 * 2                                               # ~6 tok/s observed; 2x headroom
    assert diarize.max_tokens_for(np.zeros(SR * 3600 * 3, np.float32)) == 120_000  # capped
