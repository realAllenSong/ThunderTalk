"""Microphone recording — only holds the device while actively recording.

Anti-pop measures:
  - Discards first 5 chunks (~50ms) to skip device-activation transients.
  - Applies short linear fade-in/fade-out (10ms) on returned samples.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
CHANNELS = 1

_FADE_SAMPLES = int(SAMPLE_RATE * 0.010)  # 10ms fade
_SKIP_CHUNKS = 5                           # discard first N callback chunks (~50ms)


def _refresh_devices() -> None:
    """Force PortAudio to re-scan devices so we pick up hot-plugged hardware."""
    sd._terminate()
    sd._initialize()


def _resolve_device(name: Optional[str]) -> Optional[int]:
    """Resolve a device name to an index, or None for system default."""
    _refresh_devices()

    if not name:
        return None

    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i

    print(f"[Audio] Device '{name}' not found, falling back to system default")
    return None


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._skip_counter = 0
        self._current_rms: float = 0.0

    @staticmethod
    def list_devices() -> list[str]:
        _refresh_devices()
        return [
            d["name"]
            for d in sd.query_devices()
            if d["max_input_channels"] > 0
        ]

    def start(self, device: Optional[str] = None) -> None:
        with self._lock:
            self.stop()
            self._chunks.clear()
            self._skip_counter = 0
            self._recording = True

            dev_idx = _resolve_device(device)

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=dev_idx,
                callback=self._audio_cb,
            )
            self._stream.start()

            actual = sd.query_devices(self._stream.device)
            print(f"[Audio] Recording on: {actual['name']}  "
                  f"sr={actual['default_samplerate']}  "
                  f"channels={actual['max_input_channels']}")

    def stop(self) -> Optional[np.ndarray]:
        with self._lock:
            self._recording = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            if not self._chunks:
                return None
            samples = np.concatenate(self._chunks)
            self._chunks.clear()

            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples ** 2)))
            print(f"[Audio] Recorded {len(samples)} samples ({len(samples)/SAMPLE_RATE:.1f}s)  "
                  f"peak={peak:.4f}  rms={rms:.4f}")

            if len(samples) < _FADE_SAMPLES * 2:
                return samples

            fade_in = np.linspace(0.0, 1.0, _FADE_SAMPLES, dtype=np.float32)
            samples[:_FADE_SAMPLES] *= fade_in

            fade_out = np.linspace(1.0, 0.0, _FADE_SAMPLES, dtype=np.float32)
            samples[-_FADE_SAMPLES:] *= fade_out

            return samples

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_rms(self) -> float:
        return self._current_rms

    def _audio_cb(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if self._recording:
            if self._skip_counter < _SKIP_CHUNKS:
                self._skip_counter += 1
                return
            chunk = indata[:, 0].copy()
            self._chunks.append(chunk)
            self._current_rms = float(np.sqrt(np.mean(chunk ** 2)))
