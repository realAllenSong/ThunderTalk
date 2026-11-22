"""Programme loudness (ITU-R BS.1770, the LUFS of broadcast and streaming) and
the output levelling built on it.

Speech used to leave the app peak-normalised, so how loud a clip sounded
depended on its single highest sample: a voice with sharp consonants came out
quieter than a smooth one, and two clips for the same video did not match.
Now every clip is brought to the same loudness, the pieces inside a clip are
matched by loudness too, and the few peaks that would then clip are held back
by a short look-ahead limiter.

Pure numpy: the K-weighting filter is applied in the frequency domain, one
400 ms block at a time, which is exact for the block's energy and needs no
filter library.
"""

from __future__ import annotations

import math

import numpy as np

SPEECH_LUFS = -16.0          # the usual target for spoken audio (podcasts, voice-over stems)
PEAK_CEILING = 0.891         # -1 dBFS
_BLOCK_S, _HOP_S = 0.4, 0.1
_ABS_GATE, _REL_GATE = -70.0, -10.0
SILENT = -120.0


def _k_weight_power(freqs: np.ndarray, sr: int) -> np.ndarray:
    """|H(f)|² of the BS.1770 pre-filter (high shelf +4 dB at 1.5 kHz, then a 38 Hz high-pass)."""
    def biquad(b, a) -> np.ndarray:
        z = np.exp(-2j * np.pi * freqs / sr)
        return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)) ** 2

    amp = 10 ** (4.0 / 40)
    w = 2 * math.pi * 1500.0 / sr
    al = math.sin(w) / (2 * (1 / math.sqrt(2)))
    c = math.cos(w)
    shelf = biquad(
        (amp * ((amp + 1) + (amp - 1) * c + 2 * math.sqrt(amp) * al), -2 * amp * ((amp - 1) + (amp + 1) * c),
         amp * ((amp + 1) + (amp - 1) * c - 2 * math.sqrt(amp) * al)),
        ((amp + 1) - (amp - 1) * c + 2 * math.sqrt(amp) * al, 2 * ((amp - 1) - (amp + 1) * c),
         (amp + 1) - (amp - 1) * c - 2 * math.sqrt(amp) * al))
    w = 2 * math.pi * 38.0 / sr
    al = math.sin(w) / (2 * 0.5)
    c = math.cos(w)
    high = biquad(((1 + c) / 2, -(1 + c), (1 + c) / 2), (1 + al, -2 * c, 1 - al))
    return shelf * high


def _block_powers(x: np.ndarray, sr: int) -> np.ndarray:
    """Mean square of the K-weighted signal in each 400 ms block (75 % overlap)."""
    n, hop = int(_BLOCK_S * sr), int(_HOP_S * sr)
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    scale = 1.0
    if len(x) < n:
        scale = n / max(1, len(x))                     # a short piece: one padded block, the padding not counted
        x = np.pad(x, (0, n - len(x)))
    starts = np.arange(0, len(x) - n + 1, hop)
    blocks = x[starts[:, None] + np.arange(n)[None, :]]
    spec = np.abs(np.fft.rfft(blocks, axis=1)) ** 2
    weight = _k_weight_power(np.fft.rfftfreq(n, 1 / sr), sr)
    spec[:, 1:-1] *= 2                                  # one-sided spectrum
    return (spec * weight).sum(axis=1) / (n * n) * scale


def integrated_lufs(x: np.ndarray, sr: int) -> float:
    """Gated programme loudness of mono audio; ``SILENT`` when there is nothing to measure."""
    if len(x) == 0:
        return SILENT
    p = _block_powers(x, sr)
    lk = -0.691 + 10 * np.log10(np.maximum(p, 1e-12))
    keep = lk > _ABS_GATE
    if not keep.any():
        return SILENT
    rel = -0.691 + 10 * math.log10(float(p[keep].mean())) + _REL_GATE
    keep &= lk > rel
    return float(-0.691 + 10 * math.log10(float(p[keep].mean()))) if keep.any() else SILENT


def limit(x: np.ndarray, sr: int, ceiling: float = PEAK_CEILING) -> np.ndarray:
    """Hold peaks at ``ceiling`` with a gain that starts easing down 5 ms ahead
    of each one and recovers over the next 5 ms; untouched where nothing exceeds it."""
    x = np.asarray(x, dtype=np.float32)
    if len(x) == 0 or float(np.max(np.abs(x))) <= ceiling:
        return x
    step = max(1, int(0.001 * sr))                      # 1 ms cells
    n = math.ceil(len(x) / step)
    peaks = np.abs(np.pad(x, (0, n * step - len(x)))).reshape(n, step).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-9))
    w = 5
    padded = np.pad(need, w, mode="edge")
    held = np.min(np.stack([padded[k:k + n] for k in range(2 * w + 1)]), axis=0)      # ±5 ms minimum
    smooth = np.convolve(np.pad(held, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    smooth = np.minimum(smooth, need)                   # never above what a cell needs
    centres = np.arange(n) * step + step / 2
    gain = np.interp(np.arange(len(x)), centres, smooth)
    y = x * gain.astype(np.float32)
    return np.clip(y, -ceiling, ceiling)


def normalize(x: np.ndarray, sr: int, target: float = SPEECH_LUFS,
              ceiling: float = PEAK_CEILING) -> tuple[np.ndarray, float]:
    """``x`` at ``target`` LUFS with peaks held under ``ceiling``. Returns the
    audio and the loudness it had before. Silence is returned unchanged."""
    before = integrated_lufs(x, sr)
    if before <= _ABS_GATE:
        return np.asarray(x, dtype=np.float32), before
    gain = 10 ** ((target - before) / 20)
    return limit(np.asarray(x, dtype=np.float32) * gain, sr, ceiling), before


def match_gains(pieces: list[np.ndarray], sr: int, max_db: float = 6.0) -> list[float]:
    """Gain for each piece that brings it to the median loudness of them all,
    within ±``max_db`` (a piece that far off is a bad take, not a level problem)."""
    lufs = [integrated_lufs(p, sr) for p in pieces]
    heard = [v for v in lufs if v > _ABS_GATE]
    if not heard:
        return [1.0] * len(pieces)
    target = float(np.median(heard))
    return [1.0 if v <= _ABS_GATE else 10 ** (min(max(target - v, -max_db), max_db) / 20) for v in lufs]
