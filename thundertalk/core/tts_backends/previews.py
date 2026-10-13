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


_LIVE: dict[str, tuple[np.ndarray, int]] = {}


def _live_preview(voice_id: str) -> Optional[tuple[np.ndarray, int]]:
    """System voices differ per Mac, so their clip is spoken on first use."""
    if voice_id not in _LIVE:
        from thundertalk.core.tts_backends import apple
        voice = next((v for v in apple.system_voices() if v.id == voice_id), None)
        if voice is None:
            return None
        try:
            backend = apple.AppleBackend()
            x = backend.generate(PREVIEW_TEXT.get(voice.language, PREVIEW_TEXT["english"]), voice_id, voice.language)
        except Exception:
            return None
        _LIVE[voice_id] = (x, backend.sample_rate)
    return _LIVE[voice_id]


def has_preview(voice_id: str) -> bool:
    return voice_id.startswith("apple:") or preview_path(voice_id).is_file()


def load_preview(voice_id: str) -> Optional[tuple[np.ndarray, int]]:
    """(mono float32 audio, sample rate), or None when there is no clip."""
    if voice_id.startswith("apple:"):
        return _live_preview(voice_id)
    p = preview_path(voice_id)
    if not p.is_file():
        return None
    try:
        import soundfile as sf
        x, sr = sf.read(str(p), dtype="float32", always_2d=False)
    except Exception:
        return None
    return (x if x.ndim == 1 else x.mean(axis=1)), int(sr)
