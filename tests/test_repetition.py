"""Repetition-loop detection: real loops are caught, real repetition is kept."""

from __future__ import annotations

import pytest

from thundertalk.core import repetition as rep

LOOP_EN = "come on，teacher，" + "hands up，" * 300
LOOP_ZH = "今天我们学习新的单词，" + "大家跟我读，" * 120
LOOP_JA = "先生、もう一度ゆっくり言ってください。" + "ありがとう、" * 150
LOOP_MIXED = "Okay everyone 大家好 " + "hands up 举手 " * 100


# ── units / syllables ───────────────────────────────────────────────────

def test_units_split_words_and_cjk_characters():
    assert [u.text for u in rep.units("Hands up，好的! みなさん don't")] == [
        "hands", "up", "好", "的", "み", "な", "さ", "ん", "don't"]


def test_syllables_count_cjk_per_char_and_latin_by_vowels():
    assert rep.syllables("你好") == 2
    assert rep.syllables("teacher") == 2
    assert rep.syllables("hands up") == 2
    assert rep.syllables("こんにちは") == 5
    assert rep.syllables("，。! ") == 0


# ── find_runs ───────────────────────────────────────────────────────────

def test_find_runs_reports_shortest_period():
    runs = rep.find_runs("好好好好好好")
    assert len(runs) == 1 and runs[0].period == 1 and runs[0].count == 6


def test_find_runs_ignores_punctuation_and_case():
    runs = rep.find_runs("Hands up! hands up, HANDS UP. hands up")
    assert len(runs) == 1
    r = runs[0]
    assert (r.period, r.count, r.phrase) == (2, 4, "hands up")


def test_find_runs_handles_partial_last_repetition():
    text = "go " + "hands up " * 5 + "hands"
    r = rep.find_runs(text)[0]
    assert r.count == 5 and text[r.end - 5:r.end] == "hands"


def test_find_runs_sentence_level_loop():
    sent = "我们今天一起来复习上周学过的内容。"
    r = rep.find_runs(sent * 6)[0]
    assert r.count == 6 and r.period == len(rep.units(sent))


def test_count_phrase_counts_scattered_occurrences():
    text = "Hands up, hands up. 先生 hands up! One two. hands"
    assert rep.count_phrase(text, "hands up") == 3
    assert rep.count_phrase("好的好的好", "好 的") == 2


def test_no_runs_in_ordinary_text():
    assert rep.find_runs("The quick brown fox jumps over the lazy dog.") == []
    assert rep.find_runs("我想确认一下明天下午三点的会议地点。") == []


# ── degenerate vs legit ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", [LOOP_EN, LOOP_ZH, LOOP_JA, LOOP_MIXED])
def test_long_loops_are_degenerate_and_implausible(text):
    assert rep.looks_degenerate(text, 15.0)
    assert rep.is_implausible(text, 15.0)


@pytest.mark.parametrize("text,seconds", [
    ("好的好的，没问题。", 2.0),
    ("好的好的好的", 2.0),
    ("对对对对，就是这样。", 2.5),
    ("哈哈哈哈哈", 2.0),
    ("no no no, that's not what I meant", 3.0),
    ("はいはい、わかりました。", 2.0),
    ("谢谢谢谢，太感谢了", 2.5),
])
def test_everyday_repetition_is_not_even_suspicious(text, seconds):
    assert not rep.looks_degenerate(text, seconds)
    assert rep.clean(text, seconds) == (text, False)


def test_song_chorus_is_suspicious_but_never_cut():
    chorus = "Come on, teacher. " + "Hands up, " * 10 + "now sing along."
    assert rep.has_loop(chorus)                        # worth a second look…
    assert not rep.is_implausible(chorus, 12.0)        # …but 10 × "hands up" fits 12 s
    assert rep.clean(chorus, 12.0) == (chorus, False)
    zh_chorus = "我们一起唱，" + "啦啦啦，" * 8 + "真开心。"
    assert rep.clean(zh_chorus, 8.0) == (zh_chorus, False)


def test_rate_check_needs_enough_text():
    assert not rep.is_implausible("hello world", 0.1)      # too little to judge


# ── clean / collapse ────────────────────────────────────────────────────

def test_clean_cuts_loop_keeping_two_repetitions():
    out, changed = rep.clean(LOOP_EN, 12.0)
    assert changed
    assert out == "come on，teacher，hands up，hands up，"


def test_clean_keeps_text_after_a_loop_the_model_recovered_from():
    text = "大家好，" + "举手，" * 200 + "下课了。"
    out, changed = rep.clean(text, 10.0)
    assert changed and out == "大家好，举手，举手，下课了。"


@pytest.mark.parametrize("text", [LOOP_ZH, LOOP_JA, LOOP_MIXED])
def test_clean_makes_every_language_plausible(text):
    out, changed = rep.clean(text, 15.0)
    assert changed and not rep.is_implausible(out, 15.0)
    assert len(out) < len(text) / 10
    assert out.startswith(text[:6])


def test_clean_leaves_implausible_text_without_runs_alone():
    text = "".join(chr(0x4E00 + i) for i in range(400))     # no repetition at all
    assert rep.clean(text, 5.0) == (text, False)


def test_collapse_with_keep_zero_cuts_at_loop_start():
    text = "start " + "la la " * 5
    r = rep.find_runs(text)[0]
    assert rep.collapse(text, r, keep=0) == "start"
