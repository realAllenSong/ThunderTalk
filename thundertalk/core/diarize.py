"""Multi-speaker transcription via MOSS-Transcribe-Diarize (mlx-audio).

MOSS-Transcribe-Diarize 0.9B is an end-to-end model that produces a
speaker-attributed transcript in a single pass:每个片段带起止时间戳和
匿名说话人标签（S01、S02…）。输出原始格式形如：

    [0.07][S01]Hello everyone.[2.68][2.82][S02]大家好。[6.62]

Used by the Studio (file transcription with speakers) only — the realtime dictation pipeline keeps using
the active ASR engine (single speaker, no diarization overhead).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

MODEL_REPO = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
MODEL_ID = "moss-transcribe-diarize-mlx"  # catalog id / local dir name

_MODEL = None
_MODEL_LOCK = threading.Lock()

_SEGMENT_RE = re.compile(
    r"\[(\d+(?:\.\d+)?)\]\[(S\d+)\](.*?)\[(\d+(?:\.\d+)?)\]", re.DOTALL
)
# Bare markers left over when the model emits timestamps / speaker tags
# with nothing between them (silence, or a degenerate timestamp loop).
# A trailing "[12." / "[S0" is a marker cut off by the token budget.
_MARKER_RE = re.compile(r"\[(?:\d+(?:\.\d+)?|S\d+)\]|\[[\dS.]*$")


@dataclass
class DiarizedSegment:
    start: float
    end: float
    speaker: str  # "S01", "S02", …
    text: str


def resolve_model_path() -> str:
    """Prefer the copy downloaded via the Models page; fall back to the
    HF repo id (hits the HuggingFace cache, or downloads on first use)."""
    from thundertalk.core.models import get_models_dir

    local = get_models_dir() / MODEL_ID
    if local.is_dir() and any(f.suffix == ".safetensors" for f in local.iterdir()):
        return str(local)
    return MODEL_REPO


def load_model():
    """Load (and cache) the MOSS model. Thread-safe; blocking on first call."""
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from mlx_audio.stt.utils import load_model as _load

            from thundertalk.core import speech
            speech.release_gpu()          # an idle Studio voice model makes room
            path = resolve_model_path()
            print(f"[Diarize] Loading MOSS-Transcribe-Diarize from {path}…")
            t0 = time.monotonic()
            _MODEL = _load(path)
            print(f"[Diarize] Model loaded in {time.monotonic() - t0:.1f}s")
        return _MODEL


def unload_model() -> None:
    """Release the cached model before switching Studio engines."""
    global _MODEL
    with _MODEL_LOCK:
        _MODEL = None
    import gc
    gc.collect()
    import sys
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        mx.clear_cache()


def parse_transcript(raw: str) -> list[DiarizedSegment]:
    segs = [
        DiarizedSegment(start=float(m[0]), end=float(m[3]), speaker=m[1], text=m[2].strip())
        for m in _SEGMENT_RE.findall(raw)
        if m[2].strip()
    ]
    if segs:
        return segs
    # No text-bearing segment. Plain text without markers is kept as one
    # unattributed segment; markers with nothing between them (silence, or
    # a "[0.00][S01][0.06][S02]…" loop) are not speech and yield nothing.
    raw = _MARKER_RE.sub("", raw).strip()
    if raw:
        return [DiarizedSegment(start=0.0, end=0.0, speaker="", text=raw)]
    return []


def max_tokens_for(audio) -> int:
    """Generation budget for a recording. The library default (2048 tokens)
    silently cut transcripts off after about 5.5 minutes of English: text,
    timestamps and speaker tags run at ~6 tokens per second of speech. Budget
    16/s with headroom so the model's own end-of-transcript decides."""
    try:
        if isinstance(audio, str):
            from thundertalk.core import audio_io
            seconds = audio_io.audio_duration(audio)
        else:
            seconds = len(audio) / 16000
    except Exception:
        seconds = 5400.0
    return int(min(max(2048, seconds * 16 + 512), 120_000))


def dictation_max_tokens(seconds: float) -> int:
    """Tight budget for short dictation clips. Measured on 1,600 real
    dictations: at most ~21 tokens for a 1 s clip and ~14 tokens/s on
    longer ones (text plus timestamps and speaker tags). On silence the
    model can loop on timestamps until the budget runs out (13 s with the
    2048 default); this caps that at about a second or two."""
    return int(96 + 24 * max(seconds, 0.0))


def transcribe(audio, max_tokens: int | None = None,
               between_tokens: Optional[Callable[[], None]] = None) -> list[DiarizedSegment]:
    """Transcribe *audio* with speaker labels.

    *audio* is either a path to a 16 kHz mono WAV file or a 1-D float32
    numpy array of 16 kHz samples (mlx-audio accepts both).

    *between_tokens* runs after every generated token while generation is
    suspended — long file jobs use it to pause for a dictation (one call
    keeps speaker labels consistent across the whole file, which separate
    windows would not). Greedy decoding, so the text is the same either way.
    """
    model = load_model()
    budget = max_tokens or max_tokens_for(audio)
    with _MODEL_LOCK:
        if between_tokens is None or not hasattr(model, "stream_generate"):
            result = model.generate(audio, max_tokens=budget)
            raw = result.text if hasattr(result, "text") else str(result)
        else:
            tokens: list[int] = []
            for token, _ in model.stream_generate(audio, max_tokens=budget):
                tokens.append(int(token))
                # A MOSS dictation may run while this one is paused (its
                # generation has its own cache; GPU_LOCK keeps them apart).
                _MODEL_LOCK.release()
                try:
                    between_tokens()
                finally:
                    _MODEL_LOCK.acquire()
            raw = model._tokenizer.decode(tokens, skip_special_tokens=True).strip()
    return parse_transcript(raw)


def synchronize() -> None:
    """Finish the GPU work MOSS queued ahead (mlx-lm evaluates the next token
    asynchronously) before another thread uses the GPU."""
    try:
        import mlx.core as mx
        from mlx_lm.generate import generation_stream
        mx.synchronize(generation_stream)
    except Exception:
        pass


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF        # CJK Unified Ideographs
        or 0x3000 <= cp <= 0x303F     # CJK punctuation
        or 0xFF00 <= cp <= 0xFFEF     # fullwidth forms
    )


def plain_text(segs: list[DiarizedSegment]) -> str:
    """Join segment texts without speaker labels — for dictation output.

    Inserts a space at segment boundaries only when neither side is CJK,
    so Chinese sentences concatenate without stray spaces.
    """
    out = ""
    for s in segs:
        txt = s.text.strip()
        if not txt:
            continue
        if out and not (_is_cjk(out[-1]) or _is_cjk(txt[0])):
            out += " "
        out += txt
    return out


def merge_turns(segs: list[DiarizedSegment]) -> list[DiarizedSegment]:
    """Merge consecutive segments from the same speaker into turns."""
    turns: list[DiarizedSegment] = []
    for s in segs:
        if turns and turns[-1].speaker == s.speaker:
            turns[-1].text = f"{turns[-1].text} {s.text}"
            turns[-1].end = s.end
        else:
            turns.append(DiarizedSegment(s.start, s.end, s.speaker, s.text))
    return turns
