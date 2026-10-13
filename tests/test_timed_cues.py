"""Subtitle cues for the CLI: ≤ ~7 s, ≤ 2 lines, in order, never overlapping."""

from __future__ import annotations

from thundertalk.cli import _one_line
from thundertalk.core import transcribe
from thundertalk.core.transcribe import Segment, Transcript, timed_cues


def _check(cues, max_s=7.0):
    assert cues
    prev_end = 0.0
    for c in cues:
        assert c.text.strip()
        assert c.start >= prev_end - 1e-9
        assert c.start < c.end <= c.start + max_s + 1e-9
        assert c.text.count("\n") <= 1
        prev_end = c.end


def _words(text_times):
    return [(a, b, w) for a, b, w in text_times]


def test_long_segment_without_word_times_is_split_by_length():
    text = ("今天我们讨论一下下个季度的计划，首先是产品发布的时间表，其次是市场推广的预算，"
            "最后是团队招聘的安排。大家有什么意见可以随时提出来，我们会后再整理一份纪要。")
    cues = timed_cues(Transcript([Segment(0.0, 24.0, text)], 24.0, "x"))
    _check(cues)
    assert len(cues) >= 4
    assert "".join(c.text.replace("\n", "") for c in cues) == text


def test_english_segment_is_split_into_short_cues():
    text = " ".join(["This is a fairly long English sentence that keeps going for a while,"] * 4)
    cues = timed_cues(Transcript([Segment(1.0, 21.0, text)], 22.0, "x"))
    _check(cues)
    for c in cues:
        assert all(len(line) <= 84 for line in c.text.split("\n"))


def test_word_times_cut_at_pauses_and_sentences():
    words = _words([(0.0, 0.4, "Hello"), (0.4, 0.8, "there."), (0.9, 1.3, "This"), (1.3, 1.6, "is"),
                    (1.6, 2.2, "a"), (2.2, 2.6, "test."), (5.0, 5.4, "After"), (5.4, 5.9, "a"),
                    (5.9, 6.5, "pause.")])
    seg = Segment(0.0, 6.5, "Hello there. This is a test. After a pause.", words=words)
    cues = timed_cues(Transcript([seg], 7.0, "x"))
    _check(cues)
    assert cues[-1].start == 5.0                        # the 2.4 s gap starts a new cue
    assert cues[-1].text == "After a pause."


def test_word_times_never_exceed_max_seconds():
    words = [(i * 0.5, i * 0.5 + 0.45, f"word{i}") for i in range(40)]
    seg = Segment(0.0, 20.0, " ".join(w[2] for w in words), words=words)
    _check(timed_cues(Transcript([seg], 20.0, "x")))


def test_overlapping_segments_become_monotonic():
    tr = Transcript([Segment(0.0, 3.0, "第一句话。"), Segment(2.5, 5.0, "第二句话。")], 5.0, "x")
    _check(timed_cues(tr))


def test_cjk_two_lines_break_at_punctuation_not_inside_a_word():
    text = "周末要不要去露营？最近天气超舒服。露营，"
    out = transcribe._two_lines(text, 32, True)
    assert out == "周末要不要去露营？\n最近天气超舒服。露营，"


def test_cjk_two_lines_never_split_embedded_latin_word():
    out = transcribe._two_lines("你好，这是蜗牛字幕测试hello from store", 32, True)
    assert out == "你好，这是蜗牛字幕测试\nhello from store"


def test_json_text_joins_lines_without_breaking_cjk():
    assert _one_line("周末要不要去露营？\n最近天气超舒服。") == "周末要不要去露营？最近天气超舒服。"
    assert _one_line("Hello there\nfriend") == "Hello there friend"
    assert _one_line("Hello there,\n朋友") == "Hello there, 朋友"
