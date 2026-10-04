"""Bounded local decoding and recovery of a completed dictation."""

from __future__ import annotations

import re
import time
from dataclasses import replace

import numpy as np


def has_audio_energy(samples: np.ndarray, sr: int = 16000) -> bool:
    """Conservative silence gate: uncertain speech must remain recoverable."""
    frame = max(1, int(sr * 0.02))
    n = len(samples) // frame
    if not n:
        return False
    rms = np.sqrt(np.mean(samples[:n * frame].reshape(n, frame) ** 2, axis=1))
    return bool(np.count_nonzero(rms >= 0.003) >= 3)


_WORDS = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*|[^\W_]|[^\s]", re.UNICODE)


def join_chunks(left: str, right: str, *, overlap: bool = True) -> str:
    """Remove only a matching suffix/prefix at an overlapping audio boundary."""
    from thundertalk.core.live_preview import join_text

    left, right = left.strip(), right.strip()
    if overlap and left and right:
        a = [m for m in _WORDS.finditer(left) if any(c.isalnum() for c in m.group())]
        b = [m for m in _WORDS.finditer(right) if any(c.isalnum() for c in m.group())]
        for n in range(min(12, len(a), len(b)), 0, -1):
            keys_a = [m.group().casefold() for m in a[-n:]]
            keys_b = [m.group().casefold() for m in b[:n]]
            # A single CJK character is too ambiguous to remove.
            if keys_a == keys_b and (n >= 2 or len(keys_b[0]) >= 3):
                right = right[b[n - 1].end():].lstrip(" ,，。.!！?？;；:：")
                break
    return join_text(left, right)


def decode_chunks(decode, samples: np.ndarray, sr: int, max_secs: float):
    """Use Studio's adaptive pause cuts, with bounded 150 ms edge context.

    Preserve all spans, including ones VAD is uncertain about. Every sample
    belongs to a span; overlap is additional context, never omitted audio.
    """
    from thundertalk.core.asr import AsrResult
    from thundertalk.core.transcribe import _has_speech, segment_speech

    pad = int(0.15 * sr)
    core_limit = max_secs - 2 * pad / sr
    if core_limit <= 4:
        step = max(1, int(core_limit * sr))
        spans = [(i / sr, min(len(samples), i + step) / sr)
                 for i in range(0, len(samples), step)]
    else:
        spans = segment_speech(samples, sr, target=core_limit * 0.75,
                               max_len=core_limit, keep_all=True)
    text, total_ms, incomplete, model, backend = "", 0, False, "unknown", ""
    for a, b in spans:
        start, end = int(round(a * sr)), int(round(b * sr))
        lo, hi = max(0, start - pad), min(len(samples), end + pad)
        # Floating-point pause coordinates must never exceed the cap.
        hi = min(hi, lo + int(max_secs * sr))
        r = decode(samples[lo:hi], sr)
        model, backend = getattr(r, "model", "unknown"), getattr(r, "backend", "")
        total_ms += getattr(r, "inference_ms", 0)
        incomplete |= bool(getattr(r, "truncated", False))
        # Breath/noise at the end of a take may have energy without speech.
        # Keep and decode that audio, but do not call an empty noise span loss.
        incomplete |= (not r.text.strip() and not getattr(r, "no_speech", False)
                       and _has_speech(samples[start:end], sr))
        boundary = samples[max(0, start - pad):min(len(samples), start + pad)]
        text = join_chunks(text, r.text, overlap=start > 0 and has_audio_energy(boundary, sr))
    duration = len(samples) / sr
    return AsrResult(text, duration, total_ms, model, backend,
                     total_ms / 1000 / duration if duration else 0,
                     truncated=incomplete, recovery_source="chunks")


def recover_final(engine, samples: np.ndarray, preview: str = "", *, initial=None):
    """Retry an empty/truncated final result off the UI thread, then preview.

    Covers unknown future engines too. An exception or partial decode cannot
    erase the take. The app persists failed/preview takes and their source.
    """
    from thundertalk.core.asr import AsrResult

    t0 = time.perf_counter()
    try:
        result = initial if initial is not None else engine.recognize(samples)
    except Exception as exc:
        print(f"[Dictation] Final decode failed: {type(exc).__name__}")
        result = AsrResult("", len(samples) / 16000, 0,
                           getattr(engine, "current_model", None) or "unknown",
                           getattr(engine, "active_backend", ""), truncated=True)
    if not isinstance(result, AsrResult):
        result = AsrResult(result.text, getattr(result, "duration_secs", len(samples) / 16000),
                           getattr(result, "inference_ms", 0), getattr(result, "model", "unknown"),
                           getattr(result, "backend", ""), getattr(result, "rtf", 0),
                           truncated=bool(getattr(result, "truncated", False)))
    speech = bool(preview.strip()) or has_audio_energy(samples)
    if speech and (not result.text.strip() or getattr(result, "truncated", False)):
        limit = min(float(getattr(engine, "max_clip_seconds", 20)), 8.0)
        try:
            retry = decode_chunks(engine.recognize, samples, 16000, limit)
            if retry.text.strip():
                result = replace(retry, recovery_source="redecoded")
        except Exception as exc:
            print(f"[Dictation] Chunk recovery failed: {type(exc).__name__}")
        if preview.strip() and (not result.text.strip() or getattr(result, "truncated", False)):
            result = replace(result, text=preview.strip(), truncated=False,
                             recovery_source="preview")
        elif not result.text.strip():
            result = replace(result, recovery_source="failed")
        elif getattr(result, "truncated", False):
            result = replace(result, recovery_source="partial")
    elapsed = int((time.perf_counter() - t0) * 1000)
    ms = max(result.inference_ms, elapsed)
    return replace(result, inference_ms=ms,
                   rtf=ms / 1000 / result.duration_secs if result.duration_secs else 0)
