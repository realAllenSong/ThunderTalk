"""Streaming recognition for ``WS /v1/realtime/transcribe``.

A client streams 16 kHz PCM16; Silero VAD (the same model and threshold as the
dictation speech gate, core/speech_gate.py) opens a segment when speech starts
and closes it after ``silence_ms`` of silence. While the segment is open the
words so far are sent as ``partial`` messages; when it closes, the segment is
sent as ``final`` (decoded at full quality, never a preview decode).

Timing rules (the first two come from the dictation live preview, core/live_preview.py):
- Partials never pile up: a session has at most one partial decode queued or
  running; a tick that comes while it is busy is skipped.
- A long open segment folds its older part into "committed" text at a pause
  (``find_commit_point``), decoded at final quality, so each partial re-decodes
  at most ``PARTIAL_WINDOW_S`` seconds and the final only decodes the rest.
- No partial starts in the silence after speech. Instead, as soon as the
  quick detector hears the speaker stop (~0.1 s), the segment so far is
  decoded at final quality ("early final"). If the silence then lasts the
  whole window, that result is the final, so it is sent the moment the window
  closes; if speech resumes, it is sent as a partial and the final is decoded
  again later.

Every decode, from every connection and from POST /v1/audio/transcriptions,
goes through one ``DecodeScheduler`` worker thread (finals before partials),
so the event loop never blocks on a model and MLX is only touched from one
thread (it also takes GPU_LOCK inside ``AsrEngine``).

``RealtimeSession`` holds no asyncio code: the server passes it ``post`` (run a
callback on the loop thread) and ``send`` (queue a message), and tests pass
plain functions.
"""

from __future__ import annotations

import heapq
import itertools
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 16_000
DEFAULT_SILENCE_MS = 350
MIN_SILENCE_MS, MAX_SILENCE_MS = 100, 5000

PARTIAL_EVERY_S = 0.4        # partial cadence while speech continues (audio time)
MIN_PARTIAL_S = 0.5          # don't decode less than this for a partial
PARTIAL_WINDOW_S = 6.0       # longest uncommitted tail a partial re-decodes
MIN_COMMIT_S = 1.0           # shortest piece folded into committed text
PRE_ROLL_S = 0.1             # audio kept before the VAD's own (already padded) start
MAX_SEGMENT_S = 60.0         # an open segment is closed at this length
IDLE_KEEP_S = 30.0           # audio kept between segments, for {"type":"commit"}
FAST_SILENCE_S = 0.1         # "is the speaker talking right now" detector
KEEP_SILENCE_S = 0.25        # silence kept after speech in a final (the rest of the window is not decoded)

LANGUAGES = ("auto", "en", "zh", "ja", "ko", "yue", "es", "fr", "de", "ar", "hi", "it", "pt", "ru", "nl", "tr")

FINAL, PARTIAL = 0, 1        # scheduler priorities


# ── decode scheduler ─────────────────────────────────────────────────────

class DecodeScheduler:
    """One worker thread runs every model call, most urgent first.

    Priority 0 (finals, file transcriptions, model loads) always goes before
    priority 1 (partials); equal priorities run in submission order. Returns
    ``concurrent.futures.Future`` objects (``asyncio.wrap_future`` them)."""

    def __init__(self, name: str = "tt-asr") -> None:
        self._name = name
        self._heap: list = []
        self._count = itertools.count()
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    def submit(self, priority: int, fn: Callable, *args, **kwargs) -> Future:
        fut: Future = Future()
        with self._cond:
            if self._closed:
                raise RuntimeError("scheduler is shut down")
            heapq.heappush(self._heap, (priority, next(self._count), fut, fn, args, kwargs))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._cond.notify()
        return fut

    def pending(self) -> int:
        with self._cond:
            return len(self._heap)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._heap and not self._closed:
                    self._cond.wait()
                if not self._heap:
                    return
                _, _, fut, fn, args, kwargs = heapq.heappop(self._heap)
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args, **kwargs))
            except BaseException as exc:          # noqa: BLE001 - delivered to the waiter
                fut.set_exception(exc)

    def shutdown(self) -> None:
        with self._cond:
            self._closed = True
            for item in self._heap:
                item[2].cancel()
            self._heap.clear()
            self._cond.notify_all()


