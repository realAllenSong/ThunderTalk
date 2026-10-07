import json

import numpy as np
import soundfile as sf

from thundertalk.core import music_check, music_lyrics


def _take(tmp_path, seconds=30.0, **meta):
    wav = tmp_path / "take-1.wav"
    frames = int(seconds * 48000)
    sf.write(wav, np.zeros((frames, 2), dtype=np.float32), 48000, subtype="PCM_16")
    base = {"instrumental": False, "vocal_language": "zh", "lyrics": "", "shown_lyrics": "",
            "lyric_timestamps": None}
    base.update(meta)
    (tmp_path / "take-1.json").write_text(json.dumps(base, ensure_ascii=False))
    return wav


ZH_SHOWN = "[Verse]\n我们唱欧拉SONG\n一行公式\n原文"
ZH_SUNG = "[Verse]\n我们唱欧拉宋\n一行公式\n原文"


def test_timed_take_scores_each_line_in_its_own_window(tmp_path):
    stamps = [{"text": "[Verse]", "start": 0.0, "end": 0.5, "confidence": 0.1},
              {"text": "我们唱欧拉宋", "start": 1.0, "end": 4.0, "confidence": 0.9},
              {"text": "一行公式", "start": 5.0, "end": 7.0, "confidence": 0.9},
              {"text": "原文", "start": 29.5, "end": 30.0, "confidence": 0.9}]
    wav = _take(tmp_path, lyrics=ZH_SUNG, shown_lyrics=ZH_SHOWN, lyric_timestamps=stamps)
    replies = iter(["我们唱欧拉颂", "遗憾", ""])
    lengths, waits = [], []

    def recognise(audio):
        assert audio.dtype == np.float32 and audio.ndim == 1
        lengths.append(len(audio))
        return next(replies)

    result = music_check.check_take(wav, recognise, between=lambda: waits.append(1))
    assert result.timed and len(waits) == 3
    assert np.allclose(result.windows, [(0.6, 4.4), (4.6, 7.4), (29.1, 30.0)])
    assert [n // 1600 for n in lengths] == [38, 28, 9]
    assert [c.status for c in result.checks] == [music_lyrics.CLEAR, music_lyrics.UNCLEAR,
                                                 music_lyrics.NOT_HEARD]
    assert [line.text for line in result.lines] == ["我们唱欧拉SONG", "一行公式", "原文"]
    assert result.summary == (1, 3)


def test_shown_text_also_passes(tmp_path):
    stamps = [{"text": "我们唱欧拉宋", "start": 1.0, "end": 4.0, "confidence": 0.9},
              {"text": "一行公式", "start": 5.0, "end": 7.0, "confidence": 0.9},
              {"text": "原文", "start": 8.0, "end": 9.0, "confidence": 0.9}]
    wav = _take(tmp_path, lyrics=ZH_SUNG, shown_lyrics=ZH_SHOWN, lyric_timestamps=stamps)
    replies = iter(["我们唱欧拉song", "一行公式", "原文"])
    result = music_check.check_take(wav, lambda audio: next(replies))
    assert result.summary == (3, 3)


def test_untimed_take_scans_windows_and_aligns_to_lines(tmp_path):
    wav = _take(tmp_path, lyrics=ZH_SUNG, shown_lyrics=ZH_SHOWN)
    calls = []

    def recognise(audio):
        calls.append(len(audio))
        # the overlap repeats 公式 at the start of the second window
        return "我们唱欧拉宋一行公式" if len(calls) == 1 else "公式圆圆"

    result = music_check.check_take(wav, recognise)
    assert not result.timed
    assert result.windows == [(0.0, 15.0), (13.0, 28.0), (26.0, 30.0)]
    assert len(calls) == 3
    # 原文 → 圆圆 is flagged; homophone pinyin (yuan yuan vs yuan wen) is half wrong
    assert [c.status for c in result.checks] == [music_lyrics.CLEAR, music_lyrics.CLEAR,
                                                 music_lyrics.UNCLEAR]
    assert result.checks[2].heard == "圆圆"


def test_untimed_take_with_nothing_heard(tmp_path):
    wav = _take(tmp_path, seconds=10, lyrics="[Verse]\nhello there\nsee you", vocal_language="en")
    result = music_check.check_take(wav, lambda audio: "")
    assert [c.status for c in result.checks] == [music_lyrics.NOT_HEARD] * 2
    assert result.windows == [(0.0, 10.0)]


def test_mismatched_timestamps_fall_back_to_scanning(tmp_path):
    stamps = [{"text": "hello there", "start": 1.0, "end": 3.0, "confidence": 0.9}]
    wav = _take(tmp_path, seconds=10, lyrics="hello there\nsee you", vocal_language="en",
                lyric_timestamps=stamps)
    result = music_check.check_take(wav, lambda audio: "hello there see you")
    assert not result.timed and result.summary == (2, 2)


def test_instrumental_or_empty_takes_are_not_checked(tmp_path):
    assert music_check.check_take(_take(tmp_path, instrumental=True), lambda a: "x") is None
    assert music_check.check_take(_take(tmp_path, lyrics="[Verse]\n"), lambda a: "x") is None
