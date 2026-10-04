"""TTS text handling and signal helpers — everything that decides how a passage
is cut up, capped and stitched, with no model involved."""

from __future__ import annotations

import types

import numpy as np
import pytest

from thundertalk.core import tts, tts_verify


# ── language detection ───────────────────────────────────────────────────

@pytest.mark.parametrize("text,lang", [
    ("Hello, this is a test of the emergency broadcast system.", "english"),
    ("你好，今天天气很好，我们一起去公园散步吧。", "chinese"),
    ("こんにちは、今日はいい天気ですね。", "japanese"),
    ("안녕하세요, 오늘 날씨가 정말 좋네요.", "korean"),
    ("Привет, как твои дела сегодня?", "russian"),
    ("Der Hund ist nicht in dem Haus und ich bin mit ihm auf der Straße.", "german"),
    ("Le chat est dans la maison et je ne sais pas pour quoi il est là.", "french"),
    ("", "english"),
])
def test_detect_language(text, lang):
    assert tts.detect_language(text) == lang


def test_detect_language_mixed_prefers_dominant_script():
    assert tts.detect_language("请打开 Bluetooth 设置，然后点击 Pair 按钮连接你的耳机。") == "chinese"
    assert tts.detect_language("Please open the 设置 menu and then tap the pair button on your headset.") == "english"


# ── sentence splitting ───────────────────────────────────────────────────

def _sentences(text):
    return [s for s, _ in tts.split_sentences(text)]


def test_split_keeps_abbreviations_and_decimals():
    got = _sentences("Dr. Smith paid 3.14 dollars. He left at 5 p.m. sharp! Was that odd? Yes.")
    assert got[0] == "Dr. Smith paid 3.14 dollars."
    assert len(got) == 4


def test_split_keeps_filenames_and_initials():
    got = _sentences("Open notes.txt now. J. R. Tolkien wrote it.")
    assert got == ["Open notes.txt now.", "J. R. Tolkien wrote it."]


def test_split_chinese_and_closing_quotes():
    got = _sentences("他说：“我们走吧。”然后就离开了。真的吗？")
    assert got == ["他说：“我们走吧。”", "然后就离开了。", "真的吗？"]


def test_blank_line_is_a_longer_pause():
    parts = tts.split_sentences("First paragraph here.\n\nSecond paragraph here.")
    assert [s for s, _ in parts] == ["First paragraph here.", "Second paragraph here."]
    assert parts[0][1] > 0.5           # paragraph break
    assert tts.split_sentences("One. Two.")[0][1] < 0.5


def test_unpunctuated_tail_is_kept():
    assert _sentences("Hello there. and then nothing") == ["Hello there.", "and then nothing"]


def test_split_empty():
    assert tts.split_sentences("  \n \n ") == []


# ── planning ─────────────────────────────────────────────────────────────

def test_plan_merges_short_fragments():
    plan = tts.plan_segments("Yes. No. Maybe. Fine.", "english")
    assert len(plan) == 1
    assert plan[0][0] == "Yes. No. Maybe. Fine."


def test_plan_splits_overlong_sentence_at_clauses():
    long = ("This sentence goes on and on, " * 12).strip()
    plan = tts.plan_segments(long, "english")
    assert len(plan) > 1
    assert all(len(p) <= 200 for p, _ in plan)
    assert " ".join(p for p, _ in plan).replace("  ", " ") == long


def test_plan_chinese_has_tighter_limits_than_english():
    zh = "这是一个很长的句子，" * 12 + "结束。"
    assert all(len(p) <= 80 for p, _ in tts.plan_segments(zh, "chinese"))


def test_plan_scale_controls_granularity():
    text = " ".join(f"This is sentence number {i} in the passage." for i in range(12))
    fine = tts.plan_segments(text, "english", 1.0)
    coarse = tts.plan_segments(text, "english", 100)
    assert len(coarse) == 1
    assert len(fine) > len(coarse)


def test_plan_never_drops_text():
    text = "Alpha beta. Gamma delta, epsilon zeta! Eta? Theta iota kappa lambda mu nu xi omicron pi rho sigma tau."
    joined = " ".join(p for p, _ in tts.plan_segments(text, "english"))
    assert joined.split() == text.split()


