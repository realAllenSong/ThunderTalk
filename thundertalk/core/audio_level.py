"""Bring quiet recordings up to a normal speaking level before anything judges them.

A far-away or low-gain microphone records speech at -50…-60 dBFS. Every
energy rule in the app (silence skip, pause cuts, "is this speech") was tuned
at normal levels, so such a take used to be dropped as silence without being
decoded: in the SH2 corpus 5 of 8 FLEURS English clips came back empty.

Loud-enough audio is returned unchanged (the same array), digital silence too.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
TARGET = 0.05          # 95th-percentile 20 ms frame RMS of normal dictation
QUIET = 0.03           # only audio below this is raised
MAX_GAIN = 100.0       # +40 dB
SILENT_PEAK = 1e-4     # below this it is digital silence, not a quiet voice


def loudness(samples: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """95th-percentile 20 ms frame RMS: the level of the loud (spoken) parts."""
    frame = max(1, int(sr * 0.02))
    n = len(samples) // frame
    if n == 0:
        return float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
    rms = np.sqrt(np.mean(np.square(samples[: n * frame].reshape(n, frame)), axis=1))
    return float(np.percentile(rms, 95))


def is_digital_silence(samples: np.ndarray) -> bool:
    return len(samples) == 0 or float(np.max(np.abs(samples))) < SILENT_PEAK


def normalize_quiet(samples: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    if samples is None or is_digital_silence(samples):
        return samples
    level = loudness(samples, sr)
    if level >= QUIET or level <= 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    gain = min(TARGET / level, MAX_GAIN, 0.95 / peak)
    if gain <= 1.0:
        return samples
    return (samples * gain).astype(np.float32, copy=False)
