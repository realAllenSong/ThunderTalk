"""Was anything actually said? A Silero VAD check before a dictation is decoded.

LLM-based recognisers (Fun-ASR-Nano above all, Qwen3-ASR occasionally) write a
fluent sentence for breathing, a fan or keyboard noise: their decoder has to
emit *something*. FunASR's own pipeline always puts a VAD in front for this
reason. An energy threshold cannot tell a fan from a voice, so the take is
asked of Silero VAD (MIT, 640 KB, bundled in assets/).

Measured on the SH2 corpus (39 noise and silence clips, 108 speech clips of
TTS, system voices and public recordings): threshold 0.5 rejects 39/39 noise
clips and none of the speech clips.

If the model or sherpa-onnx is unavailable the gate answers "speech" (the
old energy check decides), so a broken gate can never swallow a dictation.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

import numpy as np

SAMPLE_RATE = 16_000
THRESHOLD = 0.5
MIN_SPEECH_SECONDS = 0.25      # shortest span Silero reports, and the gate

_lock = threading.Lock()
_config = None
_unavailable = False


def _model_path() -> str:
    from thundertalk import asset_path
    return asset_path("silero_vad.onnx")


def _vad_config():
    global _config, _unavailable
    if _config is not None or _unavailable:
        return _config
    try:
        import sherpa_onnx
        path = _model_path()
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = path
        cfg.silero_vad.threshold = THRESHOLD
        cfg.silero_vad.min_silence_duration = 0.25
        cfg.silero_vad.min_speech_duration = MIN_SPEECH_SECONDS
        cfg.sample_rate = SAMPLE_RATE
        cfg.num_threads = 1
        cfg.provider = "cpu"
        _config = cfg
    except Exception as exc:                       # gate off, energy check decides
        print(f"[VAD] Speech gate unavailable: {type(exc).__name__}: {exc}")
        _unavailable = True
    return _config


def speech_seconds(samples: np.ndarray, sr: int = SAMPLE_RATE) -> Optional[float]:
    """Seconds of detected speech, or None when the gate cannot run."""
    if sr != SAMPLE_RATE or samples is None:
        return None
    with _lock:
        cfg = _vad_config()
        if cfg is None:
            return None
        try:
            import sherpa_onnx
            vad = sherpa_onnx.VoiceActivityDetector(
                cfg, buffer_size_in_seconds=max(30.0, len(samples) / sr + 2))
            win = cfg.silero_vad.window_size
            x = np.ascontiguousarray(samples, dtype=np.float32)
            # trailing silence lets a span that runs to the end close
            x = np.concatenate([x, np.zeros(sr // 2, np.float32)])
            for i in range(0, len(x) - win + 1, win):
                vad.accept_waveform(x[i:i + win])
            vad.flush()
            total = 0
            while not vad.empty():
                total += len(vad.front.samples)
                vad.pop()
            return total / sr
        except Exception as exc:
            print(f"[VAD] Speech gate failed: {type(exc).__name__}: {exc}")
            return None


def has_speech(samples: np.ndarray, sr: int = SAMPLE_RATE) -> bool:
    """True unless the clip is quiet or Silero hears no voice in it."""
    from thundertalk.core.dictation import has_audio_energy

    if samples is None or not has_audio_energy(samples, sr):
        return False
    seconds = speech_seconds(samples, sr)
    return True if seconds is None else seconds >= MIN_SPEECH_SECONDS
