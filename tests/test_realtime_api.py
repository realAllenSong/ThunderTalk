"""Streaming recognition: segmenting, message shapes, scheduling (no model loaded)."""
from __future__ import annotations

import json
import threading
import wave
from concurrent.futures import Future
from pathlib import Path

import numpy as np
import pytest

from thundertalk.api import realtime
from thundertalk.api.realtime import FINAL, PARTIAL, DecodeScheduler, RealtimeSession, VadEvent

SR = 16_000


# ── fakes ────────────────────────────────────────────────────────────────

class LevelVad:
    """Speech = samples above 0.05. Starts after 0.25 s of speech, ends after
    ``silence_ms`` of quiet, positions in absolute samples (like SileroStream)."""

    def __init__(self, silence_ms=350):
        self.silence = int(silence_ms * SR / 1000)
        self.reset(0)

    def reset(self, at):
        self.pos, self.run_speech, self.run_quiet, self.in_speech, self.start = at, 0, 0, False, None
        self.talking = False

    def accept(self, samples):
        events = []
        for chunk in np.array_split(samples, max(1, len(samples) // 512)):
            loud = float(np.max(np.abs(chunk))) > 0.05
            self.pos += len(chunk)
            self.talking = loud
            if loud:
                self.run_speech += len(chunk)
                self.run_quiet = 0
                if not self.in_speech and self.run_speech >= SR // 4:
                    self.in_speech, self.start = True, self.pos - self.run_speech
                    events.append(VadEvent("start", self.start, self.pos))
            else:
                self.run_quiet += len(chunk)
                self.run_speech = 0
                if self.in_speech and self.run_quiet >= self.silence:
                    self.in_speech = False
                    events.append(VadEvent("end", self.start, self.pos))
        return events


class SyncScheduler:
    """Runs jobs at once (or holds them while ``hold`` is set)."""

    def __init__(self):
        self.hold = False
        self.queue = []
        self.submitted = []

    def submit(self, priority, fn, *args):
        fut = Future()
        self.submitted.append(priority)
        if self.hold:
            self.queue.append((priority, fut, fn, args))
        else:
            self._run(fut, fn, args)
        return fut

    @staticmethod
    def _run(fut, fn, args):
        try:
            fut.set_result(fn(*args))
        except Exception as exc:          # noqa: BLE001
            fut.set_exception(exc)

    def release(self):
        self.hold = False
        for _, fut, fn, args in sorted(self.queue, key=lambda q: q[0]):
            self._run(fut, fn, args)
        self.queue.clear()


class Words:
    """Fake recognizer: one word per 0.5 s of audio; records every call."""

    def __init__(self, word="hi"):
        self.word = word
        self.calls = []

    def __call__(self, samples, language, preview):
        self.calls.append((len(samples) / SR, language, preview))
        return " ".join([self.word] * max(1, int(len(samples) / SR / 0.5)))


def speech(seconds, level=0.3):
    return np.full(int(seconds * SR), level, np.float32)


def quiet(seconds):
    return np.zeros(int(seconds * SR), np.float32)


def make(recognize=None, silence_ms=350, language="auto"):
    sent, sched = [], SyncScheduler()
    s = RealtimeSession(recognize or Words(), scheduler=sched, post=lambda fn: fn(), send=sent.append,
                        language=language, silence_ms=silence_ms, vad=LevelVad(silence_ms))
    return s, sent, sched


def feed_frames(s, audio, frame_s=0.2):
    n = int(frame_s * SR)
    for i in range(0, len(audio), n):
        s.feed(audio[i:i + n])


# ── session ──────────────────────────────────────────────────────────────

def test_one_utterance_gives_partials_then_one_final():
    s, sent, _ = make()
    feed_frames(s, np.concatenate([quiet(0.4), speech(2.0), quiet(0.6)]))
    kinds = [m["type"] for m in sent]
    assert kinds[-1] == "final" and kinds.count("final") == 1
    assert kinds.count("partial") >= 2
    final = sent[-1]
    assert set(final) == {"type", "text", "seg", "audio_ms", "decode_ms"}
    assert final["seg"] == 0 and final["text"].startswith("hi")
    # VAD start (2.0 s of speech) to the end of the silence window, plus the pre-roll
    assert 2200 <= final["audio_ms"] <= 2600
    assert isinstance(final["decode_ms"], int)
    for m in sent[:-1]:
        assert set(m) == {"type", "text", "seg"} and m["seg"] == 0


def test_partials_skip_unchanged_text_and_keep_cadence():
    rec = Words()
    s, sent, _ = make(rec)
    feed_frames(s, speech(3.0))
    texts = [m["text"] for m in sent if m["type"] == "partial"]
    assert len(texts) == len(set(texts))          # never the same text twice in a row
    previews = [c for c in rec.calls if c[2]]
    # about one decode per PARTIAL_EVERY_S of audio after the 0.25 s VAD onset and 0.5 s minimum
    assert 4 <= len(previews) <= 8


def test_segments_are_numbered_and_language_is_passed_through():
    rec = Words()
    s, sent, _ = make(rec, language="zh")
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6), speech(1.2), quiet(0.6)]))
    finals = [m for m in sent if m["type"] == "final"]
    assert [f["seg"] for f in finals] == [0, 1]
    assert {c[1] for c in rec.calls} == {"zh"}
    assert sum(1 for c in rec.calls if not c[2]) == 2          # one full (non-preview) decode per final


