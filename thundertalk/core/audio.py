"""Microphone recording — only holds the device while actively recording.

All PortAudio interactions are routed through ``AudioExecutor`` so a
hung CoreAudio HAL can never freeze the GUI thread.

Anti-pop measures:
  - Discards first 5 chunks (~50 ms) to skip device-activation transients.
  - Applies short linear fade-in/fade-out (10 ms) on returned samples.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np
import sounddevice as sd

from thundertalk.core.audio_executor import AudioCallTimeout, get_executor

SAMPLE_RATE = 16_000
CHANNELS = 1

_FADE_SAMPLES = int(SAMPLE_RATE * 0.010)  # 10ms fade
_SKIP_CHUNKS = 5                           # discard first N callback chunks (~50ms)

# Watchdog budgets (seconds). Generous enough that healthy CoreAudio
# completes in well under each, tight enough that the GUI never feels
# hung. All numbers are wall-clock on the GUI thread.
#
# _OPEN_TIMEOUT_S covers the worst-case start path:
# Pa_Terminate + Pa_Initialize (~100-300ms typical) + InputStream open
# (~50-200ms). 5.0s leaves headroom for slow USB / Bluetooth handshake.
_OPEN_TIMEOUT_S = 5.0
_CLOSE_TIMEOUT_S = 2.0
_QUERY_TIMEOUT_S = 2.0
_REINIT_TIMEOUT_S = 4.0


def _query_input_device_names() -> list[str]:
    """MUST run on the executor thread."""
    return [
        d["name"]
        for d in sd.query_devices()
        if d["max_input_channels"] > 0
    ]


def _lookup_device_idx(name: str) -> Optional[int]:
    """Search the cached PortAudio device list for an input device by exact
    name match. MUST run on the executor thread. Returns None if not found."""
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    return None


def _resolve_device_idx(name: Optional[str]) -> Optional[int]:
    """Resolve a saved device name to a PortAudio index.

    MUST run on the executor thread. Returns None for system default.

    On a cache miss with a non-empty name, runs a single Pa_Terminate +
    Pa_Initialize cycle and retries — this catches the very common case
    where the saved device (USB mic, Bluetooth headset) wasn't yet
    registered with CoreAudio when the app first imported sounddevice
    (e.g. login-launch racing against Bluetooth pairing). Without the
    retry, every subsequent recording would silently use the system
    default mic and the user has no way to recover short of toggling
    the input device by hand.
    """
    if not name:
        return None
    idx = _lookup_device_idx(name)
    if idx is not None:
        return idx
    print(f"[Audio] Device '{name}' not in PortAudio cache; refreshing...")
    _full_reinit()
    idx = _lookup_device_idx(name)
    if idx is not None:
        print(f"[Audio] Device '{name}' found after refresh (idx={idx})")
        return idx
    print(
        f"[Audio] Device '{name}' STILL not found after refresh — "
        "falling back to system default. The device may be in an "
        "output-only Bluetooth profile (A2DP) with no microphone, or "
        "the OS hasn't enumerated it yet."
    )
    return None


def _full_reinit() -> None:
    """MUST run on the executor thread. Catches hot-plugged hardware."""
    sd._terminate()
    sd._initialize()


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._skip_counter = 0
        self._current_rms: float = 0.0
        self._exec = get_executor()

    @staticmethod
    def list_devices() -> list[str]:
        """Return current input device names. Uses PortAudio's cached list
        from the most recent ``Pa_Initialize``; call ``refresh_devices()``
        first if you need to pick up freshly-plugged hardware."""
        try:
            return get_executor().call(
                _query_input_device_names, timeout=_QUERY_TIMEOUT_S
            )
        except AudioCallTimeout:
            print("[Audio] list_devices timed out — CoreAudio HAL wedged")
            return []

    @staticmethod
    def refresh_devices() -> list[str]:
        """Force PortAudio to re-scan hardware (Pa_Terminate + Pa_Initialize),
        then return the new input device list. Heavy operation — only call
        in response to explicit user intent (e.g. a Refresh button)."""
        ex = get_executor()
        try:
            ex.call(_full_reinit, timeout=_REINIT_TIMEOUT_S)
        except AudioCallTimeout:
            print("[Audio] refresh_devices: re-init timed out — CoreAudio wedged")
            return []
        try:
            return ex.call(_query_input_device_names, timeout=_QUERY_TIMEOUT_S)
        except AudioCallTimeout:
            return []

    def start(self, device: Optional[str] = None) -> None:
        with self._lock:
            self.stop()
            self._chunks.clear()
            self._skip_counter = 0
            self._recording = True

            def _open() -> sd.InputStream:
                dev_idx = _resolve_device_idx(device)
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="float32",
                    device=dev_idx,
                    callback=self._audio_cb,
                )
                stream.start()
                return stream

            try:
                self._stream = self._exec.call(_open, timeout=_OPEN_TIMEOUT_S)
            except AudioCallTimeout:
                print("[Audio] start: open stream timed out — CoreAudio wedged")
                self._stream = None
                self._recording = False
                return

            try:
                actual = self._exec.call(
                    lambda: sd.query_devices(self._stream.device),
                    timeout=_QUERY_TIMEOUT_S,
                )
                print(
                    f"[Audio] Recording on: {actual['name']}  "
                    f"sr={actual['default_samplerate']}  "
                    f"channels={actual['max_input_channels']}"
                )
            except AudioCallTimeout:
                pass  # Logging is best-effort; not worth failing the start.

    def stop(self) -> Optional[np.ndarray]:
        with self._lock:
            self._recording = False
            stream = self._stream
            self._stream = None

            if stream is not None:
                def _close() -> None:
                    stream.stop()
                    stream.close()

                try:
                    self._exec.call(_close, timeout=_CLOSE_TIMEOUT_S)
                except AudioCallTimeout:
                    print(
                        f"[Audio] stop: close timed out after "
                        f"{_CLOSE_TIMEOUT_S:.1f}s — abandoning stream "
                        "to avoid GUI deadlock."
                    )

            if not self._chunks:
                return None
            samples = np.concatenate(self._chunks)
            self._chunks.clear()

            peak = float(np.max(np.abs(samples)))
            rms = float(np.sqrt(np.mean(samples ** 2)))
            print(
                f"[Audio] Recorded {len(samples)} samples "
                f"({len(samples)/SAMPLE_RATE:.1f}s)  "
                f"peak={peak:.4f}  rms={rms:.4f}"
            )

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
