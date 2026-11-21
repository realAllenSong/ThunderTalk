"""Speech that is over is decoded while the take goes on; the final decodes only the rest (#15)."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from thundertalk.core import dictation
from thundertalk.core.local_stream import FAST_WINDOW_S, LocalStream, fingerprint

SR = 16000


def _take(sentences: int, speech_s: float = 6.0, pause_s: float = 0.6) -> np.ndarray:
    """A recording of ``sentences`` stretches of "speech" (a tone whose pitch numbers it) with pauses between."""
    parts = []
    for i in range(sentences):
        t = np.arange(int(speech_s * SR)) / SR
        parts.append((0.2 * np.sin(2 * np.pi * (200 + 40 * i) * t)).astype(np.float32))
        parts.append(np.zeros(int(pause_s * SR), dtype=np.float32))
    return np.concatenate(parts)


def _decoder(log: list | None = None):
    """Writes one word per sentence it hears: "s3" for the 320 Hz tone. Deterministic on the audio alone."""
    def decode(x, sr, **_kw):
        words, frame = [], int(0.5 * sr)
        for k in range(0, len(x) - frame + 1, frame):
            seg = x[k:k + frame]
            if float(np.sqrt(np.mean(seg ** 2))) < 0.1:       # under ~0.25 s of tone: an edge, not a word
                continue
            spec = np.abs(np.fft.rfft(seg))
            word = f"s{int(round((np.argmax(spec) * sr / frame - 200) / 40))}"
            if not words or words[-1] != word:
                words.append(word)
        if log is not None:
            log.append(len(x) / sr)
        return SimpleNamespace(text=" ".join(words), truncated=False, inference_ms=1, model="fake", backend="mlx")
    return decode


@pytest.fixture(autouse=True)
def _speech_everywhere(monkeypatch):
    import thundertalk.core.transcribe as transcribe
    monkeypatch.setattr(transcribe, "_has_speech", lambda x, sr=SR: float(np.sqrt(np.mean(x ** 2))) > 0.01 if len(x) else False)


def _stream(audio: dict, decode, window_s: float = 30.0, **kw):
    return LocalStream(lambda: audio.get("x"), decode, lambda: window_s, kw.pop("token", lambda: "engine"), **kw)


def _record(stream: LocalStream, audio: dict, full: np.ndarray, step_s: float = 1.0) -> None:
    """Replay a recording: the snapshot grows by ``step_s`` and the stream gets a turn each time."""
    for n in range(int(step_s * SR), len(full) + 1, int(step_s * SR)):
        audio["x"] = full[:n]
        stream.step()
    audio["x"] = full
    stream.stop()


def test_nothing_is_decoded_while_the_take_still_fits_one_window():
    full, audio, log = _take(4), {}, []                      # 26 s
    s = _stream(audio, _decoder(log)); s.start(); s.stop()
    _record(s, audio, full)
    assert log == [] and s.take(full) is None                # the final decodes it in one go, as before


def test_finished_stretches_are_decoded_during_the_take_and_the_final_adds_only_the_rest():
    full, audio, log = _take(20), {}, []                     # 132 s
    s = _stream(audio, _decoder(log)); s.start(); s.stop()   # the thread is not used: the test drives step()
    _record(s, audio, full)
    text, upto = s.take(full)
    left = (len(full) - upto) / SR
    assert s.pieces >= 3 and left <= 30.0                    # at most one window is left, however long the take
    rest = []
    final = dictation.decode_chunks(_decoder(rest), full, SR, 30.0, start=upto, prefix=text)
    assert final.text == " ".join(f"s{i}" for i in range(20))   # every sentence once, in order
    assert sum(rest) <= 30.0 + 0.5                           # the final decoded only the rest
    # The same words as decoding everything after stop:
    assert final.text == dictation.decode_chunks(_decoder(), full, SR, 30.0).text


def test_fast_finish_leaves_less_for_the_final():
    full, audio = _take(20), {}
    s = _stream(audio, _decoder()); s.start(FAST_WINDOW_S); s.stop()
    _record(s, audio, full)
    text, upto = s.take(full)
    assert (len(full) - upto) / SR <= FAST_WINDOW_S + 0.5
    final = dictation.decode_chunks(_decoder(), full, SR, 30.0, start=upto, prefix=text)
    assert final.text == " ".join(f"s{i}" for i in range(20))


def test_the_newest_second_is_never_cut():
    full = _take(6)                                          # 39.6 s; the last pause ends the recording
    span = dictation.finished_span(full, SR, 0, 30.0)
    assert span is not None and span[1] <= len(full) - SR


def test_a_piece_that_comes_back_empty_where_there_was_speech_hands_the_whole_take_to_the_final():
    full, audio = _take(12), {}
    s = _stream(audio, lambda x, sr: SimpleNamespace(text="", truncated=False)); s.start(); s.stop()
    _record(s, audio, full)
    assert s.failed and s.take(full) is None


def test_a_decode_error_hands_the_whole_take_to_the_final():
    full, audio = _take(12), {}

    def broken(x, sr):
        raise RuntimeError("model unloaded")

    s = _stream(audio, broken); s.start(); s.stop()
    audio["x"] = full
    with pytest.raises(RuntimeError):
        s.step()                                             # the loop catches this and marks the take failed
    stop = threading.Event()
    s._tick_s = 0.01
    t = threading.Thread(target=s._loop, args=(stop,)); t.start(); t.join(2.0)
    assert s.failed == "RuntimeError" and s.take(full) is None


def test_take_refuses_another_recording_a_changed_engine_and_a_second_call():
    full, other, audio = _take(12), _take(12)[::-1].copy(), {}
    state = {"token": "model-a"}
    s = _stream(audio, _decoder(), token=lambda: state["token"]); s.start(); s.stop()
    _record(s, audio, full)
    assert s.pieces >= 1
    assert s.take(other) is None                             # not the recording it followed (one-shot: now used up)
    assert s.take(full) is None

    s.start(); s.stop(); audio.clear()
    _record(s, audio, full)
    state["token"] = "model-b"                               # the model or the hotwords changed mid-take
    assert s.take(full) is None

    state["token"] = "model-a"
    s.start(); s.stop(); audio.clear()
    _record(s, audio, full)
    assert s.take(full * 3.0) is not None                    # the same take after the final raised its level


def test_take_does_nothing_while_the_recording_is_still_running():
    full, audio = _take(12), {}
    s = _stream(audio, _decoder(), tick_s=3600); s.start()   # recording; the worker thread sleeps
    audio["x"] = full
    s.step()
    assert s.pieces == 1
    assert s.take(full) is None and s.active                 # a Studio decode of some clip must not end the stream
    s.stop()
    assert s.take(full) is not None


def test_a_busy_gpu_makes_the_piece_wait_for_the_next_tick():
    full, audio, log = _take(12), {}, []
    gpu = threading.Lock()
    s = _stream(audio, _decoder(log), lock=lambda: gpu); s.start(); s.stop()
    audio["x"] = full
    gpu.acquire()                                            # a Studio job
    assert s.step() is False and log == [] and s.wait_idle(0)
    gpu.release()
    assert s.step() is True and len(log) == 1 and not gpu.locked()


def test_fingerprint_survives_a_level_change_and_tells_takes_apart():
    a = _take(3)
    assert fingerprint(a) == fingerprint(a * 4.0)
    assert fingerprint(a) != fingerprint(_take(3, speech_s=5.0)[::-1].copy())


# ── through the engine ─────────────────────────────────────────────────────

def _engine(decode, stream):
    from thundertalk.core.asr import AsrEngine
    eng = AsrEngine()
    eng._mlx_model = object()
    eng._active_backend = "mlx"
    eng._model_family = "Qwen3-ASR"
    eng._model_id = "fake"
    eng._builtin_terms = False
    eng._recognize_any = lambda x, sr=SR, preview=False: decode(x, sr)
    eng.attach_local_stream(stream)
    return eng


def test_the_engine_decodes_only_what_the_stream_left():
    full, audio, log = _take(20), {}, []
    decode = _decoder(log)
    eng = _engine(decode, None)
    s = LocalStream(lambda: audio.get("x"), eng.recognize_window, lambda: eng.max_clip_seconds, eng.stream_token)
    eng.attach_local_stream(s)
    assert eng.streams_locally
    s.start(); s.stop()
    _record(s, audio, full)
    during = sum(log); log.clear()
    r = eng.recognize(full)
    assert r.text == " ".join(f"s{i}" for i in range(20))
    assert during > 60 and sum(log) <= eng.max_clip_seconds + 0.5      # most of the work was done before stop
    log.clear()
    assert eng.recognize(full).text == r.text and sum(log) > 120       # a second call decodes all of it


def test_a_preview_decode_never_uses_up_the_stream():
    full, audio = _take(20), {}
    eng = _engine(_decoder(), None)
    s = LocalStream(lambda: audio.get("x"), eng.recognize_window, lambda: eng.max_clip_seconds, eng.stream_token)
    eng.attach_local_stream(s)
    s.start(); s.stop()
    _record(s, audio, full)
    eng.recognize(full[:5 * SR], preview=True)
    assert s.take(full) is not None


def test_engines_that_do_not_decode_in_windows_are_left_alone():
    from thundertalk.core.asr import AsrEngine
    eng = AsrEngine()
    assert not eng.streams_locally                           # nothing loaded
    eng._mlx_model = object(); eng._active_backend = "cloud"; eng._cloud = "openai:x"
    assert not eng.streams_locally