def test_silence_ms_controls_when_the_segment_closes():
    s, sent, _ = make(silence_ms=800)
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6)]))
    assert not [m for m in sent if m["type"] == "final"]
    feed_frames(s, quiet(0.4))
    assert [m for m in sent if m["type"] == "final"]


def test_empty_or_punctuation_finals_are_not_sent():
    s, sent, _ = make(lambda samples, lang, preview: "。" if not preview else "")
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6)]))
    assert sent == []
    assert s.stats["empty_finals"] == 1


def test_noise_without_speech_never_opens_a_segment():
    rec = Words()
    s, sent, _ = make(rec)
    feed_frames(s, np.concatenate([quiet(2.0), speech(0.1), quiet(1.0)]))
    assert sent == [] and rec.calls == []


def test_partial_in_flight_is_never_queued_twice():
    s, sent, sched = make()
    sched.hold = True
    feed_frames(s, speech(2.4))
    assert sched.submitted.count(PARTIAL) == 1
    assert s.stats["skipped_busy"] >= 2
    sched.release()
    assert [m["type"] for m in sent] == ["partial"]


def test_late_partial_after_the_final_is_dropped():
    s, sent, sched = make()
    feed_frames(s, speech(1.0))
    sched.hold = True
    feed_frames(s, speech(0.4))             # a partial is now held
    sched.hold = False
    feed_frames(s, quiet(0.6))             # the final runs at once
    final_index = next(i for i, m in enumerate(sent) if m["type"] == "final")
    sched.release()                         # the held partial finishes afterwards
    assert all(m["type"] != "partial" for m in sent[final_index + 1:])


def test_no_partial_starts_in_the_silence_after_speech_only_one_early_final():
    rec = Words()
    s, sent, _ = make(rec, silence_ms=1000)
    feed_frames(s, speech(1.5))
    n = len(rec.calls)
    feed_frames(s, quiet(0.8))               # still open: the window is 1000 ms
    assert [c[2] for c in rec.calls[n:]] == [False]      # one full decode, no preview
    assert not [m for m in sent if m["type"] == "final"]


def test_early_final_becomes_the_final_without_a_second_decode():
    rec = Words()
    s, sent, _ = make(rec)
    feed_frames(s, np.concatenate([speech(2.0), quiet(0.6)]))
    assert sum(1 for c in rec.calls if not c[2]) == 1
    assert sent[-1]["type"] == "final" and s.stats["early_finals"] == 1
    assert 2000 <= sent[-1]["audio_ms"] <= 2600


def test_speech_after_a_short_pause_turns_the_early_final_into_a_partial():
    rec = Words()
    s, sent, sched = make(rec)
    feed_frames(s, speech(1.6))
    sched.hold = True
    feed_frames(s, quiet(0.2))               # pause shorter than the window: early final held
    feed_frames(s, speech(0.2))              # speech resumes before it finishes
    sched.release()
    assert sent[-1]["type"] == "partial"
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6)]))
    finals = [m for m in sent if m["type"] == "final"]
    assert len(finals) == 1 and finals[0]["audio_ms"] >= 2800    # the final covers both stretches


