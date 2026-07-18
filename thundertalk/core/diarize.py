"""Multi-speaker transcription via MOSS-Transcribe-Diarize (mlx-audio).

MOSS-Transcribe-Diarize 0.9B is an end-to-end model that produces a
speaker-attributed transcript in a single pass:每个片段带起止时间戳和
匿名说话人标签（S01、S02…）。输出原始格式形如：

    [0.07][S01]Hello everyone.[2.68][2.82][S02]大家好。[6.62]

Used by the Lab page only — the realtime dictation pipeline keeps using
the active ASR engine (single speaker, no diarization overhead).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MODEL_REPO = "OpenMOSS-Team/MOSS-Transcribe-Diarize"
MODEL_ID = "moss-transcribe-diarize-mlx"  # catalog id / local dir name

_MODEL = None
_MODEL_LOCK = threading.Lock()

_SEGMENT_RE = re.compile(
    r"\[(\d+(?:\.\d+)?)\]\[(S\d+)\](.*?)\[(\d+(?:\.\d+)?)\]", re.DOTALL
)


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

            path = resolve_model_path()
            print(f"[Diarize] Loading MOSS-Transcribe-Diarize from {path}…")
            t0 = time.monotonic()
            _MODEL = _load(path)
            print(f"[Diarize] Model loaded in {time.monotonic() - t0:.1f}s")
        return _MODEL


def parse_transcript(raw: str) -> list[DiarizedSegment]:
    segs = [
        DiarizedSegment(start=float(m[0]), end=float(m[3]), speaker=m[1], text=m[2].strip())
        for m in _SEGMENT_RE.findall(raw)
        if m[2].strip()
    ]
    if segs:
        return segs
    # Model returned no [t][SXX] markers (e.g. silence-only or plain text):
    # surface the raw text as a single unattributed segment.
    raw = raw.strip()
    if raw:
        return [DiarizedSegment(start=0.0, end=0.0, speaker="", text=raw)]
    return []


def transcribe(audio) -> list[DiarizedSegment]:
    """Transcribe *audio* with speaker labels.

    *audio* is either a path to a 16 kHz mono WAV file or a 1-D float32
    numpy array of 16 kHz samples (mlx-audio accepts both).
    """
    model = load_model()
    with _MODEL_LOCK:
        result = model.generate(audio)
    raw = result.text if hasattr(result, "text") else str(result)
    return parse_transcript(raw)


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
