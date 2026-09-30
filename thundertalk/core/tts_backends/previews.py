"""Preview clips: a few seconds of each built-in voice, spoken by its own
engine and shipped with the app (assets/voices/previews/<engine>/<voice>.flac),
so a voice can be heard before choosing it, without generating anything.
Rendered by ``tools/make_voice_previews.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np

PREVIEW_SR = 24000
PREVIEW_TEXT = {
    "chinese": "你好，很高兴认识你。这是我的声音，希望你会喜欢。",
    "english": "Hi there, nice to meet you. This is what my voice sounds like.",
}


def _root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))


def preview_path(voice_id: str, root: Optional[Path] = None) -> Path:
    engine, _, slug = voice_id.partition(":")
    return (root or _root()) / "assets" / "voices" / "previews" / engine / f"{slug}.flac"


def load_preview(voice_id: str) -> Optional[tuple[np.ndarray, int]]:
    """(mono float32 audio, sample rate), or None when there is no clip."""
    p = preview_path(voice_id)
    if not p.is_file():
        return None
    try:
        import soundfile as sf
        x, sr = sf.read(str(p), dtype="float32", always_2d=False)
    except Exception:
        return None
    return (x if x.ndim == 1 else x.mean(axis=1)), int(sr)
