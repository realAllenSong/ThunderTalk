"""Finish earlier speech while the user is still talking (local engines).

The pasted text of a dictation used to come from decoding the whole take
after stop, so the wait grew with the length: 3.4 s for a typical 1–4 minute
take on an M3 Max, 10.7 s for 4.5 minutes (#15). Yet everything but the last
stretch has long been said by then.

While recording, each stretch of speech that is over is decoded here, with
the engine's own final settings (the same pause cuts, edge context and hotword
check that the final uses), and kept. At stop the final asks ``take`` for what
is done and decodes only the rest: one decode window at most, whatever the
length of the take.

Nothing is assumed: ``take`` hands the text over only when the audio is the
recording this stream followed, the engine is the one it started with, and no
piece failed or came back empty where there was speech. Otherwise the final
decodes the whole take as before.

``window_s`` is the decode window used for cutting; the pieces are about three
quarters of it. Measured on 36 takes of 30 s to 4.5 min (M3 Max, ThunderTalk ASR
0.6B v2; token errors on the 20 with references, wait after stop median / max):

    decoding after stop (1.15.0)   6.51 %   3.36 s / 11.27 s
    the engine's 30 s window       6.54 %   0.61 s / 1.18 s
    20 s (DEFAULT_WINDOW_S)        6.26 %   0.45 s / 0.76 s
    12 s (FAST_WINDOW_S)           6.44 %   0.28 s / 0.53 s

The differences in errors are within noise for 8,127 tokens; 20 s is the
default because it is also what moves the text least from decoding after stop
among the windows that halve the wait again, and "fast finish" is the opt-in.
"""

from __future__ import annotations

import threading
import time
import zlib
from typing import Callable, Optional

import numpy as np

SR = 16_000
TICK_S = 1.0
DEFAULT_WINDOW_S = 20.0  # pieces of about 15 s, at most 20 s left at stop
FAST_WINDOW_S = 12.0     # "fast finish": pieces of about 9 s, at most 12 s left at stop


def fingerprint(x: np.ndarray) -> int:
    """Identifies a recording by the signs of its first second: the same before
    and after the final raises the level of a quiet take (normalize_quiet)."""
    return zlib.crc32(np.signbit(np.ascontiguousarray(x[:SR], dtype=np.float32)).tobytes())


class LocalStream:
    """One per app; ``start()`` per recording.

    ``snapshot()`` returns the 16 kHz samples recorded so far (or None).
    ``decode(samples, sr)`` is the engine's final decode of one window.
    ``window()`` is the engine's decode window in seconds, ``token()`` whatever
    must not change during a take (model, language, hotwords).
    ``lock()`` (optional) returns the lock to take, without waiting, around a
    decode, or None (a CPU engine): when a Studio job holds the GPU the piece
    waits for the next tick.
    """

    def __init__(self, snapshot: Callable[[], Optional[np.ndarray]],
                 decode: Callable[[np.ndarray, int], object],
                 window: Callable[[], float], token: Callable[[], object] = lambda: None,
                 *, lock=None, tick_s: float = TICK_S) -> None:
        self._snapshot = snapshot
        self._decode = decode
        self._window = window
        self._token_of = token
        self._gpu = lock
        self._tick_s = tick_s
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()
        self._stop.set()
        self._thread: Optional[threading.Thread] = None
        self._reset()

    def _reset(self) -> None:
        self.text = ""
        self.upto = 0                    # samples already decoded
        self.pieces = 0
        self.decode_s = 0.0
        self.failed = ""
        self._fp: Optional[int] = None
        self._token = None
        self._max_s = 0.0

    # ── recording lifecycle (UI thread) ──
    def start(self, window_s: Optional[float] = None) -> None:
        self.stop()
        self.wait_idle(5.0)
        with self._lock:
            self._reset()
            engine_window = float(self._window())
            # Cut with the engine's window, or a shorter one; never decode more than the engine allows.
            self._max_s = min(engine_window, window_s) if window_s else engine_window
            self._token = self._token_of()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), name="local-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """No new pieces; one already being decoded still finishes (``take`` waits for it)."""
        self._stop.set()

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        return self._idle.wait(timeout)

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    # ── worker ──
    def _loop(self, stop: threading.Event) -> None:
        while not stop.wait(self._tick_s):
            try:
                self.step(stop)
            except Exception as exc:                  # noqa: BLE001 — the final decodes everything instead
                self.failed = type(exc).__name__
                print(f"[LocalStream] off for this take ({self.failed}); the final decodes all of it")
            if self.failed:
                return

    def step(self, stop: Optional[threading.Event] = None) -> bool:
        """Decode the next finished stretch, if there is one. True when a piece was added."""
        from thundertalk.core.dictation import decode_span, finished_span

        x = self._snapshot()
        if x is None or self.failed:
            return False
        span = finished_span(x, SR, self.upto, self._max_s)
        if span is None:
            return False
        gpu = self._gpu() if callable(self._gpu) else self._gpu
        self._idle.clear()                            # before the lock: the final waits for idle, then takes it
        if gpu is not None and not gpu.acquire(blocking=False):
            self._idle.set()
            return False                              # a preview decode or a Studio job has the GPU: next tick
        try:
            if stop is not None and stop.is_set():
                return False
            if self._fp is None:
                self._fp = fingerprint(x)
            start, end = span
            t0 = time.perf_counter()
            text, _r, incomplete = decode_span(self._decode, x, SR, start, end, self._max_s, self.text)
            dt = time.perf_counter() - t0
        finally:
            if gpu is not None:
                gpu.release()
            self._idle.set()
        if incomplete:
            # Empty or cut short where there was speech: the final's own recovery handles
            # the whole take, exactly as it would have without this stream.
            self.failed = "incomplete piece"
            print("[LocalStream] a piece came back incomplete; the final decodes all of it")
            return False
        with self._lock:
            self.text, self.upto = text, end
            self.pieces += 1
            self.decode_s += dt
        print(f"[LocalStream] piece {self.pieces}: {(end - start) / SR:.1f}s in {dt:.2f}s "
              f"({self.upto / SR:.1f}s of the take done)")
        return True

    # ── final ──
    def take(self, samples: np.ndarray, timeout: float = 30.0) -> Optional[tuple[str, int]]:
        """(text so far, samples covered) when ``samples`` is the recording this
        stream followed, with the same engine state, and nothing failed; None
        means "decode it all". One-shot."""
        if not self._stop.is_set():
            return None                  # still recording: some other clip is being decoded (a Studio job)
        done = self._idle.wait(timeout)
        with self._lock:
            ok = (done and not self.failed and 0 < self.upto <= len(samples) and self._fp is not None
                  and fingerprint(samples) == self._fp and self._token_of() == self._token)
            out = (self.text, self.upto) if ok else None
            self._fp = None              # a retry, or another clip, is decoded in full
            return out
