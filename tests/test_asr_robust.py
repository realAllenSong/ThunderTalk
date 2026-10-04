"""Studio robustness with fakes: span capping, loop re-decode / cut-back, and
dictation priority (Studio pauses between spans; a dictation started during
a long job completes, in full, without waiting for the job)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk.core import audio_io, transcribe
from thundertalk.core.priority import DICTATION, DictationPriority

SR = transcribe.SR


def _burst(seconds, amp=0.2, hz=150.0):
    t = np.arange(int(seconds * SR)) / SR
    env = 0.04 + 0.96 * np.sin(2 * np.pi * 3.0 * t) ** 2
    return (amp * env * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _silence(seconds, noise=0.0008, seed=0):
    rng = np.random.default_rng(seed)
    return (noise * rng.standard_normal(int(seconds * SR))).astype(np.float32)


def _talk(pieces):
    out = []
    for i, (sp, pa) in enumerate(pieces):
        out += [_burst(sp), _silence(pa, seed=i)]
    return np.concatenate(out)


class Engine:
    """A CPU (sherpa-like) engine whose text is decided by ``reply(seconds)``."""
    is_loaded = True
    current_model = "fake"
    active_backend = "onnx"

    def __init__(self, reply=None, delay=0.0):
        self.reply = reply or (lambda s: "ok")
        self.delay = delay
        self.durations: list[float] = []
        self.lock = threading.Lock()

    def recognize(self, samples, sr=SR, cut_loops=True):
        dur = len(samples) / sr
        with self.lock:
            self.durations.append(dur)
        if self.delay:
            time.sleep(self.delay)
        return SimpleNamespace(text=self.reply(dur))


@pytest.fixture(autouse=True)
def _clean_priority(monkeypatch):
    # A lock regression must fail in seconds, not wait for production's
    # ten-minute stale signal safety valve.
    monkeypatch.setattr(DICTATION, "_stale_s", 2.0)
    yield
    while DICTATION.active:
        DICTATION.end()


def _wav(tmp_path, x, name="a.wav"):
    p = tmp_path / name
    audio_io.write_wav(str(p), x, SR)
    return str(p)


# ── span capping ────────────────────────────────────────────────────────

def test_fast_path_never_decodes_more_than_the_cap(tmp_path):
    x = _talk([(25.0, 0.5), (7.0, 0.5), (40.0, 0.5)])        # long runs without pauses
    eng = Engine()
    transcribe.transcribe_file(_wav(tmp_path, x), eng)
    pad = 0.3
    assert max(eng.durations) <= transcribe.FAST_MAX_SPAN_S + pad + 0.05


def test_capped_spans_still_cut_at_pauses():
    x = _talk([(6.0, 0.6)] * 10)
    spans = transcribe.segment_speech(x, target=transcribe.FAST_TARGET_S, max_len=transcribe.FAST_MAX_SPAN_S)
    assert all(b - a <= transcribe.FAST_MAX_SPAN_S + 1e-6 for a, b in spans)
    for _, b in spans[:-1]:
        j = int(b * SR)
        assert np.sqrt(np.mean(x[max(0, j - 800): j + 800] ** 2)) < 0.01


# ── loop guard ──────────────────────────────────────────────────────────

LOOP = "come on，teacher，" + "hands up，" * 300


def test_looping_span_is_redecoded_in_short_pieces():
    seg = _talk([(3.5, 0.5)] * 4)                                # 16 s, pauses every 4 s
    eng = Engine(reply=lambda s: LOOP if s > 10 else "hands up，hands up，")
    text = transcribe.decode_guarded(eng, seg)
    assert len(eng.durations) >= 3
    assert all(d <= transcribe.REDECODE_MAX_S + 0.01 for d in eng.durations[1:])
    assert "hands up" in text and text.count("hands up") <= 2 * (len(eng.durations) - 1)
    assert len(text) < 200


def test_piece_that_still_loops_is_cut_back():
    seg = _talk([(3.5, 0.5)] * 4)
    eng = Engine(reply=lambda s: LOOP)                          # loops at every length
    text = transcribe.decode_guarded(eng, seg)
    assert 0 < len(text) < 200 * len(eng.durations)
    for piece in text.split("come on"):
        assert piece.count("hands up") <= 2


def test_short_looping_span_is_cut_back_without_redecode():
    seg = _burst(6.0)
    eng = Engine(reply=lambda s: LOOP)
    text = transcribe.decode_guarded(eng, seg)
    assert len(eng.durations) == 1
    assert text == "come on，teacher，hands up，hands up，"


def test_real_chorus_is_kept_as_decoded():
    chorus = "Come on, teacher. " + "Hands up, " * 10 + "now sing along."
    seg = _talk([(3.5, 0.5)] * 4)
    eng = Engine(reply=lambda s: chorus if s > 10 else "Hands up, hands up, hands up, hands up.")
    assert transcribe.decode_guarded(eng, seg) == chorus     # pieces repeat it too → original wins


def test_chorus_split_across_pieces_is_still_recognised_as_real():
    chorus = "Come on, teacher. " + "Hands up. " * 9
    seg = _talk([(3.5, 0.5)] * 4)
    eng = Engine(reply=lambda s: chorus if s > 10 else "Hands up. " * round(s / 2.2) + "先生")
    assert transcribe.decode_guarded(eng, seg) == chorus.strip()
    assert len(eng.durations) >= 3                    # it did take the second look


def test_everyday_text_is_decoded_once():
    eng = Engine(reply=lambda s: "好的好的，没问题。")
    assert transcribe.decode_guarded(eng, _burst(12.0)) == "好的好的，没问题。"
    assert len(eng.durations) == 1


def test_transcribe_file_output_has_no_loop(tmp_path):
    x = _talk([(16.0, 0.6), (5.0, 0.6)])
    eng = Engine(reply=lambda s: LOOP if s > 10 else "hands up，hands up")
    tr = transcribe.transcribe_file(_wav(tmp_path, x), eng)
    assert "hands up" in tr.to_text() and len(tr.to_text()) < 400


def test_asr_engine_cuts_loops_from_final_results_only():
    from thundertalk.core.asr import AsrEngine, AsrResult
    eng = AsrEngine()
    eng._recognizer = object()
    eng._recognize_any = lambda s, sr, preview=False: AsrResult(LOOP, len(s) / sr, 1, "m")
    x = _burst(5.0)
    assert eng.recognize(x).text == "come on，teacher，hands up，hands up，"
    assert eng.recognize(x, preview=True).text == LOOP
    assert eng.recognize(x, cut_loops=False).text == LOOP   # Studio re-decodes instead


def test_sherpa_qwen3_generation_budget_follows_clip_length():
    from thundertalk.core.asr import AsrEngine

    class Stream:
        def __init__(self):
            self.options = {}
            self.result = SimpleNamespace(text="hello")

        def set_option(self, k, v):
            self.options[k] = v

        def accept_waveform(self, sr, x):
            pass

    streams = []

    class Rec:
        def create_stream(self):
            streams.append(Stream())
            return streams[-1]

        def decode_stream(self, s):
            pass

    eng = AsrEngine()
    eng._recognizer, eng._model_family = Rec(), "Qwen3-ASR"
    x = _burst(10.0)
    eng.recognize(x)
    eng.recognize(x, preview=True)
    eng._max_new_tokens = 256                         # "low" memory mode
    eng.recognize(_burst(60.0))
    assert [s.options["max_new_tokens"] for s in streams[:2]] == ["304", "152"]
    assert len(streams) > 3
    assert all(int(s.options["max_new_tokens"]) <= 256 for s in streams[2:])
    eng._model_family = "SenseVoice"
    eng.recognize(x)
    assert streams[-1].options == {}


# ── dictation priority ──────────────────────────────────────────────────

def test_priority_counts_overlapping_dictations():
    p = DictationPriority()
    p.begin()
    p.begin()
    p.end()
    assert p.active
    p.end()
    assert not p.active
    p.end()                                  # extra end is harmless
    assert not p.active


def test_priority_expires_a_leaked_begin():
    p = DictationPriority(stale_s=0.05)
    p.begin()
    time.sleep(0.08)
    assert not p.active
    assert p.wait_clear() == 0.0
    p.begin()                                # the leaked count is forgotten
    p.end()
    assert not p.active


def test_wait_clear_returns_on_end_and_on_cancel():
    p = DictationPriority()
    p.begin()
    threading.Timer(0.15, p.end).start()
    waited = p.wait_clear()
    assert 0.1 < waited < 1.0
    p.begin()
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    assert p.wait_clear(cancel) < 1.0
    p.end()


def test_studio_pauses_between_spans_while_dictation_is_active(tmp_path):
    x = _talk([(9.0, 0.6)] * 6)
    eng = Engine(delay=0.05)
    msgs: list[tuple[float, str]] = []
    t0 = time.monotonic()
    DICTATION.begin()
    threading.Timer(0.5, DICTATION.end).start()
    tr = transcribe.transcribe_file(_wav(tmp_path, x), eng,
                                    progress=lambda p, m: msgs.append((time.monotonic() - t0, m)))
    first_decode = next(t for t, m in msgs if "/" in m)
    assert [m for _, m in msgs].count("yield") == 1
    assert first_decode >= 0.45                  # nothing was decoded while dictating
    assert len(tr.segments) >= 2


def test_cancel_while_paused_for_dictation(tmp_path):
    x = _talk([(9.0, 0.6)] * 6)
    cancel = threading.Event()
    DICTATION.begin()
    threading.Timer(0.2, cancel.set).start()
    with pytest.raises(transcribe.TranscribeCancelled):
        transcribe.transcribe_file(_wav(tmp_path, x), Engine(), cancel=cancel)


def test_dictation_completes_during_a_long_studio_job(tmp_path):
    """Studio decodes 30 spans at 0.04 s each on a CPU engine. A dictation
    started mid-job gets its whole clip decoded right away (no lock shared
    with Studio), and Studio does no work until the dictation is done."""
    x = _talk([(9.0, 0.6)] * 30)
    said = "我想确认一下明天下午三点的会议，please bring the report."
    eng = Engine(reply=lambda s: said if 9.0 < s < 9.5 else "studio", delay=0.04)
    progress: list[tuple[float, str]] = []
    out: dict = {}

    def job():
        out["tr"] = transcribe.transcribe_file(
            _wav(tmp_path, x), eng, progress=lambda p, m: progress.append((time.monotonic(), m)))

    th = threading.Thread(target=job, daemon=True)
    th.start()
    time.sleep(0.1)
    DICTATION.begin()
    t_start = time.monotonic()
    time.sleep(0.2)                                   # the user speaks
    t_stop = time.monotonic()
    clip = _burst(9.2)
    text = eng.recognize(clip).text                  # the AsrWorker call (CPU: no GPU_LOCK)
    latency = time.monotonic() - t_stop
    DICTATION.end()
    th.join(3)
    assert not th.is_alive()
    assert text == said
    assert latency < 0.5                              # one decode, no queueing behind Studio
    started_during = [m for t, m in progress if t_start + 0.08 < t < t_stop and "/" in m]
    assert started_during == []                       # Studio paused after its in-flight span
    assert "yield" in [m for _, m in progress]
    assert len(out["tr"].segments) == 30


def test_gpu_engine_studio_holds_gpu_lock_per_span_only(tmp_path):
    from thundertalk.core.gpu_lock import GPU_LOCK
    x = _talk([(9.0, 0.6)] * 6)
    held: list[bool] = []

    class Gpu(Engine):
        active_backend = "mlx"

        def recognize(self, samples, sr=SR, cut_loops=True):
            got = GPU_LOCK.acquire(blocking=False)    # RLock: re-entrant if we hold it
            held.append(got and GPU_LOCK._is_owned())
            if got:
                GPU_LOCK.release()
            return super().recognize(samples, sr, cut_loops)

    transcribe.transcribe_file(_wav(tmp_path, x), Gpu())
    assert held and all(held)
    assert GPU_LOCK.acquire(blocking=False)
    GPU_LOCK.release()


def test_moss_path_pauses_mid_generation_for_dictation(monkeypatch, tmp_path):
    from thundertalk.core import diarize
    from thundertalk.core.gpu_lock import GPU_LOCK
    x = _talk([(9.0, 0.6)] * 3)
    lock_free_during_pause: list[bool] = []

    def fake_transcribe(audio, max_tokens=None, between_tokens=None):
        for k in range(50):
            if k == 10:
                DICTATION.begin()

                def dictation():
                    with GPU_LOCK:                    # an MLX dictation gets the GPU
                        lock_free_during_pause.append(True)
                    DICTATION.end()
                threading.Timer(0.1, dictation).start()
            between_tokens()
        return [SimpleNamespace(start=0.0, end=3.0, speaker="S01", text="hi")]

    monkeypatch.setattr(diarize, "load_model", lambda: object())
    monkeypatch.setattr(diarize, "transcribe", fake_transcribe)
    monkeypatch.setattr(diarize, "synchronize", lambda: None)
    msgs: list[str] = []
    tr = transcribe.transcribe_file(_wav(tmp_path, x), None, speakers=True,
                                    progress=lambda p, m: msgs.append(m))
    assert lock_free_during_pause == [True]
    assert "yield" in msgs and tr.has_speakers


def test_diarize_between_tokens_streams_and_releases_model_lock(monkeypatch):
    from thundertalk.core import diarize

    class Tok:
        def decode(self, ids, skip_special_tokens=True):
            return "[0.00][S01]" + "".join(chr(ord("a") + i % 26) for i in ids) + "[1.00]"

    class Model:
        _tokenizer = Tok()

        def stream_generate(self, audio, max_tokens):
            for i in range(5):
                yield i, None

    monkeypatch.setattr(diarize, "load_model", lambda: Model())
    seen = []
    segs = diarize.transcribe(np.zeros(SR, np.float32),
                              between_tokens=lambda: seen.append(diarize._MODEL_LOCK.locked()))
    assert seen == [False] * 5
    assert segs[0].text == "abcde" and segs[0].speaker == "S01"


# ── UI states ───────────────────────────────────────────────────────────

def test_overlay_waiting_state_only_while_transcribing(qapp):
    from thundertalk.core.i18n import t
    from thundertalk.ui.overlay import VoiceOverlay
    ov = VoiceOverlay()
    ov.show_recording()
    ov.show_waiting()                                 # still recording: ignored
    assert ov._state == VoiceOverlay._RECORDING
    ov.show_transcribing()
    ov.show_waiting()
    assert ov._state == VoiceOverlay._TRANSCRIBING
    assert ov._text == t("overlay.waiting_studio").rstrip("…")
    ov.hide_overlay()


def test_studio_progress_names_the_pause():
    from thundertalk.core import i18n
    from thundertalk.ui.studio.transcribe_tab import phase_text
    orig = i18n.LANG
    try:
        for lang in ("en", "zh"):
            i18n.set_language(lang)
            assert phase_text("yield") == i18n.t("studio.progress.yield") != "studio.progress.yield"
    finally:
        i18n.set_language(orig)


# ── dictation worker: waits (not drops) when the GPU is busy ─────────────

def test_asr_worker_waits_for_gpu_and_decodes_the_whole_clip(qapp):
    from thundertalk.app import AsrWorker
    from thundertalk.core.gpu_lock import GPU_LOCK

    class GpuEngine:
        uses_gpu = True

        def recognize(self, samples):
            return SimpleNamespace(text=f"{len(samples)} samples", inference_ms=1,
                                   duration_secs=len(samples) / SR, backend="mlx", rtf=0.1)

    got, waits = [], []
    clip = np.ones(SR * 9, np.float32)
    w = AsrWorker(GpuEngine(), clip, lock=GPU_LOCK)
    w.done.connect(lambda text, *a: got.append(text))
    w.waiting.connect(lambda: waits.append(1))
    hold = threading.Event()

    def studio():
        with GPU_LOCK:
            hold.wait(2)
    th = threading.Thread(target=studio)
    th.start()
    time.sleep(0.05)
    w.start()
    time.sleep(0.2)
    assert got == []
    hold.set()
    th.join()
    assert w.wait(3000)
    qapp.processEvents()
    assert waits == [1] and got == [f"{SR * 9} samples"]