# ── streaming VAD ────────────────────────────────────────────────────────

@dataclass
class VadEvent:
    kind: str                    # "start" | "end"
    start: int                   # absolute sample where the speech starts (VAD-padded)
    at: int                      # absolute sample where the event was detected


class SileroStream:
    """Silero VAD (sherpa-onnx) fed frame by frame; positions are absolute
    samples since the session began. A segment ends after ``silence_ms`` of
    silence. A second, quick detector (``FAST_SILENCE_S``) answers "is the
    speaker talking right now", which only gates partials."""

    def __init__(self, silence_ms: int = DEFAULT_SILENCE_MS) -> None:
        import sherpa_onnx as so

        from thundertalk.core import speech_gate

        def config(min_silence: float, min_speech: float):
            cfg = so.VadModelConfig()
            cfg.silero_vad.model = speech_gate._model_path()
            cfg.silero_vad.threshold = speech_gate.THRESHOLD
            cfg.silero_vad.min_silence_duration = min_silence
            cfg.silero_vad.min_speech_duration = min_speech
            cfg.sample_rate = SAMPLE_RATE
            cfg.num_threads = 1
            cfg.provider = "cpu"
            return cfg

        cfg = config(silence_ms / 1000, speech_gate.MIN_SPEECH_SECONDS)
        self._vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=MAX_SEGMENT_S + 30)
        self._fast = so.VadModel.create(config(FAST_SILENCE_S, FAST_SILENCE_S))
        self._win = cfg.silero_vad.window_size
        self._rest = np.zeros(0, np.float32)
        self._origin = 0             # absolute sample of the detectors' sample 0
        self._fed = 0                # samples given to the detectors since _origin
        self._in_speech = False
        self.talking = False

    def accept(self, samples: np.ndarray) -> list[VadEvent]:
        x = np.concatenate([self._rest, samples]) if len(self._rest) else samples
        events: list[VadEvent] = []
        n = len(x) - len(x) % self._win
        for i in range(0, n, self._win):
            w = x[i:i + self._win]
            self._vad.accept_waveform(w)
            self.talking = bool(self._fast.is_speech(w))
            self._fed += self._win
            at = self._origin + self._fed
            detected = self._vad.is_speech_detected()
            if detected and not self._in_speech:
                events.append(VadEvent("start", self._origin + max(0, self._vad.current_segment.start), at))
            while not self._vad.empty():
                seg = self._vad.front
                events.append(VadEvent("end", self._origin + seg.start, at))
                self._vad.pop()
            self._in_speech = detected
        self._rest = x[n:].copy()
        return events

    def reset(self, at: int) -> None:
        """Forget all state; the next sample given is absolute sample ``at``."""
        self._vad.reset()
        self._fast.reset()
        self._rest = np.zeros(0, np.float32)
        self._origin, self._fed = at, 0
        self._in_speech = self.talking = False


# ── text helpers ─────────────────────────────────────────────────────────

_WORDY = re.compile(r"\w", re.UNICODE)


def meaningful(text: str) -> bool:
    """Anything besides punctuation and spaces?"""
    return bool(_WORDY.search(text or ""))


def join_text(a: str, b: str) -> str:
    from thundertalk.core.live_preview import join_text as join
    return join(a, b)


def find_commit_point(audio: np.ndarray, sr: int = SAMPLE_RATE) -> int:
    from thundertalk.core.live_preview import find_commit_point as find
    return find(audio, sr)


def looping(text: str, seconds: float) -> bool:
    from thundertalk.core.preview_guard import preview_is_looping
    return preview_is_looping(text, seconds)


