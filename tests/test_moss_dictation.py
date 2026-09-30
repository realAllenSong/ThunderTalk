"""MOSS dictation on silence and non-speech: the raw outputs below are real
ones from dictation history (clips of 0.1–5 s with nobody speaking)."""

from __future__ import annotations

import numpy as np
import pytest

from thundertalk.core import diarize
from thundertalk.core.asr import AsrEngine


LOOP = "[0.00][S01][0.06][S02][0.12][S01][0.18][S02][0.24][S01][0.30][S02][0.36][S01][0."


@pytest.mark.parametrize("raw", [
    LOOP,
    "[0.00][S01][0.86]",
    "[0.00][S01][0.06][2.92][S01][3.18]",
    "[1.86][1.86][S02][2.02][2.02][S01][2.18][2.18][S02][2.34]",
    "[7.30][7.30][S02] [7.36][7.36][S01] [7.42]",
    "",
])
def test_markers_without_words_are_not_speech(raw):
    assert diarize.parse_transcript(raw) == []


def test_real_speech_still_parses():
    segs = diarize.parse_transcript("[0.07][S01]Hello everyone.[2.68][2.82][S02]大家好。[6.62]")
    assert [(s.speaker, s.text) for s in segs] == [("S01", "Hello everyone."), ("S02", "大家好。")]
    assert diarize.parse_transcript("plain text")[0].text == "plain text"


def test_dictation_budget_is_tight_but_fits_fast_speech():
    assert diarize.dictation_max_tokens(1.0) < 200            # a loop stops within ~1 s
    assert diarize.dictation_max_tokens(1.0) >= 2 * 21        # 1 s clips need ≤ 21 tokens
    assert diarize.dictation_max_tokens(10.0) >= 2 * 14 * 10  # peak ~14 tokens/s


@pytest.mark.parametrize("raw, expected", [
    ("[clear throat]", ""),
    ("[sniff]", ""),
    ("[Clapping]", ""),
    ("[0.00][S01][clear throat][1.20]", ""),
    ("[0.00][S01]好的[sniff]，知道了。[2.10]", "好的，知道了。"),
    (LOOP, ""),
])
def test_dictation_output_drops_non_speech(monkeypatch, raw, expected):
    seen = {}

    def fake_transcribe(audio, max_tokens=None):
        seen["max_tokens"] = max_tokens
        return diarize.parse_transcript(raw)

    monkeypatch.setattr(diarize, "transcribe", fake_transcribe)
    eng = AsrEngine()
    eng._moss_model = object()
    audio = (0.05 * np.sin(np.linspace(0, 400, 16000))).astype(np.float32)
    res = eng.recognize(audio, 16000)
    assert res.text == expected
    assert seen["max_tokens"] == diarize.dictation_max_tokens(1.0)
