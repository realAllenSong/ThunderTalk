"""Cloud dictation that transcribes while you speak.

While a cloud engine records, every finished stretch of speech (cut at a
pause once ``SEGMENT_S`` seconds have built up) is sent to the selected cloud
model right away, with the text so far as context. The results are the live
preview, from the same model that writes the final text. At stop only the
remaining tail is sent, so the final arrives about as fast as one short
request instead of one long one.

Each piece of audio is sent exactly once, so the cost equals one full-clip
request (Groq bills at least 10 s per request; its segments are never
shorter). Any failed segment turns streaming off for that take and the final
falls back to transcribing the whole recording, so nothing is lost.
"""

from __future__ import annotations

import threading
import time
import zlib
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, Signal

SR = 16_000
SEGMENT_S = 8.0          # send once this much un-sent audio has built up…
MAX_SEGMENT_S = 25.0     # …cut even without a pause beyond this
GROQ_SEGMENT_S = 12.0    # Groq bills ≥ 10 s per request
TICK_S = 1.0
KEEP_S = 1.0             # never cut inside the last second (words still forming)


def _fingerprint(x: np.ndarray) -> int:
    return zlib.crc32(np.ascontiguousarray(x[:SR], dtype=np.float32).tobytes())


class CloudStream(QObject):
    """One per app; ``start()`` per recording. ``text_changed`` carries the
    committed transcript so far (for the overlay)."""

    text_changed = Signal(str)

    def __init__(self, snapshot: Callable[[], Optional[np.ndarray]],
                 transcribe: Callable[[np.ndarray, str], str], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._transcribe = transcribe          # (samples, previous_text) → text
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reset()

    def _reset(self) -> None:
        self.text = ""
        self.upto = 0                    # samples already transcribed
        self.segments = 0
        self.failed = ""
        self._fp: Optional[int] = None
        self._gen = 0

    # ── recording lifecycle (UI thread) ──
    def start(self, segment_s: float = SEGMENT_S) -> None:
        self.stop()
        with self._lock:
            self._reset()
        self._segment_s = segment_s
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cloud-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """No new segments; one already in flight still finishes (``take`` waits)."""
        self._stop.set()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── worker ──
    def _loop(self) -> None:
        from thundertalk.core.live_preview import find_commit_point
        from thundertalk.core.speech_gate import has_speech
        while not self._stop.wait(TICK_S):
            x = self._snapshot()
            if x is None or self.failed:
                continue
            pending = x[self.upto:]
            if len(pending) < self._segment_s * SR:
                continue
            cut = find_commit_point(pending, SR, min_s=self._segment_s * 0.6, keep_s=KEEP_S)
            if len(pending) >= MAX_SEGMENT_S * SR:
                cut = cut or int((MAX_SEGMENT_S - KEEP_S) * SR)
            if cut < self._segment_s * 0.5 * SR:
                continue
            piece = pending[:cut]
            self._idle.clear()
            try:
                if self._fp is None:
                    self._fp = _fingerprint(x)
                text = self._transcribe(piece, self.text) if has_speech(piece, SR) else ""
            except Exception as exc:                  # noqa: BLE001 — the final falls back
                self.failed = type(exc).__name__
                print(f"[CloudStream] segment failed ({self.failed}); the final will send the whole take")
                continue
            finally:
                self._idle.set()
            from thundertalk.core.live_preview import join_text
            with self._lock:
                self.text = join_text(self.text, text)
                self.upto += cut
                self.segments += 1
            print(f"[CloudStream] segment {self.segments}: {cut / SR:.1f}s → {len(text)} chars "
                  f"(sent {self.upto / SR:.1f}s so far)")
            self.text_changed.emit(self.text)

    # ── final ──
    def take(self, samples: np.ndarray, timeout: float = 60.0) -> Optional[tuple[str, int]]:
        """(text so far, samples covered) when ``samples`` is the recording this
        stream followed and nothing failed; None means "transcribe it all"."""
        self._stop.set()
        self._idle.wait(timeout)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            out = None
            if not (self.failed or self.upto == 0 or self.upto > len(samples)
                    or self._fp is None or _fingerprint(samples) != self._fp):
                out = (self.text, self.upto)
            self._fp = None              # one-shot: a retry or another clip transcribes in full
            return out