# ── duration model ───────────────────────────────────────────────────────

def test_expected_seconds_scales_with_length():
    short = tts.expected_seconds("Hello world.", "english")
    long = tts.expected_seconds("Hello world. " * 10, "english")
    assert 0.7 <= short < 2.5
    assert long > short * 5


def test_expected_seconds_chinese_is_per_character():
    a = tts.expected_seconds("你好世界" * 5, "chinese")
    b = tts.expected_seconds("你好世界" * 10, "chinese")
    assert b > a * 1.6


# ── signal helpers ───────────────────────────────────────────────────────

SR = tts.SR


def _tone(seconds, hz=180.0, amp=0.3, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _f0(x, sr=SR):
    """Dominant frequency via FFT peak."""
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[int(np.argmax(spec))]


def test_trim_silence_removes_lead_and_tail():
    x = np.concatenate([np.zeros(SR), _tone(0.5), np.zeros(SR)])
    y = tts.trim_silence(x)
    assert 0.5 < len(y) / SR < 0.8


def test_trim_silence_leaves_pure_silence_alone():
    x = np.zeros(SR, dtype=np.float32)
    assert len(tts.trim_silence(x)) == len(x)


def test_fade_zeroes_the_edges_only():
    y = tts.fade(np.ones(SR, dtype=np.float32))
    assert y[0] == 0.0 and y[-1] == 0.0 and y[SR // 2] == 1.0


@pytest.mark.parametrize("rate", [0.75, 0.9, 1.25, 1.5])
def test_time_stretch_changes_duration_not_pitch(rate):
    x = _tone(2.0, 150.0)
    y = tts.time_stretch(x, rate)
    assert abs(len(y) / len(x) - 1 / rate) < 0.02
    assert abs(_f0(y[SR // 4: -SR // 4]) - 150.0) < 6.0
    assert np.max(np.abs(y)) < 0.5


def test_time_stretch_identity_and_short_input():
    x = _tone(1.0)
    assert tts.time_stretch(x, 1.0) is x
    tiny = np.ones(100, dtype=np.float32)
    assert tts.time_stretch(tiny, 1.5) is tiny


def test_assemble_inserts_pauses_and_crossfades():
    a, b = _tone(1.0), _tone(1.0)
    out = tts.assemble([a, b], [0.5])
    assert abs(len(out) / SR - 2.5) < 0.02
    mid = out[int(1.1 * SR): int(1.4 * SR)]
    assert np.max(np.abs(mid)) < 1e-6
    assert tts.assemble([], []).size == 0


def test_active_rms_ignores_silence():
    x = np.concatenate([_tone(1.0, amp=0.2), np.zeros(SR * 3, dtype=np.float32)])
    assert 0.12 < tts.active_rms(x) < 0.16


# ── engine guards that don't need a model ────────────────────────────────

def test_synthesize_rejects_empty_text_and_unknown_voice():
    eng = tts.TtsEngine()
    with pytest.raises(ValueError):
        eng.synthesize("   ", "vivian")
    with pytest.raises(ValueError):
        eng.synthesize("hello", "not_a_voice")


def test_missing_model_is_a_typed_error(monkeypatch):
    monkeypatch.setattr(tts, "repo_ready", lambda repo: False)
    with pytest.raises(tts.TtsModelMissing) as ei:
        tts.TtsEngine().synthesize("hello there", "ryan")
    assert ei.value.repo == tts.CUSTOM_REPO


def test_clone_uses_base_model_and_presets_use_custom():
    clone = tts.ClonePrompt(np.zeros(SR, np.float32), "hi")
    assert tts.TtsEngine.repo_for(clone) == tts.BASE_REPO
    assert tts.TtsEngine.repo_for("vivian") == tts.CUSTOM_REPO


class _FakeModel:
    """Stands in for the MLX model: returns a tone whose length follows the text."""

    def __init__(self, seconds_per_char=0.075, fail_first=0, truncate_above=None):
        self.calls = 0
        self.spc = seconds_per_char
        self.fail_first = fail_first
        self.truncate_above = truncate_above      # pieces longer than this many chars end early
        self.temps = []

    def generate_custom_voice(self, text, speaker, language, instruct=None, max_tokens=4096, **kw):
        self.calls += 1
        self.temps.append(kw.get("temperature"))
        secs = len(text) * self.spc
        if self.truncate_above and len(text) > self.truncate_above:
            secs *= 0.4                            # the model stops after the first part
        if self.calls <= self.fail_first:
            secs = min(max_tokens / tts.FRAMES_PER_SEC, secs * 6)     # runaway → hits the cap
        yield type("R", (), {"audio": _tone(secs, 200.0)})()


@pytest.fixture
def fake_engine(monkeypatch):
    import sys
    import types
    fake_mx = types.SimpleNamespace(random=types.SimpleNamespace(seed=lambda s: None), clear_cache=lambda: None,
                                    array=lambda a: a, cpu="cpu", gpu="gpu",
                                    default_stream=lambda device: device,
                                    set_default_stream=lambda stream: None,
                                    new_thread_unsafe_stream=lambda device: device,
                                    synchronize=lambda stream: None)
    from thundertalk.core import mlx_runtime
    monkeypatch.setattr(mlx_runtime, "_STREAMS", None)
    monkeypatch.setitem(sys.modules, "mlx", types.SimpleNamespace(core=fake_mx))
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mx)
    eng = tts.TtsEngine()

    def install(model):
        eng._model, eng._repo = model, tts.CUSTOM_REPO
        return eng
    return install


def test_synthesize_pipeline_end_to_end_with_fake_model(fake_engine):
    model = _FakeModel()
    eng = fake_engine(model)
    text = "This is the first sentence. This is the second one, a little longer than before. Third."
    seen = []
    r = eng.synthesize(text, "ryan", language="english", seed=1, progress=lambda i, n, s: seen.append((i, n)))
    assert r.sample_rate == tts.SR and r.language == "english"
    assert r.duration > 3.0
    assert np.max(np.abs(r.audio)) <= 0.9
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1]
    assert all(s.ok for s in r.segments)


def test_runaway_generation_is_retried_and_capped(fake_engine):
    model = _FakeModel(fail_first=1)
    eng = fake_engine(model)
    r = eng.synthesize("A perfectly normal sentence of moderate length.", "ryan", language="english", seed=2)
    assert r.segments[0].attempts >= 2
    assert r.segments[0].ok
    assert r.segments[0].audio_s < 5.0


def test_speed_changes_output_length(fake_engine):
    eng = fake_engine(_FakeModel())
    text = "One sentence that is long enough to measure a stretch reliably on the output."
    normal = eng.synthesize(text, "ryan", language="english", seed=3)
    fast = eng.synthesize(text, "ryan", language="english", seed=3, speed=1.5)
    assert fast.duration < normal.duration * 0.75


def test_cancel_stops_between_pieces(fake_engine):
    import threading
    eng = fake_engine(_FakeModel())
    cancel = threading.Event()

    def progress(i, n, s):
        if i == 1:
            cancel.set()

    text = " ".join(f"Sentence number {i} is here and it is reasonably long for a test." for i in range(8))
    with pytest.raises(tts.TtsCancelled):
        eng.synthesize(text, "ryan", language="english", params=tts.TtsParams(chunk_scale=0.3),
                       progress=progress, cancel=cancel)


# ── read-back verification ───────────────────────────────────────────────

def test_error_rate_ignores_case_punctuation_and_numbers():
    ref = "A fast typist manages about sixty words a minute, easily."
    hyp = "a fast typist manages about 60 words a minute easily"
    assert tts_verify.error_rate(ref, hyp, "english") == 0.0


def test_error_rate_detects_dropped_and_wrong_words():
    ref = "The quick brown fox jumps over the lazy dog today."
    assert tts_verify.error_rate(ref, "The quick brown fox jumps over the lazy dog today", "english") == 0.0
    dropped = tts_verify.error_rate(ref, "The quick brown fox", "english")
    assert dropped > 0.5
    assert tts_verify.error_rate(ref, "", "english") == 1.0


def test_error_rate_is_character_level_for_chinese():
    ref = "把所有处理都放在自己的电脑上，就没有这个担心。"
    assert tts_verify.error_rate(ref, "把所有处理都放在自己的电脑上就没有这个担心", "chinese") == 0.0
    assert tts_verify.error_rate("关于语音输入首先要明白的一点", "关于语音输入", "chinese") > 0.4
    assert tts_verify.error_rate("六十个词", "60个词", "chinese") is None       # too short once numbers are removed


def test_verifier_needs_a_loaded_engine():
    assert tts_verify.make_verifier(None) is None
    assert tts_verify.make_verifier(types.SimpleNamespace(is_loaded=False)) is None



def test_verifier_triggers_a_retry_and_the_good_take_wins(fake_engine):
    eng = fake_engine(_FakeModel())
    calls = []

    def verifier(audio, text, lang):
        calls.append(text)
        return 0.9 if len(calls) == 1 else 0.02           # first take misread, second fine

    r = eng.synthesize("A single sentence that the reader will misread once.", "ryan", language="english",
                       seed=5, verifier=verifier)
    assert r.segments[0].attempts == 2 and r.segments[0].ok
    assert r.segments[0].error_rate == pytest.approx(0.02)


def test_a_verifier_that_cannot_read_the_language_is_switched_off(fake_engine):
    eng = fake_engine(_FakeModel())
    calls = []

    def verifier(audio, text, lang):
        calls.append(1)
        return 1.0                                        # always "completely wrong"

    text = " ".join(f"This is sentence number {i} of a long passage that goes on for a while." for i in range(14))
    r = eng.synthesize(text, "ryan", language="english", seed=6, verifier=verifier,
                       params=tts.TtsParams(chunk_scale=0.6))
    assert len(r.segments) >= 4
    assert len(calls) <= 2                                # gave up after two confident disagreements
    assert all(s.ok for s in r.segments)                  # and did not flag every piece as bad


def test_retries_reseed_at_the_same_temperature(fake_engine):
    model = _FakeModel(fail_first=2)
    eng = fake_engine(model)
    eng.synthesize("A perfectly normal sentence of moderate length.", "ryan", language="english", seed=9)
    assert len(model.temps) >= 3 and len(set(model.temps)) == 1


def test_a_piece_the_model_keeps_cutting_short_is_redone_sentence_by_sentence(fake_engine):
    model = _FakeModel(truncate_above=70)              # long pieces always end early
    eng = fake_engine(model)
    text = ("This is the first sentence of a longer passage. Here is the second sentence, a bit longer. "
            "And this is the third and final one.")
    r = eng.synthesize(text, "ryan", language="english", seed=4, params=tts.TtsParams(chunk_scale=5))
    assert len(r.segments) == 1
    assert r.segments[0].ok                            # recovered through the per-sentence fallback
    expected = tts.expected_seconds(text, "english")
    assert r.duration > 0.8 * expected                 # nothing was dropped


def test_fallback_is_not_used_when_the_first_context_works(fake_engine):
    model = _FakeModel()
    eng = fake_engine(model)
    text = "One sentence here. Another one there. And a third sentence."
    r = eng.synthesize(text, "ryan", language="english", seed=4, params=tts.TtsParams(chunk_scale=5))
    assert model.calls == 1 and r.segments[0].attempts == 1


def test_a_missing_last_sentence_counts_as_a_failure():
    # Real case from the acceptance run: the model stopped before the final sentence.
    ref = ("嗨，小王，我是设计组的李明。周二你发来的设计稿我看过了，整体非常不错，不过有两点想跟你确认一下。"
           "第一，手机端的结算按钮有点小。第二，最终的文案能不能在下周五之前给我？如果没问题的话，麻烦回复我一下，"
           "谢谢你这么快就完成了。")
    heard = ("嗨，小王，我是设计组的李明。周二你发来的设计稿我看过了，整体非常不错。不过有两点想跟你确认一下。"
             "第一，手机端的结算按钮有点小。第二，最终的文案能不能在下周五之前给我？如果没问题的话，麻烦回复我一下。")
    assert tts_verify.error_rate(ref, heard, "chinese") >= 0.5
    assert tts_verify.error_rate(ref, ref, "chinese") == 0.0
    en = "Please send the numbers by Friday, and thanks again for the quick turnaround on this."
    assert tts_verify.error_rate(en, "Please send the numbers by Friday and", "english") >= 0.5
    # a small slip at the very end is not a missing ending
    assert tts_verify.error_rate(en, "please send the numbers by friday and thanks again for the quick turn around on this",
                                 "english") < 0.2
