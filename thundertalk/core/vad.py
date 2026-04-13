"""Simple energy-based voice activity segmentation for long recordings.

Splits audio into segments at silence boundaries so each segment fits
within the ASR model's context window. This avoids KV-cache overflow
for the sherpa-onnx ONNX backend (max ~2.5 min per segment).

mlx-qwen3-asr handles chunking internally (>1200s) so this module is
mainly needed for the sherpa-onnx path.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000

MAX_SEGMENT_SECS = 120        # target max per segment (2 min, safe for 4096 KV cache)
MIN_SILENCE_SECS = 0.3        # minimum silence gap to consider as boundary
SILENCE_THRESHOLD = 0.01      # RMS amplitude below this = silence
FRAME_SECS = 0.025            # analysis frame duration


def segment_audio(
    samples: np.ndarray,
    sr: int = SAMPLE_RATE,
    max_secs: float = MAX_SEGMENT_SECS,
) -> list[np.ndarray]:
    """Split *samples* at silence boundaries into segments <= *max_secs*.

    Returns a list of numpy arrays. If audio is already short enough,
    returns a single-element list.
    """
    duration = len(samples) / sr
    if duration <= max_secs:
        return [samples]

    frame_len = int(sr * FRAME_SECS)
    min_silence_frames = int(MIN_SILENCE_SECS / FRAME_SECS)
    max_segment_samples = int(max_secs * sr)

    # Compute per-frame RMS energy
    n_frames = len(samples) // frame_len
    if n_frames == 0:
        return [samples]

    frames = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))

    # Find silence regions (consecutive low-energy frames)
    is_silent = rms < SILENCE_THRESHOLD
    silence_starts: list[int] = []
    run = 0
    for i, s in enumerate(is_silent):
        if s:
            run += 1
        else:
            if run >= min_silence_frames:
                mid = i - run // 2
                silence_starts.append(mid * frame_len)
            run = 0
    if run >= min_silence_frames:
        mid = n_frames - run // 2
        silence_starts.append(mid * frame_len)

    if not silence_starts:
        # No silence found — hard-split at max_segment_samples
        return _hard_split(samples, max_segment_samples)

    # Greedy segmentation using silence boundaries
    segments: list[np.ndarray] = []
    seg_start = 0

    for boundary in silence_starts:
        if boundary - seg_start >= max_segment_samples:
            segments.append(samples[seg_start:boundary])
            seg_start = boundary

    # Last segment
    if seg_start < len(samples):
        remaining = samples[seg_start:]
        if len(remaining) > max_segment_samples:
            segments.extend(_hard_split(remaining, max_segment_samples))
        else:
            segments.append(remaining)

    return segments if segments else [samples]


def _hard_split(samples: np.ndarray, chunk_size: int) -> list[np.ndarray]:
    """Fallback: split at fixed intervals when no silence is detected."""
    parts = []
    for i in range(0, len(samples), chunk_size):
        parts.append(samples[i : i + chunk_size])
    return parts
