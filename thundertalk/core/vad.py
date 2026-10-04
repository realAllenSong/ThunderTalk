"""Compatibility entry point for bounded, pause-based speech segmentation.

The same adaptive cuts are used by Studio and final dictation. The bound is
supplied by the caller's model metadata, not a universal KV-cache duration.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
MAX_SEGMENT_SECS = 20.0


def segment_audio(samples: np.ndarray, sr: int = SAMPLE_RATE,
                  max_secs: float = MAX_SEGMENT_SECS) -> list[np.ndarray]:
    from thundertalk.core.transcribe import segment_speech

    if len(samples) / sr <= max_secs:
        return [samples]
    spans = segment_speech(samples, sr, target=max_secs * .75, max_len=max_secs, keep_all=True)
    return [samples[int(round(a * sr)):int(round(b * sr))] for a, b in spans]
