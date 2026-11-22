"""Pauses chosen by what comes next, the pause marker, and polished output (best of several takes per sentence)."""

from __future__ import annotations

import numpy as np
import pytest

from thundertalk.core import speech, tts
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend


# ── pauses ──
def _pauses(text):
    return [round(p, 2) for _, p in tts.split_sentences(text)]


def test_the_pause_after_a_sentence_follows_what_the_next_one_does():
    text = "你知道为什么吗？因为它太便宜了。所以大家都在用。但是问题也来了。这事不简单。"
    assert _pauses(text) == [tts.PAUSE_ANSWER, tts.PAUSE_ON, tts.PAUSE_TURN, tts.PAUSE_PLAIN, tts.PAUSE_PUNCH]


def test_english_connectives_count_too_and_need_a_whole_word():
    p = _pauses("It is fast. But it is not free. Butter is cheap. So we use it.")
    assert p[0] == tts.PAUSE_TURN and p[1] == tts.PAUSE_PLAIN and p[2] == tts.PAUSE_ON


def test_a_paragraph_ends_with_a_longer_pause_and_a_short_closing_line_longer_still():
    p = _pauses("这是第一段的第一句话，说得比较长一些。这是第一段的最后一句话，也说得比较长。\n\n第二段只有这么一句比较长的话而已。")
    assert p[1] == tts.PAUSE_PARA and p[2] == tts.PAUSE_PARA
    assert _pauses("这一段先说了一句很长很长的话。就这样。")[-1] == tts.PAUSE_PUNCH
    assert _pauses("就这样。")[-1] == tts.PAUSE_PARA                       # a one-line paragraph is not a punchline


def test_neighbouring_pauses_of_one_kind_alternate():
    p = _pauses("第一句话在这里。第二句话在这里。第三句话在这里。第四句话在这里。最后一句话在这里结束。")
    assert p[:4] == [tts.PAUSE_PLAIN, tts.PAUSE_PLAIN + tts.PAUSE_STEP, tts.PAUSE_PLAIN, tts.PAUSE_PLAIN + tts.PAUSE_STEP]


def test_a_pause_marker_sets_the_pause_is_not_read_and_is_never_merged_away():
    parts = tts.split_sentences("先说第一件事。[停顿 1.2]再说第二件事。[pause 0.3s] And a third.")
    assert [s for s, _ in parts] == ["先说第一件事。", "再说第二件事。", "And a third."]
    assert parts[0][1] == 1.2 and parts[1][1] == 0.3 and getattr(parts[1][1], "forced", False)
    plan = tts.plan_segments("短句一。[pause 0.3]短句二。", "chinese")
    assert [s for s, _ in plan] == ["短句一。", "短句二。"]                    # a short pause still splits the piece
    assert "停顿" not in "".join(s for s, _ in plan)


def test_normal_pieces_still_merge_sentences_within_a_paragraph():
    plan = tts.plan_segments("你知道为什么吗？因为它太便宜了。但是问题也来了。\n\n第二段。", "chinese", 2.5)
    assert len(plan) == 2


def test_polished_pieces_are_one_sentence_each_with_tiny_fragments_joined():
    plan = tts.plan_sentences("对。这件事其实很简单。我们一步一步来看。", "chinese")
    assert [s for s, _ in plan] == ["对。这件事其实很简单。", "我们一步一步来看。"]
    long = "这是一个非常非常长的句子，" * 10 + "终于结束了。"
    assert all(len(s) <= 80 for s, _ in tts.plan_sentences(long, "chinese"))


# ── pitch and picking ──
def _voice(hz, seconds=1.5, sr=24000):
    t = np.arange(int(seconds * sr)) / sr
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    return (0.3 * env * np.sin(2 * np.pi * hz * t)).astype(np.float32)


@pytest.mark.parametrize("hz", [90.0, 140.0, 220.0, 300.0])
def test_median_pitch_is_found_within_a_fraction_of_a_semitone(hz):
    st = tts.median_semitones(_voice(hz), 24000)
    assert st == pytest.approx(12 * np.log2(hz / 55.0), abs=0.4)


def test_no_pitch_from_silence_or_noise():
    assert tts.median_semitones(np.zeros(24000, np.float32), 24000) is None
    rng = np.random.default_rng(0)
    assert tts.median_semitones(rng.normal(0, 0.1, 24000).astype(np.float32), 24000) is None


