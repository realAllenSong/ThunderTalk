"""Microphone recording — only holds the device while actively recording.

Anti-pop measures:
  - Discards first 2 chunks (~20ms) to skip device-activation transients.
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
_SKIP_CHUNKS = 2                           # discard first N callback chunks


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._skip_counter = 0

    @staticmethod
    def list_devices() -> list[str]:
        return [
            d["name"]
            for d in sd.query_devices()
            if d["max_input_channels"] > 0  # type: ignore[arg-type]
        ]

    def start(self, device: Optional[str] = None) -> None:
        with self._lock:
            self.stop()
            self._chunks.clear()
            self._skip_counter = 0
            self._recording = True

            dev_idx = None
            if device:
                for i, d in enumerate(sd.query_devices()):
                    if d["name"] == device and d["max_input_channels"] > 0:  # type: ignore[arg-type]
                        dev_idx = i
                        break

            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=dev_idx,
                callback=self._audio_cb,
            )
            self._stream.start()

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

            if len(samples) < _FADE_SAMPLES * 2:
                return samples

            # Fade-in
            fade_in = np.linspace(0.0, 1.0, _FADE_SAMPLES, dtype=np.float32)
            samples[:_FADE_SAMPLES] *= fade_in

            # Fade-out
            fade_out = np.linspace(1.0, 0.0, _FADE_SAMPLES, dtype=np.float32)
            samples[-_FADE_SAMPLES:] *= fade_out

            return samples

    @property
    def is_recording(self) -> bool:
        return self._recording

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
            self._chunks.append(indata[:, 0].copy())
