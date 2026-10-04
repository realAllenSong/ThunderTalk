"""Live preview during dictation: scheduling, skip-when-busy, stop/cancel,
bounded work on long recordings, and the overlay text. Fake ASR, real widgets."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from thundertalk.core import live_preview as lp
from thundertalk.core.live_preview import LivePreview, find_commit_point, join_text

SR = 16_000


def _pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def _until(cond, ms: int = 3000) -> bool:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


def _speech(secs: float, pauses: tuple[float, ...] = ()) -> np.ndarray:
    """A tone with silent gaps (0.4 s) at the given times."""
    t = np.arange(int(secs * SR)) / SR
    a = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for p in pauses:
        a[int(p * SR): int((p + 0.4) * SR)] = 0.0005
    return a


class FakeRecorder:
    """Grows its buffer as if the mic callback were appending chunks."""

    def __init__(self, audio: np.ndarray) -> None:
        self.audio = audio
        self.n = 0

    def feed(self, secs: float) -> None:
        self.n = min(len(self.audio), self.n + int(secs * SR))

    def snapshot(self):
        return self.audio[: self.n].copy() if self.n else None


class FakeAsr:
    """Text = one word per second of audio; optional delay; records calls."""

    def __init__(self, delay: float = 0.0, text_fn=None) -> None:
        self.delay = delay
        self.calls: list[int] = []
        self.active = 0
        self.max_active = 0
        self._mx = threading.Lock()
        self.text_fn = text_fn or (lambda s: " ".join(f"w{i}" for i in range(int(len(s) / SR))))

    def recognize(self, samples: np.ndarray) -> str:
        with self._mx:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(len(samples))
        try:
            time.sleep(self.delay)
            return self.text_fn(samples)
        finally:
            with self._mx:
                self.active -= 1


def _preview(rec, asr, **kw) -> tuple[LivePreview, list[str]]:
    kw.setdefault("interval_ms", 40)
    kw.setdefault("first_tick_ms", 10)
    kw.setdefault("lock", threading.RLock())
    p = LivePreview(rec.snapshot, asr.recognize, **kw)
    got: list[str] = []
    p.text_changed.connect(got.append)
    return p, got


# ── scheduling ──────────────────────────────────────────────────────────

def test_ticks_periodically_and_emits_growing_text(qapp):
    rec, asr = FakeRecorder(_speech(10)), FakeAsr()
    p, got = _preview(rec, asr)
    p.start()
    for _ in range(4):
        rec.feed(1.0)
        _pump(60)
    p.stop()
    assert p.stats["ran"] >= 3
    assert got and got[-1].startswith("w0 w1")
    # Each new emission only when the text actually changed.
    assert all(a != b for a, b in zip(got, got[1:]))


def test_no_decode_below_minimum_audio(qapp):
    rec, asr = FakeRecorder(_speech(5)), FakeAsr()
    p, got = _preview(rec, asr)
    rec.feed(0.3)
    p.start()
    _pump(150)
    p.stop()
    assert asr.calls == [] and got == []


def test_skips_tick_while_previous_decode_runs(qapp):
    rec, asr = FakeRecorder(_speech(10)), FakeAsr(delay=0.15)
    rec.feed(3.0)
    p, _ = _preview(rec, asr, interval_ms=20)
    p.start()
    _pump(500)
    p.stop()
    assert p.wait_idle(2.0)
    assert p.stats["skipped_busy"] > 0
    assert asr.max_active == 1            # never two decodes at once


def test_skips_when_gpu_lock_is_held_elsewhere(qapp):
    rec, asr = FakeRecorder(_speech(5)), FakeAsr()
    rec.feed(2.0)
    lock = threading.RLock()
    held, release = threading.Event(), threading.Event()

    def other_job():                      # e.g. a TTS sentence in the Studio
        with lock:
            held.set()
            release.wait(5)

    th = threading.Thread(target=other_job)
    th.start()
    held.wait(2)
    p, got = _preview(rec, asr, lock=lock)
    p.start()
    _pump(200)
    assert asr.calls == [] and p.stats["skipped_lock"] >= 1
    release.set()
    th.join()
    assert _until(lambda: bool(got))      # resumes once the GPU is free
    p.stop()


# ── stop / cancel ───────────────────────────────────────────────────────

def test_stop_drops_inflight_result_and_starts_nothing_new(qapp):
    rec, asr = FakeRecorder(_speech(5)), FakeAsr(delay=0.25)
    rec.feed(2.0)
    p, got = _preview(rec, asr)
    p.start()
    assert _until(lambda: p.busy)
    p.stop()
    assert p.busy                         # stop() returns at once
    t0 = time.perf_counter()
    assert p.wait_idle(2.0)
    assert time.perf_counter() - t0 < 0.3
    calls = len(asr.calls)
    _pump(250)
    assert got == []                      # the in-flight text never shows up
    assert len(asr.calls) == calls        # no decode after stop


def test_stop_between_commit_and_tail_skips_the_tail_decode(qapp):
    rec, asr = FakeRecorder(_speech(20, pauses=(6.0,))), FakeAsr(delay=0.2)
    rec.feed(12.0)
    p, got = _preview(rec, asr, window_s=8.0)
    p.start()
    assert _until(lambda: len(asr.calls) == 1)   # commit decode running
    p.stop()
    assert p.wait_idle(2.0)
    assert len(asr.calls) == 1 and got == []


def test_final_waits_for_running_preview_decode(qapp):
    from thundertalk.app import AsrWorker

    rec, asr = FakeRecorder(_speech(5)), FakeAsr(delay=0.2)
    rec.feed(2.0)
    p, _ = _preview(rec, asr)
    p.start()
    assert _until(lambda: p.busy)
    p.stop()

    class Engine:
        def recognize(self, samples):
            from thundertalk.core.asr import AsrResult
            assert asr.active == 0        # never concurrent with the preview
            return AsrResult("final", 2.0, 1, "fake")

    done: list[str] = []
    w = AsrWorker(Engine(), rec.snapshot(), wait_before=lambda: p.wait_idle(5))
    w.done.connect(lambda text, *_: done.append(text))
    w.start()
    assert _until(lambda: done == ["final"])
    w.wait()


def test_restart_resets_committed_text(qapp):
    rec, asr = FakeRecorder(_speech(10)), FakeAsr()
    rec.feed(2.0)
    p, got = _preview(rec, asr)
    p.start()
    assert _until(lambda: bool(got))
    p.stop()
    p.wait_idle(1)
    rec2 = FakeRecorder(_speech(3))
    p._snapshot = rec2.snapshot
    asr.text_fn = lambda s: "fresh"
    rec2.feed(1.0)
    got.clear()
    p.start()
    assert _until(lambda: bool(got))
    p.stop()
    assert got[-1] == "fresh"


# ── long recordings: bounded work ───────────────────────────────────────

def test_long_recording_only_redecodes_the_tail(qapp):
    audio = _speech(40, pauses=(5.0, 11.0, 17.0, 23.0, 29.0, 35.0))
    rec, asr = FakeRecorder(audio), FakeAsr()
    p, got = _preview(rec, asr, window_s=8.0)
    p.start()
    for _ in range(40):
        rec.feed(1.0)
        _pump(45)
    _pump(100)
    p.stop()
    assert p.stats["commits"] >= 3
    assert max(asr.calls) <= 8.0 * SR + 1     # never more than the window
    # Committed text survives: the preview keeps growing across commits.
    assert len(got[-1].split()) >= 30


def test_slow_model_backs_off(qapp, monkeypatch):
    rec, asr = FakeRecorder(_speech(20)), FakeAsr(delay=0.3)   # 2 s audio → RTF 0.15
    rec.feed(2.0)
    p, _ = _preview(rec, asr, interval_ms=100)
    p.start()
    assert _until(lambda: p.stats["ran"] >= 1)
    p.stop()
    p.wait_idle(1)
    assert p.interval_ms >= 700               # ~2.5× the decode time
    assert p.window_s == pytest.approx(lp.TARGET_DECODE_S / 0.15, rel=0.2)


def test_model_too_slow_turns_preview_off_for_the_recording(qapp, monkeypatch):
    monkeypatch.setattr(lp, "MAX_DECODE_S", 0.1)
    rec, asr = FakeRecorder(_speech(10)), FakeAsr(delay=0.2)
    rec.feed(2.0)
    p, got = _preview(rec, asr, interval_ms=20)
    p.start()
    assert _until(lambda: bool(got))
    rec.feed(3.0)
    _pump(400)
    assert len(asr.calls) == 1 and got == ["w0 w1"]   # what was shown stays
    p.stop()
    asr.delay = 0.0
    p.start()                                         # next recording tries again
    assert _until(lambda: len(asr.calls) >= 3)
    p.stop()


def test_slow_first_preview_during_studio_does_not_disable_preview(qapp, monkeypatch):
    from thundertalk.core.priority import STUDIO
    monkeypatch.setattr(lp, "MAX_DECODE_S", 0.01)
    rec, asr = FakeRecorder(_speech(10)), FakeAsr(delay=0.03)
    rec.feed(2.0)
    p, _ = _preview(rec, asr)
    p.start()
    p._timer.stop()
    with STUDIO.running():
        p._run(p._gen)
    assert p.stats["contended"] == 1 and not p._too_slow
    assert p.stats["uncontended_decode_s"] == []
    assert p.window_s == p._max_window
    # The next uncontended decode still detects a genuinely slow model.
    p._run(p._gen)
    assert p._too_slow and len(p.stats["uncontended_decode_s"]) == 1
    p.stop()


def test_preview_caps_qwen_mlx_generation(qapp, monkeypatch):
    import sys
    import types

    pytest.importorskip("mlx.core")
    from thundertalk.core.asr import AsrEngine

    seen: list[int] = []
    fake = types.SimpleNamespace(transcribe=lambda audio, **kw: (
        seen.append(kw["max_new_tokens"]), types.SimpleNamespace(text="hello"))[1])
    monkeypatch.setitem(sys.modules, "mlx_qwen3_asr", fake)
    eng = AsrEngine()
    eng._mlx_model = object()
    audio = _speech(5)
    assert eng.recognize(audio).text == "hello"
    assert eng.recognize(audio, preview=True).text == "hello"
    assert seen[0] == 64 + 24 * 5 and 32 < seen[1] <= 100


def test_decode_errors_never_raise(qapp):
    rec = FakeRecorder(_speech(5))
    rec.feed(2.0)

    def boom(_):
        raise RuntimeError("model unloaded")

    p = LivePreview(rec.snapshot, boom, interval_ms=30, first_tick_ms=5, lock=threading.RLock())
    p.start()
    assert _until(lambda: p.stats["errors"] >= 2)
    p.stop()


def test_commit_point_prefers_latest_pause():
    a = _speech(9, pauses=(2.0, 6.0))
    cut = find_commit_point(a, SR)
    assert 6.0 * SR <= cut <= 6.4 * SR
    # Pause inside the protected last second is not used.
    b = _speech(6, pauses=(2.0, 5.3))
    assert 2.0 * SR <= find_commit_point(b, SR) <= 2.4 * SR


def test_commit_point_with_noise_floor_and_few_pauses():
    rng = np.random.default_rng(0)
    a = _speech(12, pauses=(7.0,)) + rng.normal(0, 0.004, 12 * SR).astype(np.float32)
    assert 7.0 * SR <= find_commit_point(a, SR) <= 7.4 * SR


def test_preview_wanted_follows_setting_and_translation_mode():
    from thundertalk.core.live_preview import preview_wanted

    def s(**kw):
        d = {"live_preview": True, "translation_target": "off", "translation_mode": "direct"}
        d.update(kw)
        return d

    assert preview_wanted(s())
    assert not preview_wanted(s(live_preview=False))
    assert not preview_wanted(s(translation_target="en"))                      # S2TT pastes English
    assert preview_wanted(s(translation_target="en", translation_mode="review"))  # pastes the ASR text


def test_join_text_spacing():
    assert join_text("hello", "world") == "hello world"
    assert join_text("你好", "世界") == "你好世界"
    assert join_text("我们用 GPU", "来跑") == "我们用 GPU来跑"
    assert join_text("", "x") == "x" and join_text("x", " ") == "x"


# ── through the real engine (no model): MOSS silence, hotword filter ────

def test_moss_marker_only_output_shows_nothing(qapp, monkeypatch):
    from thundertalk.core import diarize
    from thundertalk.core.asr import AsrEngine

    monkeypatch.setattr(diarize, "transcribe",
                        lambda audio, max_tokens=None: diarize.parse_transcript(
                            "[0.00][S01][0.06][S02][0.12][S01][0."))
    eng = AsrEngine()
    eng._moss_model = object()
    rec = FakeRecorder(_speech(5))
    rec.feed(2.0)
    p = LivePreview(rec.snapshot, lambda s: eng.recognize(s).text,
                    interval_ms=30, first_tick_ms=5)
    got: list[str] = []
    p.text_changed.connect(got.append)
    p.start()
    assert _until(lambda: p.stats["ran"] >= 2)
    p.stop()
    assert got == []


def test_moss_speaker_turns_flatten_to_one_line(qapp, monkeypatch):
    from thundertalk.core import diarize
    from thundertalk.core.asr import AsrEngine

    monkeypatch.setattr(diarize, "transcribe",
                        lambda audio, max_tokens=None: diarize.parse_transcript(
                            "[0.0][S01]Hi there.[1.0][1.1][S02]Hello.[2.0]"))
    eng = AsrEngine()
    eng._moss_model = object()
    eng.set_speaker_labels(True)
    rec = FakeRecorder(_speech(5))
    rec.feed(2.0)
    p = LivePreview(rec.snapshot, lambda s: eng.recognize(s).text,
                    interval_ms=30, first_tick_ms=5)
    got: list[str] = []
    p.text_changed.connect(got.append)
    p.start()
    assert _until(lambda: bool(got))
    p.stop()
    assert got[-1] == "S01: Hi there. S02: Hello."


# ── overlay ─────────────────────────────────────────────────────────────

def test_overlay_preview_grows_and_keeps_latest_words(qapp):
    from thundertalk.ui.overlay import VoiceOverlay

    ov = VoiceOverlay()
    ov.show_recording()
    h0 = ov.height()
    ov.set_preview_text("hello world")
    assert ov.preview_lines == ["hello world"]
    assert ov.height() > h0
    long = " ".join(f"word{i}" for i in range(120))
    ov.set_preview_text(long)
    lines = ov.preview_lines
    assert len(lines) == 3
    assert lines[0].startswith("…")
    assert lines[-1].endswith("word119")        # newest words visible
    assert ov.grab().width() == ov.width()      # paints without error
    ov.set_preview_text("")
    assert ov.preview_lines == [] and ov.height() == h0
    ov.hide_overlay()


def test_overlay_preview_wraps_chinese_and_resets(qapp):
    from thundertalk.ui.overlay import VoiceOverlay

    ov = VoiceOverlay()
    ov.show_recording()
    ov.set_preview_text("今天我们来聊一聊本地语音识别模型在苹果芯片上的表现" * 6)
    assert len(ov.preview_lines) == 3
    assert ov.preview_lines[0].startswith("…")
    ov.show_transcribing()                       # last preview stays while finishing
    assert len(ov.preview_lines) == 3
    ov.show_error("No speech detected")
    assert ov.preview_lines == []
    ov.show_recording()
    assert ov.preview_lines == []
    ov.set_preview_text("x")
    ov.hide_overlay()
    assert ov.preview_lines == []
    ov.set_preview_text("ignored while hidden")
    assert ov.preview_lines == []


def test_settings_toggle_and_strings(qapp, isolated_home, no_audio_hw):
    from thundertalk.core import i18n
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.settings_page import SettingsPage

    for key in ("settings.live_preview.label", "settings.live_preview.desc"):
        entry = i18n._STRINGS[key] if hasattr(i18n, "_STRINGS") else None
        if entry is not None:
            assert entry["en"] and entry["zh"]
    s = Settings()
    assert s.get("live_preview") is True
    page = SettingsPage(s)
    page._live_toggle.toggled_signal.emit(False)
    assert Settings().get("live_preview") is False


@pytest.mark.parametrize("text", [
    "我们来看一下机器人机器人机器人，接下来继续", "你好你好你好你好",
    "Let us try fallback fallback fallback fallback now.",
    "we should try again, try again, try again!",
    "介绍 humanoid机器人humanoid机器人humanoid机器人humanoid机器人",
    "GPT 的 Astra、Luna，humanoid 机器人 humanoid 机器人 humanoid 机器人",
])
def test_guard_realistic_loops(text):
    from thundertalk.core.preview_guard import preview_is_looping
    assert preview_is_looping(text, 8)


@pytest.mark.parametrize("text", [
    "今天介绍GPT的Astra、Luna、Terra以及humanoid机器人。",
    "This is a fairly fast English sentence with GPT-6.1 and Claude Opus.",
    "我们试试机器人机器人，继续。", "www.example.com", "1234567890", "",
])
def test_guard_clean_text(text):
    from thundertalk.core.preview_guard import preview_is_looping
    assert not preview_is_looping(text, 8)


def test_guard_implausible_length():
    from thundertalk.core.preview_guard import preview_is_looping
    text = " ".join(f"word{i}" for i in range(100))
    assert preview_is_looping(text, 1)
    assert not preview_is_looping(text, 60)


def test_loop_window_never_committed_or_used_as_reference(qapp):
    rec = FakeRecorder(_speech(16, pauses=(5, 11)))
    responses = iter(["今天使用Astra模型。", "前面是Luna，humanoid机器人" * 4, "干净的尾巴。"])
    p, got = _preview(rec, FakeAsr(text_fn=lambda _: next(responses)), window_s=8)
    p.start()
    p._timer.stop()
    rec.feed(3)
    p._on_result(p._gen, p._decode(p._gen))
    assert p.last_clean_text() == "今天使用Astra模型。"
    rec.feed(7)
    p._on_result(p._gen, p._decode(p._gen))
    assert p.loop_detected
    assert "Luna" not in p._committed_text
    assert p.last_clean_text() == "干净的尾巴。"
    p.stop()
    assert p.last_clean_text() == "干净的尾巴。"
    p.reset()
    assert p.last_clean_text() == "" and not p.loop_detected


def test_recording_setting_and_translations(qapp, isolated_home, no_audio_hw):
    from thundertalk.core import i18n
    from thundertalk.core.settings import Settings
    from thundertalk.ui.pages.settings_page import SettingsPage
    s = Settings()
    assert s.get("keep_recent_recordings") is True
    page = SettingsPage(s)
    page._recordings_toggle.toggled_signal.emit(False)
    assert Settings().get("keep_recent_recordings") is False
    for key in ("settings.keep_recent_recordings.label", "settings.keep_recent_recordings.desc"):
        assert i18n._STRINGS[key]["en"] and i18n._STRINGS[key]["zh"]


def test_guard_long_repeated_phrase():
    from thundertalk.core.preview_guard import preview_is_looping
    phrase = " ".join(f"technicalTerm{i}" for i in range(10))
    assert preview_is_looping(phrase * 3, 30)


def test_guard_single_letter_words_and_punctuation_runaway():
    from thundertalk.core.preview_guard import preview_is_looping
    assert preview_is_looping("I I I cannot stop", 3)
    assert preview_is_looping("." * 1000, 3)
    assert not preview_is_looping("www and AAA batteries", 3)