def test_picking_keeps_the_sentences_in_one_register():
    # Alone, each sentence would take its first (slightly better) take, which jumps 4 semitones up and down.
    costs = [[0.00, 0.02], [0.00, 0.02], [0.00, 0.02]]
    pitches = [[10.0, 12.0], [14.0, 12.5], [10.0, 12.0]]
    assert tts.pick_takes(costs, pitches, [0.08, 0.08]) == [1, 1, 1]
    assert tts.pick_takes(costs, pitches, [0.0, 0.0]) == [0, 0, 0]


def test_reading_correctly_outweighs_matching_the_neighbours():
    costs = [[0.0], [1.3, 0.0], [0.0]]                     # take 0 of the middle sentence misreads
    pitches = [[10.0], [10.0, 14.0], [10.0]]
    assert tts.pick_takes(costs, pitches, [0.08, 0.08]) == [0, 1, 0]


def test_unknown_pitch_costs_nothing_and_empty_input_is_fine():
    assert tts.pick_takes([[0.1, 0.0]], [[None, None]], []) == [1]
    assert tts.pick_takes([], [], []) == []


# ── polished synthesis through the engine ──
class Takes(TtsBackend):
    """Take k of each sentence is spoken at PITCH[k]; take 0 also reads the
    sentence back slightly better, so only the neighbours can make it lose."""

    PITCH = (220.0, 150.0, 155.0, 160.0)

    def __init__(self):
        self.info = BackendInfo(id="tk", name="tk", blurb_en="", blurb_zh="", languages=("chinese",),
                                supports_presets=True, supports_clone=True, needs_gpu=False,
                                downloads=(Download(kind="hf", source="org/tk", size_mb=1),))
        self.sample_rate = 24000
        self.calls = []

    def is_ready(self):
        return True

    def voices(self):
        return [BackendVoice(id="tk:a", name="A", language="chinese", gender="f")]

    def load(self):
        pass

    def generate(self, text, voice, language, *, seed=0, speed=1.0, context=None):
        k = sum(1 for c in self.calls if c == text)
        self.calls.append(text)
        x = _voice(self.PITCH[k % 4], seconds=len(text) * 0.22)
        n = int(0.05 * 24000)
        x[-n:] *= np.linspace(1, 0, n, dtype=np.float32)
        return np.concatenate([x, np.zeros(n, np.float32)])          # the word dies away, then silence


@pytest.fixture
def takes(monkeypatch):
    b = Takes()
    monkeypatch.setattr(speech, "BACKEND_ORDER", ("tk",))
    monkeypatch.setattr(speech, "_BACKENDS", {})
    monkeypatch.setattr(speech, "_make", lambda bid: b)
    return b


TEXT = "第一句话讲的是一件事情。第二句话接着往下讲。第三句话把它说完了。"


def test_polished_output_speaks_each_sentence_four_times_and_keeps_one_register(takes):
    first = []
    verifier = lambda audio, seg, lang: 0.0 if not first.append(1) and len(first) % 4 == 1 else 0.02   # noqa: E731
    r = speech.SpeechEngine().synthesize(TEXT, "tk:a", language="chinese", seed=1, verifier=verifier,
                                         params=tts.TtsParams(takes=4))
    assert [s.text for s in r.segments] == ["第一句话讲的是一件事情。", "第二句话接着往下讲。", "第三句话把它说完了。"]
    assert all(s.attempts == 4 for s in r.segments) and len(takes.calls) == 12
    st = [s.pitch_st for s in r.segments]
    assert max(st) - min(st) < 1.5                         # not the 220 Hz takes, though they read best on their own


def test_a_normal_request_is_unchanged(takes):
    r = speech.SpeechEngine().synthesize(TEXT, "tk:a", language="chinese", seed=1)
    assert len(takes.calls) == len(r.segments) == 1 and r.segments[0].pitch_st is None


def test_when_no_take_reads_correctly_two_more_are_tried(takes):
    r = speech.SpeechEngine().synthesize("这是唯一的一句话。", "tk:a", language="chinese", seed=1,
                                         verifier=lambda audio, seg, lang: 0.3, params=tts.TtsParams(takes=4))
    assert r.segments[0].attempts == 6 and not r.segments[0].ok
