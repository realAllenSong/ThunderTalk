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


_PAD_S = 0.15          # audio kept on both sides of a span as context


def _spans(samples: np.ndarray, sr: int, max_secs: float, target: float | None = None):
    """Pause cuts for ``samples``: (start_s, end_s) spans, none longer than one decode window."""
    from thundertalk.core.transcribe import segment_speech

    core_limit = max_secs - 2 * _PAD_S
    if core_limit <= 4:
        step = max(1, int(core_limit * sr))
        return [(i / sr, min(len(samples), i + step) / sr) for i in range(0, len(samples), step)]
    return segment_speech(samples, sr, target=target or core_limit * 0.75, max_len=core_limit, keep_all=True)


def decode_span(decode, samples: np.ndarray, sr: int, start: int, end: int, max_secs: float, text: str = ""):
    """Decode samples[start:end] with edge context and join it to ``text``.
    Returns (text, result, incomplete)."""
    from thundertalk.core.transcribe import _has_speech

    pad = int(_PAD_S * sr)
    lo, hi = max(0, start - pad), min(len(samples), end + pad)
    # Floating-point pause coordinates must never exceed the cap.
    hi = min(hi, lo + int(max_secs * sr))
    r = decode(samples[lo:hi], sr)
    # Breath/noise at the end of a take may have energy without speech.
    # Keep and decode that audio, but do not call an empty noise span loss.
    incomplete = bool(getattr(r, "truncated", False)) or (
        not r.text.strip() and not getattr(r, "no_speech", False) and _has_speech(samples[start:end], sr))
    boundary = samples[max(0, start - pad):min(len(samples), start + pad)]
    return join_chunks(text, r.text, overlap=start > 0 and has_audio_energy(boundary, sr)), r, incomplete


def finished_span(samples: np.ndarray, sr: int, start: int, max_secs: float, *,
                  target: float | None = None, keep_s: float = 1.0):
    """While the speaker goes on: the next span after ``start`` that is over, as (start, end)
    samples, or None. A span is over when the pause cuts put another one after it and it
    ends at least ``keep_s`` before the newest audio (words there are still forming)."""
    pending = samples[start:]
    if len(pending) / sr <= max_secs - 2 * _PAD_S:
        return None                              # still fits one window: nothing is cut yet
    spans = _spans(pending, sr, max_secs, target)
    if len(spans) < 2 or spans[0][1] > len(pending) / sr - keep_s:
        return None
    return start + int(round(spans[0][0] * sr)), start + int(round(spans[0][1] * sr))


def decode_chunks(decode, samples: np.ndarray, sr: int, max_secs: float, *,
                  start: int = 0, prefix: str = "", target: float | None = None):
    """Use Studio's adaptive pause cuts, with bounded 150 ms edge context.

    Preserve all spans, including ones VAD is uncertain about. Every sample
    belongs to a span; overlap is additional context, never omitted audio.

    ``start``/``prefix``: the take up to sample ``start`` is already decoded
    as ``prefix`` (core/local_stream.py did it while the user was speaking);
    only the rest is decoded, cut the same way.
    """
    from thundertalk.core.asr import AsrResult

    text, total_ms, incomplete, model, backend = prefix, 0, False, "unknown", ""
    for a, b in _spans(samples[start:], sr, max_secs, target):
        text, r, bad = decode_span(decode, samples, sr, start + int(round(a * sr)), start + int(round(b * sr)),
                                   max_secs, text)
        model, backend = getattr(r, "model", "unknown"), getattr(r, "backend", "")
        total_ms += getattr(r, "inference_ms", 0)
        incomplete |= bad
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
    from thundertalk.core.audio_level import normalize_quiet
    from thundertalk.core.speech_gate import has_speech

    t0 = time.perf_counter()
    samples = normalize_quiet(samples)
    if initial is None and has_audio_energy(samples) and not has_speech(samples):
        # Sound but no voice (fan, breathing, keys): LLM decoders invent a
        # sentence for it, and so does the live preview, so neither is pasted.
        # Digital silence keeps the old path (a preview of lost audio wins).
        print("[Dictation] No speech detected (VAD); nothing to paste")
        return AsrResult("", len(samples) / 16000, int((time.perf_counter() - t0) * 1000),
                         getattr(engine, "current_model", None) or "unknown",
                         getattr(engine, "active_backend", ""))
    try:
        result = initial if initial is not None else engine.recognize(samples)
    except Exception as exc:
        from thundertalk.core.cloud import CloudError
        if isinstance(exc, CloudError):
            # A key, quota or network problem: retrying in chunks would only fail
            # (and maybe bill) again. The caller shows the message; the take is kept.
            print(f"[Dictation] Cloud decode failed: {exc.code}")
            raise
        print(f"[Dictation] Final decode failed: {type(exc).__name__}")
        result = AsrResult("", len(samples) / 16000, 0,
                           getattr(engine, "current_model", None) or "unknown",
                           getattr(engine, "active_backend", ""), truncated=True)
    if not isinstance(result, AsrResult):
        result = AsrResult(result.text, getattr(result, "duration_secs", len(samples) / 16000),
                           getattr(result, "inference_ms", 0), getattr(result, "model", "unknown"),
                           getattr(result, "backend", ""), getattr(result, "rtf", 0),
                           truncated=bool(getattr(result, "truncated", False)))
    speech = bool(preview.strip()) or has_speech(samples)
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
