"""LongCat-AudioDiT-1B (Meituan, MIT) on MLX, through mlx-audio.

A cloning model like IndexTTS: every voice is a reference recording plus its
transcript. The built-in voices are the shared preset clips (``presets.py``);
"My voices" are the user's own recordings. Chinese and English.

It differs from the other engines in how it generates: the whole piece is
denoised at once (flow matching, 16 steps), so a piece takes about the same
time whatever its length, and the model decides the length of the audio from
the number of characters before it starts. Two consequences handled here:

* Reference and generated audio share one 30-second window. ``chunk_scale``
  tells the pipeline how long a piece may be for the voice in use, and
  ``generate`` splits a piece that still would not fit, so nothing is ever cut
  off at the window's end.
* There is no speed control: the pipeline time-stretches afterwards.

Measured on an M3 Max with the 8-bit weights (2026-11-21, eight clips in two
built-in voices): 4–7 s per piece, 6.3 GB peak memory, every clip read back
without an error, English names inside Chinese included.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.mlx_runtime import serialized_mlx
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend
from thundertalk.core.tts_backends.presets import load_presets

REPO = "mlx-community/LongCat-AudioDiT-1B-8bit"
TOKENIZER_REPO = "google/umt5-base"         # tokenizer only: the encoder weights are inside REPO
TOKENIZER_DIR = "umt5-base-tokenizer"
SR = 24000
_NEEDED = ("config.json", "model.safetensors")
_TOKENIZER_FILES = ("spiece.model", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                    "config.json")

WINDOW_S = 30.0          # the model's max_wav_duration: reference + generated audio
MARGIN_S = 1.5           # kept free at the end of the window
REF_PEAK = 10 ** (-6 / 20)   # the reference is level-matched to -6 dBFS peak
BASE_SEED = 1024         # the model card's seed; the user preferred it to another by ear
# The model's own length estimate (mlx_audio…longcat_audiodit._approx_duration).
_ZH_S, _EN_S = 0.21, 0.082
_SPLIT_AT = "。！？!?；;，,、 "


def approx_seconds(text: str) -> float:
    """How long the model will make ``text``, before the reference-pace factor."""
    text = re.sub(r"\s+", "", text)
    zh = sum(1 for c in text if "一" <= c <= "鿿")
    en = sum(1 for c in text if c.isalpha() and not ("一" <= c <= "鿿"))
    other = len(text) - zh - en
    if zh > en:
        zh += other
    else:
        en += other
    return zh * _ZH_S + en * _EN_S


def pace(ref_seconds: float, ref_text: str) -> float:
    """The model stretches its estimate by how slowly the reference is spoken (1–1.5×)."""
    return float(min(max(ref_seconds / max(approx_seconds(ref_text), 1e-6), 1.0), 1.5))


def budget_seconds(ref_seconds: float) -> float:
    """Generated audio that fits in the window next to a reference of this length."""
    return max(2.0, WINDOW_S - ref_seconds - MARGIN_S)


def split_to_fit(text: str, fits) -> list[str]:
    """``text`` in as few parts as possible such that ``fits(part)`` holds for each,
    cut at the punctuation mark (or space) nearest the middle."""
    text = text.strip()
    if not text or fits(text) or len(text) < 4:
        return [text] if text else []
    mid = len(text) // 2
    cuts = [i + 1 for i, c in enumerate(text[:-1]) if c in _SPLIT_AT and i > 0]
    cut = min(cuts, key=lambda i: abs(i - mid)) if cuts else mid
    return split_to_fit(text[:cut], fits) + split_to_fit(text[cut:], fits)


class LongCatBackend(TtsBackend):
    info = BackendInfo(
        params="1B", language_codes=("zh", "en"), speed="realtime",
        id="longcat",
        name="LongCat-AudioDiT",
        blurb_en=("Meituan LongCat-AudioDiT 1B (8-bit MLX). Voice cloning in Chinese and English, mixed text "
                  "included. Generates a whole piece at once, so long sentences take no longer than short "
                  "ones. MIT licensed."),
        blurb_zh=("美团 LongCat-AudioDiT 1B（8-bit MLX）。中文、英文及中英混说的声音克隆。"
                  "整段一次生成，长句和短句耗时差不多。MIT 许可。"),
        languages=("chinese", "english"),
        supports_presets=True,
        supports_clone=True,
        needs_gpu=True,
        downloads=(Download(kind="hf", source=REPO, size_mb=2050, required=_NEEDED),
                   Download(kind="hf-dir", source=TOKENIZER_REPO, dest=TOKENIZER_DIR, size_mb=20,
                            allow=_TOKENIZER_FILES)),
    )
    sample_rate = SR

    def __init__(self, repo: str = REPO) -> None:
        self.repo = repo
        self._model = None
        self._lock = threading.Lock()

    # ── contract ─────────────────────────────────────────────────────────
    def _tokenizer_dir(self) -> Path:
        from thundertalk.core.models import get_models_dir
        return get_models_dir() / TOKENIZER_DIR

    def is_ready(self) -> bool:
        from thundertalk.core import models, speech
        return models.hf_snapshot_has(self.repo, _NEEDED) and speech.download_ready(self.info.downloads[1])

    def voices(self) -> list[BackendVoice]:
        return [BackendVoice(id=f"longcat:{p.slug}", name=p.name, language=p.language, gender=p.gender,
                             blurb_en=p.blurb_en, blurb_zh=p.blurb_zh) for p in load_presets()]

    @serialized_mlx
    def load(self) -> None:
        if self._model is not None:
            return
        from thundertalk.core.tts import TtsModelMissing
        if not self.is_ready():
            raise TtsModelMissing(self.repo)
        from mlx_audio.tts.utils import load_model          # imports are CPU work: outside GPU_LOCK
        from thundertalk.core.models import hf_snapshot_dir
        with self._lock, GPU_LOCK:
            if self._model is None:
                model = load_model(str(hf_snapshot_dir(self.repo)))
                # The model asks transformers for its tokenizer by name on every
                # request, which is a network round trip; give it the local copy.
                model.config.text_encoder_model = str(self._tokenizer_dir())
                self._model = model

    @serialized_mlx
    def unload(self) -> None:
        with self._lock:
            self._model = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def chunk_scale(self, voice, language: str, default: float) -> float:
        """Pieces short enough to fit next to this voice's reference."""
        audio, ref_text = self._reference(voice)
        ref_s = len(audio) / SR
        per_char = (_ZH_S if language in ("chinese", "japanese", "korean") else _EN_S / 0.82) * pace(ref_s, ref_text)
        hard = 80 if language in ("chinese", "japanese", "korean") else 200     # plan_segments' base hard limit
        return float(min(default, max(0.3, budget_seconds(ref_s) / per_char / hard)))

    @serialized_mlx
    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        audio, ref_text = self._reference(voice)
        self.load()
        ref_s = len(audio) / SR
        limit = budget_seconds(ref_s) / pace(ref_s, ref_text)
        parts = split_to_fit(text, lambda s: approx_seconds(s) <= limit)
        import mlx.core as mx
        ref = mx.array(audio)
        out: list[np.ndarray] = []
        for k, part in enumerate(parts):
            with GPU_LOCK:
                res = list(self._model.generate(
                    text=part, ref_audio=ref, ref_text=ref_text, guidance_method="apg", cfg_strength=4.0,
                    steps=16, seed=(BASE_SEED + int(seed) + k) & 0x7FFFFFFF))
            out.append(np.concatenate([np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in res]))
            if k < len(parts) - 1:
                out.append(np.zeros(int(0.12 * SR), dtype=np.float32))
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    # ── references ───────────────────────────────────────────────────────
    def _reference(self, voice) -> tuple[np.ndarray, str]:
        """(24 kHz mono float32 at -6 dBFS peak, its exact transcript)."""
        if isinstance(voice, str):
            slug = voice.split(":", 1)[1] if voice.startswith("longcat:") else None
            clip = next((p for p in load_presets() if p.slug == slug), None)
            if clip is None:
                raise ValueError(f"unknown LongCat voice: {voice}")
            return _preset_audio(str(clip.wav)), clip.text
        return _level(np.asarray(voice.audio, dtype=np.float32).reshape(-1)), voice.text


def _level(a: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(a))) if len(a) else 0.0
    return (a * (REF_PEAK / peak)).astype(np.float32) if peak > 1e-6 else a.astype(np.float32)


_PRESET_AUDIO: dict[str, np.ndarray] = {}


def _preset_audio(path: str) -> np.ndarray:
    if path not in _PRESET_AUDIO:
        from thundertalk.core import audio_io
        a = np.asarray(audio_io.decode_audio(path, SR), dtype=np.float32).reshape(-1)
        _PRESET_AUDIO[path] = _level(a)
    return _PRESET_AUDIO[path]
