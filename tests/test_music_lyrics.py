"""Lyric check scoring, windows and the no-timestamp alignment fallback."""
from __future__ import annotations

import pytest

from thundertalk.core import music_lyrics as ml


def test_lyric_lines_skip_tags_and_blank_lines():
    lines = ml.lyric_lines("[Verse]\n第一行\n\n第二行\n[chorus]\nla la\n")
    assert [(x.index, x.text, x.section) for x in lines] == [
        (0, "第一行", "verse"), (1, "第二行", "verse"), (2, "la la", "chorus")]
    assert ml.lyric_lines("just one line")[0].section == ""


def test_chinese_units_are_toneless_pinyin_with_latin_words_kept():
    assert ml.units("欧拉SONG，你好!", "zh") == ["ou", "la", "song", "ni", "hao"]


def test_homophones_and_latin_heard_for_chinese_pass():
    assert ml.check_line(0, "我在宋歌", ["我在颂歌"], "zh").status == ml.CLEAR
    assert ml.check_line(0, "欧拉song", ["欧拉宋"], "zh").status == ml.CLEAR


@pytest.mark.parametrize("heard, sung", [("遗憾", "一行公式"), ("圆圆", "原文")])
def test_the_known_garbled_lines_are_flagged(heard, sung):
    c = ml.check_line(3, heard, [sung], "zh")
    assert c.status == ml.UNCLEAR and c.error > ml.PASS_ERROR and c.heard == heard


def test_either_sung_or_shown_text_may_match():
    shown, sung = "欧拉SONG", "欧拉宋"
    assert ml.check_line(0, "欧拉颂", [sung, shown], "zh").status == ml.CLEAR
    assert ml.check_line(0, "欧拉 song", [sung, shown], "zh").status == ml.CLEAR


def test_empty_recognition_is_not_heard():
    c = ml.check_line(1, "  ", ["walking home"], "en")
    assert c.status == ml.NOT_HEARD and c.error is None


def test_english_uses_word_error_rate():
    ok = ml.check_line(0, "walking home beneath the city light", ["Walking home beneath the city lights"], "en")
    bad = ml.check_line(0, "talking phone", ["Walking home beneath the city lights"], "en")
    assert ok.status == ml.CLEAR and ok.error == pytest.approx(1 / 6, abs=1e-3)
    assert bad.status == ml.UNCLEAR


def test_japanese_compares_characters():
    assert ml.units("さくら 咲く", "ja") == list("さくら咲く")


def test_windows():
    assert ml.line_windows([(0.2, 3.0), (58.0, 59.9)], 60.0) == [(0.0, 3.4), (57.6, 60.0)]
    assert ml.scan_windows(30.0) == [(0.0, 15.0), (13.0, 28.0), (26.0, 30.0)]
    assert ml.scan_windows(10.0) == [(0.0, 10.0)]
    assert ml.scan_windows(0) == []


def test_overlap_between_windows_is_dropped():
    assert ml.overlap_merge(["p q r s", "r s t u"], "en") == list("pqrstu")
    assert ml.overlap_merge(["p q", "x y"], "en") == list("pqxy")


def test_alignment_fallback_assigns_text_to_lines():
    lines = [ml.units(x, "zh") for x in ("我走在路上", "一行公式", "天亮了")]
    heard = ml.units("我走在路上遗憾天亮了", "zh")
    got = ml.assign(heard, lines)
    checks = [ml.judge(i, "", g, [lines[i]]) for i, g in enumerate(got)]
    assert [c.status for c in checks] == [ml.CLEAR, ml.UNCLEAR, ml.CLEAR]
    assert ml.summary(checks) == (2, 3)


def test_alignment_fallback_marks_a_skipped_line_not_heard():
    lines = [ml.units(x, "en") for x in ("walking home tonight", "under city lights", "we keep it slow")]
    got = ml.assign(ml.units("walking home tonight we keep it slow", "en"), lines)
    assert got[1] == [] and ml.judge(1, "", got[1], [lines[1]]).status == ml.NOT_HEARD
    assert ml.assign([], lines) == [[], [], []] and ml.assign(["x"], []) == []