def gate_has_speech(samples: np.ndarray) -> bool:
    from thundertalk.core.speech_gate import has_speech
    return has_speech(samples, SAMPLE_RATE)


# ── one connection ───────────────────────────────────────────────────────

Recognize = Callable[[np.ndarray, str, bool], str]     # (samples, language, preview) → text


@dataclass
class _Open:
    seg: int
    start: int                           # absolute sample
    committed_text: str = ""
    committed_upto: int = 0              # absolute sample; audio before it is in committed_text
    last_partial_at: int = 0             # absolute sample when the last partial was scheduled
    last_partial_text: str = ""
    partial: Optional[Future] = field(default=None, repr=False)
    talked: bool = True                  # speech heard since the last early final was started
    early: Optional[tuple] = field(default=None, repr=False)   # (future, end sample) of the early final


class RealtimeSession:
    """Segmenting and message logic for one WebSocket connection.

    ``feed_pcm16``, ``commit``, ``reset`` and ``close`` are called on the
    session's own thread (the event loop). Decodes run on ``scheduler``;
    their results come back through ``post`` before anything is sent."""

    def __init__(self, recognize: Recognize, *, scheduler, post: Callable[[Callable[[], None]], None],
                 send: Callable[[dict], None], language: str = "auto",
                 silence_ms: int = DEFAULT_SILENCE_MS, vad=None, sample_rate: int = SAMPLE_RATE) -> None:
        if sample_rate != SAMPLE_RATE:
            raise ValueError("16 kHz only")
        self._recognize = recognize
        self._scheduler = scheduler
        self._post = post
        self._send = send
        self.language = language
        self.silence_ms = silence_ms
        self._vad = vad if vad is not None else SileroStream(silence_ms)
        self._sr = sample_rate
        self._audio = np.zeros(0, np.float32)
        self._base = 0                   # absolute sample of _audio[0]
        self._total = 0                  # absolute samples received
        self._odd = b""                  # half a sample left from the last frame
        self._next_seg = 0
        self._open: Optional[_Open] = None
        self._idle_from = 0              # first sample not part of any closed segment
        self._gen = 0                    # bumped by close(): drops every result still in flight
        self._closed = False
        self.stats = {"partials": 0, "finals": 0, "skipped_busy": 0, "empty_finals": 0, "early_finals": 0}

    # ── input ───────────────────────────────────────────────────────────

    def feed_pcm16(self, data: bytes) -> None:
        if self._closed:
            return
        data = self._odd + data
        if len(data) % 2:
            data, self._odd = data[:-1], data[-1:]
        else:
            self._odd = b""
        if not data:
            return
        self.feed(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0)

    def feed(self, samples: np.ndarray) -> None:
        if self._closed or not len(samples):
            return
        self._audio = np.concatenate([self._audio, samples.astype(np.float32, copy=False)])
        self._total += len(samples)
        for ev in self._vad.accept(samples):
            if ev.kind == "start" and self._open is None:
                start = max(self._idle_from, self._base, ev.start - int(PRE_ROLL_S * self._sr))
                self._open = _Open(seg=self._next_seg, start=start, committed_upto=start,
                                   last_partial_at=start)
                self._next_seg += 1
            elif ev.kind == "end" and self._open is not None:
                quiet_from = ev.at - int(self.silence_ms * self._sr / 1000)
                self._finalize(min(ev.at, self._total), decode_to=quiet_from + int(KEEP_SILENCE_S * self._sr))
        op = self._open
        if op is not None and self._total - op.start >= MAX_SEGMENT_S * self._sr:
            self._finalize(self._total)
            self._vad.reset(self._total)
        elif op is not None:
            if self._vad.talking:
                op.talked, op.early = True, None   # speech goes on: an early final is out of date
                self._maybe_partial(op)
            elif op.talked:
                self._start_early_final(op)
        self._trim()

    def commit(self) -> None:
        """Close the open segment now (or, with none open, decode whatever
        speech arrived since the last segment)."""
        if self._closed:
            return
        if self._open is not None:
            self._finalize(self._total)
        elif self._total - self._idle_from >= int(0.3 * self._sr):
            seg = self._next_seg
            self._next_seg += 1
            audio = self._slice(self._idle_from, self._total)
            self._submit_final(seg, audio, "", gate=True)
            self._idle_from = self._total
        self._vad.reset(self._total)

    def reset(self) -> None:
        """Drop the open segment and buffered audio. Finals of segments that
        closed before the reset are still delivered."""
        self._open = None                # its partial in flight is dropped with it
        self._idle_from = self._total
        self._vad.reset(self._total)
        self._trim()

    def close(self) -> None:
        self._closed = True
        self._gen += 1
        self._open = None

    # ── partials ────────────────────────────────────────────────────────

    def _maybe_partial(self, op: _Open) -> None:
        if self._total - op.last_partial_at < PARTIAL_EVERY_S * self._sr:
            return
        if self._total - op.committed_upto < MIN_PARTIAL_S * self._sr:
            return
        if not self._vad.talking:
            return                       # silence after speech: let the final come first
        if op.partial is not None and not op.partial.done():
            self.stats["skipped_busy"] += 1
            return
        op.last_partial_at = self._total
        tail = self._slice(op.committed_upto, self._total)
        committed, upto, lang = op.committed_text, op.committed_upto, self.language
        fut = self._scheduler.submit(PARTIAL, self._partial_job, tail, committed, upto, lang)
        op.partial = fut
        gen = self._gen
        fut.add_done_callback(lambda f: self._post(lambda: self._partial_done(gen, op, upto, f)))

    def _start_early_final(self, op: _Open) -> None:
        if self._total - op.committed_upto < int(0.3 * self._sr):
            return
        op.talked = False
        end = self._total
        tail = self._slice(op.committed_upto, end)
        fut = self._scheduler.submit(FINAL, self._final_job, tail, op.committed_text, self.language, False)
        op.early = (fut, end)
        gen = self._gen
        fut.add_done_callback(lambda f: self._post(lambda: self._early_done(gen, op, f)))

    def _early_done(self, gen: int, op: _Open, fut: Future) -> None:
        """Speech resumed before the window closed: the early final is a partial."""
        if gen != self._gen or self._open is not op or fut.cancelled() or fut.exception() is not None:
            return
        text = " ".join(fut.result()[0].split())
        if meaningful(text) and text != op.last_partial_text and not looping(text, (self._total - op.start) / self._sr):
            op.last_partial_text = text
            self.stats["partials"] += 1
            self._send({"type": "partial", "text": text, "seg": op.seg})

    def _partial_job(self, tail: np.ndarray, committed: str, upto: int, lang: str):
        """Worker thread. Returns (committed_text, committed_upto, text)."""
        sr = self._sr
        if len(tail) > PARTIAL_WINDOW_S * sr:
            cut = find_commit_point(tail, sr)
            if cut >= MIN_COMMIT_S * sr:
                piece = self._recognize(tail[:cut], lang, False)
                if not looping(piece, cut / sr):
                    committed = join_text(committed, piece)
                upto += cut
                tail = tail[cut:]
        text = ""
        if len(tail) >= MIN_PARTIAL_S * sr:
            text = self._recognize(tail, lang, True)
            if looping(text, len(tail) / sr):
                text = ""
        return committed, upto, join_text(committed, text)

    def _partial_done(self, gen: int, op: _Open, upto_before: int, fut: Future) -> None:
        if gen != self._gen or self._open is not op or fut.cancelled():
            return                       # the segment closed or the session reset meanwhile
        try:
            committed, upto, text = fut.result()
        except Exception as exc:          # noqa: BLE001 - a partial must never end the session
            print(f"[Realtime] partial decode failed: {type(exc).__name__}: {exc}")
            return
        if op.committed_upto == upto_before and upto > upto_before:
            op.committed_text, op.committed_upto = committed, upto
        text = " ".join(text.split())
        if meaningful(text) and text != op.last_partial_text:
            op.last_partial_text = text
            self.stats["partials"] += 1
            self._send({"type": "partial", "text": text, "seg": op.seg})

    # ── finals ──────────────────────────────────────────────────────────

    def _finalize(self, end: int, decode_to: Optional[int] = None) -> None:
        """Close the open segment at ``end``; decode its audio up to
        ``decode_to`` (speech end plus a little silence) when given."""
        op, self._open = self._open, None
        if op is None:
            return
        self._idle_from = end
        stop = end if decode_to is None else max(op.committed_upto, min(end, decode_to))
        if decode_to is not None and op.early is not None and op.early[1] >= decode_to - int(KEEP_SILENCE_S * self._sr):
            # The early final covers all the speech: it is (or soon will be) the final.
            fut, early_end = op.early
            audio_ms = int(round((early_end - op.start) * 1000 / self._sr))
            gen, seg = self._gen, op.seg
            self.stats["early_finals"] += 1
            fut.add_done_callback(lambda f: self._post(lambda: self._final_done(gen, seg, audio_ms, f)))
            return
        tail = self._slice(op.committed_upto, stop)
        audio_ms = int(round((stop - op.start) * 1000 / self._sr))
        self._submit_final(op.seg, tail, op.committed_text, audio_ms=audio_ms)

    def _submit_final(self, seg: int, tail: np.ndarray, committed: str, *, audio_ms: Optional[int] = None,
                      gate: bool = False) -> None:
        if audio_ms is None:
            audio_ms = int(round(len(tail) * 1000 / self._sr))
        lang, gen = self.language, self._gen
        fut = self._scheduler.submit(FINAL, self._final_job, tail, committed, lang, gate)
        fut.add_done_callback(lambda f: self._post(lambda: self._final_done(gen, seg, audio_ms, f)))

    def _final_job(self, tail: np.ndarray, committed: str, lang: str, gate: bool):
        """Worker thread. Returns (text, decode_ms)."""
        t0 = time.perf_counter()
        text = ""
        if len(tail) >= int(0.1 * self._sr) and (not gate or gate_has_speech(tail)):
            text = self._recognize(tail, lang, False)
        return join_text(committed, text), int(round((time.perf_counter() - t0) * 1000))

    def _final_done(self, gen: int, seg: int, audio_ms: int, fut: Future) -> None:
        if gen != self._gen or fut.cancelled():
            return
        try:
            text, decode_ms = fut.result()
        except Exception as exc:          # noqa: BLE001
            print(f"[Realtime] final decode failed: {type(exc).__name__}: {exc}")
            self._send({"type": "error", "message": f"recognition failed: {exc}"})
            return
        text = " ".join(text.split())
        if not meaningful(text):
            self.stats["empty_finals"] += 1
            return
        self.stats["finals"] += 1
        self._send({"type": "final", "text": text, "seg": seg, "audio_ms": audio_ms, "decode_ms": decode_ms})

    # ── audio buffer ────────────────────────────────────────────────────

    def _slice(self, a: int, b: int) -> np.ndarray:
        a, b = max(a, self._base), min(b, self._total)
        return self._audio[a - self._base:b - self._base].copy() if b > a else np.zeros(0, np.float32)

    def _trim(self) -> None:
        keep_from = self._open.start if self._open is not None else max(
            self._idle_from, self._total - int(IDLE_KEEP_S * self._sr))
        keep_from = min(keep_from, self._total)
        if keep_from - self._base > self._sr:            # compact in ≥ 1 s steps
            self._audio = self._audio[keep_from - self._base:].copy()
            self._base = keep_from
