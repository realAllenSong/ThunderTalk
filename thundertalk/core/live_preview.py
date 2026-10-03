"""Live transcript preview while dictating.

While the hotkey recording runs, a timer on the UI thread periodically takes
the audio captured so far and re-recognizes it with the loaded model on a
background thread. The result is only shown in the floating indicator — the
pasted text still comes from the normal full-clip recognition after stop, so
accuracy never depends on the preview.

Cost is bounded for long dictations: earlier speech is decoded once and kept
("committed") at a pause, and each tick only re-decodes the uncommitted tail
(at most ``window_s`` seconds). Ticks never overlap — a tick that comes while
the previous one is still decoding is skipped — and a slow model stretches
the interval and shrinks the window instead of lagging behind. One that
can't keep up at all (MAX_DECODE_S) is switched off for that recording.

Every decode holds GPU_LOCK, taken without waiting: if a synthesis or file
transcription holds the GPU, the tick is skipped. After stop() no new decode
starts; the final recognition calls wait_idle() so it never runs at the same
time as a preview decode (the ONNX models share one recognizer).
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from thundertalk.core.gpu_lock import GPU_LOCK

SAMPLE_RATE = 16_000

BASE_INTERVAL_MS = 1200      # tick period when the model keeps up
MAX_INTERVAL_MS = 4000       # slowest we go before the preview gets pointless
FIRST_TICK_MS = 900          # first preview once there's ~1 s of audio
MIN_AUDIO_S = 0.6            # don't decode less than this
WINDOW_S = 10.0              # longest uncommitted tail we re-decode
MIN_WINDOW_S = 3.0
TARGET_DECODE_S = 0.6        # aim for decodes no longer than this
KEEP_TAIL_S = 1.0            # never commit the last second (words still forming)
# A single decode slower than this means the model can't keep up on this
# machine right now. The final can wait behind one in-flight decode (stop()
# cancels between a commit and its tail decode, not inside one), so the
# preview stops for the rest of the recording (the text shown so far stays).
MAX_DECODE_S = 2.0


def preview_wanted(settings) -> bool:
    """Live preview for this dictation? Off in direct-translation mode: the
    pasted text is another language, so the spoken words would mislead."""
    if not settings.get("live_preview"):
        return False
    tgt = settings.get("translation_target")
    mode = settings.get("translation_mode") or "direct"
    return not (tgt and tgt != "off" and mode == "direct")


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF


def join_text(a: str, b: str) -> str:
    """Join two transcript pieces; no space between CJK characters."""
    a, b = a.strip(), b.strip()
    if not a or not b:
        return a or b
    sep = "" if (_is_cjk(a[-1]) or _is_cjk(b[0])) else " "
    return a + sep + b


def find_commit_point(audio: np.ndarray, sr: int = SAMPLE_RATE,
                      min_s: float = 1.0, keep_s: float = KEEP_TAIL_S) -> int:
    """Sample index where *audio* can be split for committing: the middle of
    the latest pause that leaves at least ``keep_s`` seconds after it, or the
    quietest moment in that range when the speaker never pauses."""
    frame = int(sr * 0.03)
    n = len(audio) // frame
    lo, hi = int(min_s * sr) // frame, (len(audio) - int(keep_s * sr)) // frame
    if n == 0 or hi <= lo:
        return max(0, len(audio) - int(keep_s * sr))
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    # Relative to the noise floor, but never above a third of typical speech
    # level: with few pauses the 10th percentile is itself speech.
    floor = float(np.percentile(rms, 10))
    silent = rms < max(0.006, min(floor * 2.5, float(np.median(rms)) * 0.3))
    min_run = max(1, int(0.2 / 0.03))
    best = -1
    run = 0
    for i in range(lo, hi):
        run = run + 1 if silent[i] else 0
        if run >= min_run:
            best = i - run // 2          # keep updating → latest pause wins
    if best < 0:
        # No pause: cut at the quietest smoothed frame.
        smooth = np.convolve(rms, np.ones(5) / 5, mode="same")
        best = lo + int(np.argmin(smooth[lo:hi]))
    return best * frame


class LivePreview(QObject):
    """Schedules preview decodes during a recording; emits ``text_changed``.

    ``snapshot()`` returns all 16 kHz samples captured so far (or None);
    ``recognize(samples)`` returns the text for a clip. Both are called on
    the worker thread.
    """

    text_changed = Signal(str)
    _result = Signal(int, str)          # generation, text (worker → UI thread)

    def __init__(
        self,
        snapshot: Callable[[], Optional[np.ndarray]],
        recognize: Callable[[np.ndarray], str],
        *,
        lock=GPU_LOCK,
        interval_ms: int = BASE_INTERVAL_MS,
        first_tick_ms: int = FIRST_TICK_MS,
        window_s: float = WINDOW_S,
        sample_rate: int = SAMPLE_RATE,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._recognize = recognize
        self._lock = lock
        self._sr = sample_rate
        self._base_interval = interval_ms
        self._first_tick = first_tick_ms
        self._max_window = window_s
        self._window = window_s
        self._interval = interval_ms

        self._gen = 0
        self._active = False
        self._idle = threading.Event()
        self._idle.set()
        self._cancel = threading.Event()
        self._committed_text = ""
        self._committed_upto = 0         # samples already folded into committed text
        self._last_text = ""
        self._too_slow = False
        self.stats: dict = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._result.connect(self._on_result)

    # ── public API (UI thread) ───────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @property
    def busy(self) -> bool:
        return not self._idle.is_set()

    @property
    def interval_ms(self) -> int:
        return self._interval

    @property
    def window_s(self) -> float:
        return self._window

    def start(self) -> None:
        self._gen += 1
        self._active = True
        self._cancel.clear()
        self._committed_text = ""
        self._committed_upto = 0
        self._last_text = ""
        self._too_slow = False
        self._interval = self._base_interval
        self._window = self._max_window
        self.stats = {"ran": 0, "skipped_busy": 0, "skipped_lock": 0, "errors": 0,
                      "decode_s": [], "latency_s": [], "commits": 0}
        self._timer.start(self._first_tick)

    def stop(self) -> None:
        """Stop scheduling and drop any result still in flight. Returns at
        once; a decode already running finishes in the background."""
        if not self._active:
            return
        self._gen += 1
        self._active = False
        self._cancel.set()
        self._timer.stop()
        d = self.stats.get("decode_s") or [0.0]
        print(f"[Preview] ran={self.stats.get('ran', 0)} "
              f"skipped={self.stats.get('skipped_busy', 0)}+{self.stats.get('skipped_lock', 0)} "
              f"commits={self.stats.get('commits', 0)} "
              f"decode avg={sum(d) / len(d):.2f}s max={max(d):.2f}s "
              f"interval={self._interval}ms window={self._window:.1f}s")

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Block until no preview decode is running (call off the UI thread)."""
        return self._idle.wait(timeout)

    # ── scheduling ───────────────────────────────────────────────────────

    def _tick(self) -> None:
        if not self._active or self._too_slow:
            return
        if self.busy:
            self.stats["skipped_busy"] += 1
        else:
            self._idle.clear()
            gen = self._gen
            threading.Thread(target=self._run, args=(gen,), daemon=True,
                             name="live-preview").start()
        self._timer.start(self._interval)

    def _stale(self, gen: int) -> bool:
        return gen != self._gen or self._cancel.is_set()

    def _run(self, gen: int) -> None:
        t_tick = time.perf_counter()
        try:
            if not self._lock.acquire(blocking=False):
                self.stats["skipped_lock"] += 1
                return
            try:
                if self._stale(gen):
                    return
                n0 = len(self.stats["decode_s"])
                text = self._decode(gen)
                slowest = max(self.stats["decode_s"][n0:], default=0.0)
            finally:
                self._lock.release()
            dt = time.perf_counter() - t_tick
            if text is not None and not self._stale(gen):
                # Interval follows the whole tick (a commit adds a second decode).
                self._interval = int(max(self._base_interval, min(MAX_INTERVAL_MS, dt * 2500)))
                if slowest > MAX_DECODE_S:
                    self._too_slow = True
                    print(f"[Preview] {slowest:.1f}s per decode is too slow; preview off "
                          "for the rest of this recording")
                self.stats["latency_s"].append(dt)
                self._result.emit(gen, text)
        except Exception as exc:          # a preview must never break dictation
            self.stats["errors"] += 1
            print(f"[Preview] decode failed: {exc}")
        finally:
            self._idle.set()

    def _decode(self, gen: int) -> Optional[str]:
        audio = self._snapshot()
        if audio is None:
            return None
        tail = audio[self._committed_upto:]
        if len(tail) < MIN_AUDIO_S * self._sr:
            return None

        if len(tail) > self._window * self._sr:
            # Fold the older part of the tail into the committed text at a
            # pause, so later ticks only re-decode the newest speech.
            cut = find_commit_point(tail, self._sr)
            if cut > MIN_AUDIO_S * self._sr:
                piece = self._timed(tail[:cut])
                if self._stale(gen):
                    return None
                self._committed_text = join_text(self._committed_text, piece)
                self._committed_upto += cut
                tail = tail[cut:]
                self.stats["commits"] += 1

        text = self._timed(tail) if len(tail) >= MIN_AUDIO_S * self._sr else ""
        return join_text(self._committed_text, text)

    def _timed(self, samples: np.ndarray) -> str:
        t0 = time.perf_counter()
        text = self._recognize(samples) or ""
        dt = time.perf_counter() - t0
        self.stats["ran"] += 1
        self.stats["decode_s"].append(dt)
        self._adapt(dt, len(samples) / self._sr)
        return text.strip()

    def _adapt(self, decode_s: float, audio_s: float) -> None:
        """Slow models decode less audio per tick (and _run stretches the
        interval), so the preview keeps up and the GPU stays mostly free."""
        rtf = decode_s / audio_s if audio_s > 0 else 0.0
        if rtf > 0:
            self._window = max(MIN_WINDOW_S, min(self._max_window, TARGET_DECODE_S / rtf))

    # ── results (UI thread) ──────────────────────────────────────────────

    def _on_result(self, gen: int, text: str) -> None:
        if gen != self._gen or not self._active:
            return                        # a stale result from before stop()
        text = " ".join(text.split())     # MOSS speaker turns are newline-separated
        if text != self._last_text:
            self._last_text = text
            self.text_changed.emit(text)
