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


# ---- sung lyrics and the pronunciation helper ------------------------------

def test_shown_euler_song_is_sung_as_euler_song_in_characters():
    assert ml.sung_line("欧拉SONG", "zh", user={}) == "欧拉宋"
    # the check later accepts the homophone the model actually sang
    assert ml.check_line(0, "欧拉颂", [ml.sung_line("欧拉SONG", "zh", user={}), "欧拉SONG"],
                         "zh").status == ml.CLEAR


def test_builtin_table_then_letters_then_syllables():
    assert ml.suggest("AI", "zh", user={}) == ml.Suggestion("诶艾", ml.BUILTIN)
    assert ml.suggest("hello", "zh", user={}).text == "哈喽"
    assert ml.suggest("GPU", "zh", user={}) == ml.Suggestion("吉屁优", ml.RULE)
    assert ml.transliterate("sola") == "索拉"
    assert ml.transliterate("song") == "宋"
    assert ml.transliterate("world") == "沃尔德"
    assert ml.transliterate("string") == "斯特林"


def test_user_dictionary_wins_and_is_saved(tmp_path):
    path = tmp_path / "music" / "pronunciations.json"
    ml.remember("Song", "颂", path)
    user = ml.load_user_dict(path)
    assert user == {"song": "颂"}
    assert ml.suggest("SONG", "zh", user) == ml.Suggestion("颂", ml.USER)
    ml.remember("song", "", path)
    assert ml.load_user_dict(path) == {}
    path.write_text("not json", "utf-8")
    assert ml.load_user_dict(path) == {}


def test_lines_without_chinese_and_non_chinese_songs_are_kept():
    assert ml.sung_line("Hello my love", "zh", user={}) == "Hello my love"
    assert ml.sung_line("欧拉SONG", "en", user={}) == "欧拉SONG"
    assert ml.suggest_sung("song", "en", user={}) == "song"
    assert ml.latin_words("我爱 Python 和 AI") == ["Python", "AI"]
    assert ml.latin_words("all latin") == []


def test_shown_and_sung_round_trip_keeps_tags_and_line_order():
    lyrics = "[verse]\n欧拉SONG\n\n普通的一行\n[chorus]\nHello 世界\n"
    pairs = ml.sung_pairs(lyrics, "zh", overrides={"Hello 世界": "哈罗世界"}, user={})
    assert [(ln.index, ln.text, sung) for ln, sung in pairs] == [
        (0, "欧拉SONG", "欧拉宋"), (1, "普通的一行", "普通的一行"), (2, "Hello 世界", "哈罗世界")]
    sung = ml.sung_lyrics(lyrics, "zh", overrides={"Hello 世界": "哈罗世界"}, user={})
    assert sung == "[verse]\n欧拉宋\n\n普通的一行\n[chorus]\n哈罗世界"
    assert [ln.section for ln in ml.lyric_lines(sung)] == [ln.section for ln, _ in pairs]
    assert [ln.index for ln in ml.lyric_lines(sung)] == [0, 1, 2]


def test_sentence_timestamps_map_to_lines_only_one_to_one():
    stamps = [{"text": "[verse]", "start": 0, "end": 0.3}, {"text": "第一行", "start": 1, "end": 3},
              {"text": "第二行", "start": 3.2, "end": 5}]
    assert ml.line_spans(stamps, 2) == [(1.0, 3.0), (3.2, 5.0)]
    assert ml.line_spans(stamps, 3) is None
    assert ml.line_spans(None, 2) is None
    assert ml.line_spans([{"text": "x", "start": 4, "end": 2}], 1) is None
