"""Audio playback with pause and seek — numpy + sounddevice only.

The old Lab player decoded with scipy and resampled with scipy, neither of
which ships in the packaged app, so "Play" silently did nothing there. This
one plays a float32 buffer straight through PortAudio at the device's own
sample rate (resampled once with the FFT resampler in audio_io).

All PortAudio open/close calls go through the shared AudioExecutor so a
wedged CoreAudio HAL can't freeze the GUI thread.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from thundertalk.core import audio_io

_OPEN_TIMEOUT_S = 5.0
_CLOSE_TIMEOUT_S = 2.0


class Player:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._x: Optional[np.ndarray] = None       # source, mono float32
        self._sr = 0
        self._buf: Optional[np.ndarray] = None     # resampled to the device rate
        self._rate = 0
        self._pos = 0                              # frames into _buf
        self._playing = False
        self._ended = False
        self._stream = None
        self.error: str = ""

    # -- state ----------------------------------------------------------
    @property
    def duration(self) -> float:
        return len(self._x) / self._sr if self._x is not None and self._sr else 0.0

    @property
    def position(self) -> float:
        with self._lock:
            if self._buf is None or not self._rate:
                return 0.0
            return min(self._pos / self._rate, self.duration)

    @property
    def fraction(self) -> float:
        d = self.duration
        return self.position / d if d > 0 else 0.0

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def has_audio(self) -> bool:
        return self._x is not None and len(self._x) > 0

    # -- control --------------------------------------------------------
    def load(self, x: np.ndarray, sr: int) -> None:
        self.stop()
        with self._lock:
            self._x = np.ascontiguousarray(x, dtype=np.float32)
            self._sr = int(sr)
            self._buf, self._rate, self._pos = None, 0, 0
            self._ended = False
            self.error = ""

    def clear(self) -> None:
        self.stop()
        with self._lock:
            self._x, self._buf, self._sr, self._rate, self._pos = None, None, 0, 0, 0

    def play(self, start: Optional[float] = None) -> bool:
        """Start (or resume) playback; ``start`` is a fraction 0..1 to jump to."""
        if not self.has_audio:
            return False
        try:
            self._ensure_stream()
        except Exception as exc:                    # no output device, HAL timeout, …
            self.error = str(exc)
            self._playing = False
            return False
        with self._lock:
            if start is not None:
                self._pos = int(min(max(start, 0.0), 1.0) * len(self._buf))
            elif self._ended or self._pos >= len(self._buf):
                self._pos = 0
            self._ended = False
            self._playing = True
        return True

    def pause(self) -> None:
        with self._lock:
            self._playing = False
        self._close_stream()

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._pos = 0
        self._close_stream()

    def seek(self, frac: float) -> None:
        """Move the playhead; keeps playing if it already was."""
        with self._lock:
            if self._buf is None:
                self._prepare(self._default_rate())
            self._pos = int(min(max(frac, 0.0), 1.0) * len(self._buf))
            self._ended = False

    def poll(self) -> bool:
        """Call from the UI timer. Releases the device once playback ended.
        Returns True while audio is playing."""
        if self._ended and self._stream is not None:
            self._close_stream()
        return self._playing

    # -- internals ------------------------------------------------------
    @staticmethod
    def _default_rate() -> int:
        try:
            import sounddevice as sd
            from thundertalk.core.audio_executor import get_executor
            info = get_executor().call(lambda: sd.query_devices(kind="output"), timeout=2.0)
            return int(info["default_samplerate"]) or 48000
        except Exception:
            return 48000

    def _prepare(self, rate: int) -> None:
        assert self._x is not None
        self._buf = self._x if rate == self._sr else audio_io.resample(self._x, self._sr, rate).astype(np.float32)
        self._rate = rate

    def _ensure_stream(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
        import sounddevice as sd
        from thundertalk.core.audio_executor import get_executor
        rate = self._default_rate()
        with self._lock:
            if self._buf is None or self._rate != rate:
                frac = self._pos / len(self._buf) if self._buf is not None and len(self._buf) else 0.0
                self._prepare(rate)
                self._pos = int(frac * len(self._buf))

        def _open():
            s = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", callback=self._callback)
            s.start()
            return s

        stream = get_executor().call(_open, timeout=_OPEN_TIMEOUT_S)
        with self._lock:
            self._stream = stream

    def _close_stream(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            from thundertalk.core.audio_executor import get_executor

            def _close():
                stream.stop()
                stream.close()
            get_executor().call(_close, timeout=_CLOSE_TIMEOUT_S)
        except Exception:
            pass

    def _callback(self, outdata, frames, time_info, status) -> None:
        with self._lock:
            buf = self._buf
            if not self._playing or buf is None:
                outdata[:] = 0
                return
            n = min(frames, len(buf) - self._pos)
            if n > 0:
                outdata[:n, 0] = buf[self._pos: self._pos + n]
                self._pos += n
            if n < frames:
                outdata[max(n, 0):] = 0
                self._playing = False
                self._ended = True