def test_commit_closes_the_open_segment_now():
    s, sent, _ = make()
    feed_frames(s, speech(1.2))
    s.commit()
    assert sent[-1]["type"] == "final" and sent[-1]["seg"] == 0
    feed_frames(s, quiet(1.0))
    assert [m["type"] for m in sent].count("final") == 1     # not closed a second time


def test_commit_without_open_segment_uses_the_speech_gate(monkeypatch):
    s, sent, _ = make()
    feed_frames(s, np.concatenate([quiet(0.1), speech(0.2), quiet(0.1)]))   # too short for the VAD
    monkeypatch.setattr(realtime, "gate_has_speech", lambda x: True)
    s.commit()
    assert sent and sent[-1]["type"] == "final"
    feed_frames(s, quiet(0.4))
    monkeypatch.setattr(realtime, "gate_has_speech", lambda x: False)
    n = len(sent)
    s.commit()
    assert len(sent) == n


def test_reset_drops_the_open_segment():
    s, sent, _ = make()
    feed_frames(s, speech(1.5))
    s.reset()
    feed_frames(s, quiet(1.0))
    assert [m for m in sent if m["type"] == "final"] == []
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6)]))
    assert [m["seg"] for m in sent if m["type"] == "final"] == [1]


def test_closed_session_sends_nothing_more():
    s, sent, sched = make()
    feed_frames(s, speech(1.0))
    sched.hold = True
    feed_frames(s, quiet(0.6))
    s.close()
    sched.release()
    assert all(m["type"] == "partial" for m in sent)


def test_long_segment_commits_older_speech_and_final_decodes_only_the_rest(monkeypatch):
    rec = Words()
    s, sent, _ = make(rec)
    monkeypatch.setattr(realtime, "find_commit_point", lambda audio, sr=SR: int(3 * SR))
    feed_frames(s, speech(9.0))
    feed_frames(s, quiet(0.6))
    final = sent[-1]
    assert final["type"] == "final"
    longest = max(c[0] for c in rec.calls)
    assert longest <= realtime.PARTIAL_WINDOW_S + 0.5     # nothing re-decodes the whole 9 s
    assert final["audio_ms"] >= 9000


def test_segment_is_cut_at_the_maximum_length(monkeypatch):
    monkeypatch.setattr(realtime, "MAX_SEGMENT_S", 3.0)
    s, sent, _ = make()
    feed_frames(s, speech(4.0))
    assert [m["seg"] for m in sent if m["type"] == "final"] == [0]


def test_pcm16_frames_of_any_length_including_odd_bytes():
    s, sent, _ = make()
    pcm = (np.concatenate([speech(1.5), quiet(0.6)]) * 32767).astype("<i2").tobytes()
    for i in range(0, len(pcm), 1001):                 # odd sizes split samples across frames
        s.feed_pcm16(pcm[i:i + 1001])
    assert sent[-1]["type"] == "final"
    assert s._total == len(pcm) // 2


def test_buffer_stays_bounded_between_segments():
    s, _, _ = make()
    feed_frames(s, quiet(realtime.IDLE_KEEP_S + 20))
    assert len(s._audio) <= (realtime.IDLE_KEEP_S + 1.5) * SR


def test_decode_failure_reports_an_error_and_keeps_the_session():
    def broken(samples, lang, preview):
        if not preview:
            raise RuntimeError("boom")
        return "hi"
    s, sent, _ = make(broken)
    feed_frames(s, np.concatenate([speech(1.0), quiet(0.6)]))
    assert sent[-1] == {"type": "error", "message": "recognition failed: boom"}


# ── scheduler ────────────────────────────────────────────────────────────

def test_scheduler_runs_finals_before_queued_partials():
    sched = DecodeScheduler()
    gate, order = threading.Event(), []
    first = sched.submit(FINAL, gate.wait, 5)
    futs = [sched.submit(PARTIAL, order.append, "p1"), sched.submit(PARTIAL, order.append, "p2"),
            sched.submit(FINAL, order.append, "f1")]
    gate.set()
    first.result(5)
    for f in futs:
        f.result(5)
    assert order == ["f1", "p1", "p2"]
    sched.shutdown()


