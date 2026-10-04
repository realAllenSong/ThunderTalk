"""Kokoro v1.1 (multi-lang, via sherpa-onnx) — small, fast, CPU-only TTS.

82M parameters, ONNX on CPU (no GPU_LOCK needed), roughly 4x faster than real
time with four threads. 103 built-in speakers: sids 0–2 are English female,
3–57 Chinese female, 58–102 Chinese male (the Chinese voices read mixed
Chinese/English text). No cloning. Speed is applied natively by the model.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core.tts_backends.base import (
    BackendInfo,
    BackendVoice,
    Download,
    TtsBackend,
)

MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "tts-models/kokoro-multi-lang-v1_1.tar.bz2")
MODEL_FOLDER = "kokoro-multi-lang-v1_1"
SAMPLE_RATE = 24000

NUM_SPEAKERS = 103
_EN_FEMALE = {0: "Maple", 1: "Sol", 2: "Vale"}      # af_maple, af_sol, bf_vale
_ZH_FEMALE = range(3, 58)
_ZH_MALE = range(58, 103)
_PER_GROUP = 10


def _spread(r: range, n: int) -> list[int]:
    """``n`` evenly spaced members of ``r`` (first and last included)."""
    lo, hi = r[0], r[-1]
    return [round(lo + i * (hi - lo) / (n - 1)) for i in range(n)]


def voice_id(sid: int) -> str:
    return f"kokoro:{sid}"


def all_voice_ids() -> list[str]:
    """Every speaker the model has (103), for a "more voices" list."""
    return [voice_id(s) for s in range(NUM_SPEAKERS)]


def _curated_voices() -> list[BackendVoice]:
    out = [BackendVoice(id=voice_id(sid), name=name, language="english", gender="f",
                        blurb_en=f"English female ({name})",
                        blurb_zh=f"英文女声（{name}）")
           for sid, name in _EN_FEMALE.items()]
    for i, sid in enumerate(_spread(_ZH_FEMALE, _PER_GROUP), 1):
        out.append(BackendVoice(id=voice_id(sid), name=f"Female {i}", language="chinese",
                                gender="f", blurb_en=f"Chinese female {i}",
                                blurb_zh=f"中文女声 {i}"))
    for i, sid in enumerate(_spread(_ZH_MALE, _PER_GROUP), 1):
        out.append(BackendVoice(id=voice_id(sid), name=f"Male {i}", language="chinese",
                                gender="m", blurb_en=f"Chinese male {i}",
                                blurb_zh=f"中文男声 {i}"))
    return out


_VOICES = _curated_voices()


def _parse_sid(voice) -> int:
    if not isinstance(voice, str):
        # ClonePrompt or anything else that is not a preset id.
        raise ValueError("Kokoro has no voice cloning; pick one of its built-in voices")
    prefix, _, num = voice.partition(":")
    if prefix != "kokoro" or not num.isdigit() or not 0 <= int(num) < NUM_SPEAKERS:
        raise ValueError(f"not a Kokoro voice: {voice!r}")
    return int(num)


class KokoroBackend(TtsBackend):
    stochastic = False              # same text + voice → same audio; retrying cannot help
    info = BackendInfo(
        params="82M", language_codes=("zh", "en"), speed="kokoro",
        id="kokoro",
        name="Kokoro",
        blurb_en="Small and fast, runs on the CPU. Built-in Chinese and English voices; no cloning.",
        blurb_zh="小巧快速，只用 CPU。内置中英文音色，不支持克隆。",
        languages=("chinese", "english"),
        supports_presets=True,
        supports_clone=False,
        needs_gpu=False,
        downloads=(Download(kind="tar", source=MODEL_URL, dest=MODEL_FOLDER, size_mb=364),),
    )
    sample_rate = SAMPLE_RATE

    def __init__(self, model_dir: Optional[Path] = None, num_threads: int = 4) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else None
        self._num_threads = num_threads
        self._tts = None
        self._lock = threading.Lock()

    @property
    def model_dir(self) -> Path:
        if self._model_dir is not None:
            return self._model_dir
        from thundertalk.core.models import get_models_dir
        return get_models_dir() / MODEL_FOLDER

    def is_ready(self) -> bool:
        d = self.model_dir
        return (d / "model.onnx").is_file() and (d / "voices.bin").is_file()

    def voices(self) -> list[BackendVoice]:
        return list(_VOICES)

    def load(self) -> None:
        with self._lock:
            if self._tts is not None:
                return
            if not self.is_ready():
                from thundertalk.core.tts import TtsModelMissing
                raise TtsModelMissing(MODEL_URL)
            import sherpa_onnx as so

            k = str(self.model_dir)
            cfg = so.OfflineTtsConfig(
                model=so.OfflineTtsModelConfig(
                    kokoro=so.OfflineTtsKokoroModelConfig(
                        model=f"{k}/model.onnx",
                        voices=f"{k}/voices.bin",
                        tokens=f"{k}/tokens.txt",
                        data_dir=f"{k}/espeak-ng-data",
                        dict_dir=f"{k}/dict",
                        lexicon=f"{k}/lexicon-us-en.txt,{k}/lexicon-zh.txt",
                    ),
                    num_threads=self._num_threads,
                    provider="cpu",
                ),
                rule_fsts=f"{k}/date-zh.fst,{k}/phone-zh.fst,{k}/number-zh.fst",
                max_num_sentences=1,
            )
            self._tts = so.OfflineTts(cfg)

    def unload(self) -> None:
        with self._lock:
            self._tts = None

    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        sid = _parse_sid(voice)          # validate before any heavy work
        if context is not None:
            context["native_speed"] = True
        if not text.strip():
            return np.zeros(0, dtype=np.float32)
        self.load()
        with self._lock:
            tts = self._tts
            if tts is None:              # unloaded between load() and here
                raise RuntimeError("Kokoro was unloaded during generation")
            audio = tts.generate(text, sid=sid, speed=float(speed))
        samples = np.asarray(audio.samples, dtype=np.float32)
        if audio.sample_rate != SAMPLE_RATE:
            raise RuntimeError(f"Kokoro returned {audio.sample_rate} Hz, expected {SAMPLE_RATE}")
        return samples