def test_scheduler_delivers_exceptions_and_runs_on_one_thread():
    sched = DecodeScheduler()
    names = {sched.submit(PARTIAL, lambda: threading.current_thread().name).result(5) for _ in range(5)}
    assert names == {"tt-asr"}
    with pytest.raises(ValueError):
        sched.submit(FINAL, lambda: (_ for _ in ()).throw(ValueError("x"))).result(5)
    sched.shutdown()


# ── real Silero model (640 KB, CPU; no speech model) ─────────────────────

def _voice_16k() -> np.ndarray:
    path = Path(__file__).resolve().parent.parent / "assets" / "voices" / "explainer-zh.wav"
    if not path.is_file():
        pytest.skip("no bundled voice clip")
    with wave.open(str(path)) as w:
        sr, x = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(1)
    t = np.arange(int(len(x) * SR / sr)) * sr / SR
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)[: 4 * SR]


def test_silero_stream_opens_and_closes_a_segment():
    pytest.importorskip("sherpa_onnx")
    vad = realtime.SileroStream(350)
    voice = _voice_16k()
    x = np.concatenate([quiet(0.5), voice, quiet(1.0)])
    events = []
    for i in range(0, len(x), 3200):
        events += vad.accept(x[i:i + 3200])
    kinds = [e.kind for e in events]
    assert kinds[0] == "start" and kinds[-1] == "end"
    end = events[-1]
    assert end.at - end.start <= len(x)
    assert not vad.talking
    vad.reset(len(x))
    assert vad.accept(quiet(0.5)) == []


# ── WebSocket endpoint (fake engine and VAD) ─────────────────────────────

class FakeAsr:
    default_id = None

    def __init__(self):
        self.rec = Words("hello")

    def resolve(self, model):
        if model == "missing":
            from thundertalk.api.server import ModelError
            raise ModelError("Model not downloaded: missing")
        return model or "fake-asr"

    async def ensure(self, model_id):
        return None

    def loaded(self, model_id):
        return True

    def recognizer(self, model_id):
        return self.rec


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from thundertalk.api import server
    monkeypatch.setattr(server, "_asr", FakeAsr())
    monkeypatch.setattr(realtime, "SileroStream", LevelVad)
    return TestClient(server.app)


def test_websocket_ready_partial_final(client):
    with client.websocket_connect("/v1/realtime/transcribe?language=en&silence_ms=350") as ws:
        assert ws.receive_json() == {"type": "ready", "model": "fake-asr", "sample_rate": 16000}
        pcm = (np.concatenate([speech(1.6), quiet(0.6)]) * 32767).astype("<i2").tobytes()
        for i in range(0, len(pcm), 6400):
            ws.send_bytes(pcm[i:i + 6400])
        msgs = []
        while not msgs or msgs[-1]["type"] != "final":
            msgs.append(ws.receive_json())
        assert msgs[0]["type"] == "partial" and msgs[0]["seg"] == 0
        assert msgs[-1]["text"].startswith("hello") and msgs[-1]["seg"] == 0


def test_websocket_commit_reset_and_bad_messages(client):
    with client.websocket_connect("/v1/realtime/transcribe?model=other") as ws:
        assert ws.receive_json()["model"] == "other"
        ws.send_bytes((speech(1.0) * 32767).astype("<i2").tobytes())
        ws.send_text(json.dumps({"type": "commit"}))
        msgs = [ws.receive_json()]
        while msgs[-1]["type"] != "final":
            msgs.append(ws.receive_json())
        ws.send_text(json.dumps({"type": "reset"}))
        ws.send_text("nonsense")
        assert ws.receive_json()["type"] == "error"


def test_websocket_rejects_unknown_language_and_missing_model(client):
    with client.websocket_connect("/v1/realtime/transcribe?language=xx") as ws:
        assert ws.receive_json()["type"] == "error"
    with client.websocket_connect("/v1/realtime/transcribe?model=missing") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error" and "not downloaded" in msg["message"]


def test_health_keeps_its_shape(client):
    r = client.get("/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "model"}
