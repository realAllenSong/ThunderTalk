"""IndexTTS-2.5 (bilibili) on MLX, through the vendored ``third_party/mlx_indextts``.

IndexTTS is a pure cloning model: every voice is a reference recording. The
built-in voices are the shared preset clips (``presets.py``); "My voices" are
the user's own recordings.

Mac notes (see third_party/README.md): the official PyTorch build is slow and
memory-hungry on MPS, so this uses the MLX port with the fp32 GroupNorm fix,
8-bit weights (1.7 GB) and the official 120-token text segmentation.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.mlx_runtime import serialized_mlx
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend
from thundertalk.core.tts_backends.presets import load_presets

REPO = "vanch007/mlx-indextts2-2.5-8bit"
W2V_REPO = "facebook/w2v-bert-2.0"          # semantic front-end, fetched by the port via transformers
SR = 22050
_LANG = {"chinese": "zh", "english": "en", "japanese": "ja", "spanish": "es", "arabic": "ar"}
_NEEDED = ("config.yaml", "gpt.safetensors", "s2mel.safetensors", "bigvgan.safetensors",
           "codec.safetensors", "model_manifest.json", "wav2vec2bert_stats.pt")
_W2V_NEEDED = ("config.json", "preprocessor_config.json", "model.safetensors")


def _third_party() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return base / "third_party"


class IndexTTSBackend(TtsBackend):
    info = BackendInfo(
        params="~0.8B", language_codes=("zh", "en", "ja", "es", "ar"), speed="realtime",
        backbone_params=True,
        id="indextts",
        name="IndexTTS-2.5",
        blurb_en=("bilibili IndexTTS-2.5 (8-bit MLX). Very faithful voice cloning in Chinese, English, "
                  "Japanese, Spanish and Arabic. About real time on Apple silicon. "
                  "Weights under the bilibili Model Use License."),
        blurb_zh=("bilibili IndexTTS-2.5（8-bit MLX）。克隆还原度很高，支持中、英、日、西、阿语。"
                  "在 Apple 芯片上约为实时速度。权重遵循 bilibili 模型使用许可。"),
        languages=("chinese", "english", "japanese", "spanish", "arabic"),
        supports_presets=True,
        supports_clone=True,
        needs_gpu=True,
        downloads=(Download(kind="hf", source=REPO, size_mb=1720),
                   Download(kind="hf", source=W2V_REPO, size_mb=2320)),
    )
    sample_rate = SR

    def __init__(self, repo: str = REPO) -> None:
        self.repo = repo
        self._model = None
        self._lock = threading.Lock()
        self._tmp = Path(tempfile.gettempdir()) / "thundertalk-indextts-refs"

    # ── contract ─────────────────────────────────────────────────────────
    def _snapshot(self) -> Optional[Path]:
        from thundertalk.core.models import hf_snapshot_dir
        return hf_snapshot_dir(self.repo)

    def is_ready(self) -> bool:
        from thundertalk.core.models import hf_snapshot_dir
        snap, w2v = self._snapshot(), hf_snapshot_dir(W2V_REPO)
        from thundertalk.core.runtime import needed
        return bool(not needed() and snap and all((snap / f).exists() for f in _NEEDED)
                    and w2v and all((w2v / f).exists() for f in _W2V_NEEDED))

    def voices(self) -> list[BackendVoice]:
        return [BackendVoice(id=f"indextts:{p.slug}", name=p.name, language=p.language, gender=p.gender,
                             blurb_en=p.blurb_en, blurb_zh=p.blurb_zh) for p in load_presets()]

    @serialized_mlx
    def load(self) -> None:
        if self._model is not None:
            return
        from thundertalk.core.tts import TtsModelMissing
        if not self.is_ready():
            raise TtsModelMissing(self.repo)
        from thundertalk.core.runtime import require
        require()
        tp = str(_third_party())
        if tp not in sys.path:
            sys.path.insert(0, tp)
        import os
        from thundertalk.core.models import hf_snapshot_dir
        # Point the port at the cached encoder so it never reaches the network.
        os.environ.setdefault("INDEXTTS_W2V_BERT_DIR", str(hf_snapshot_dir(W2V_REPO)))
        from mlx_indextts.generate_v25 import IndexTTSv25      # torch & co: CPU work, outside GPU_LOCK
        with self._lock, GPU_LOCK:
            if self._model is None:
                self._model = IndexTTSv25(model_dir=str(self._snapshot()), quantize_bits=8)

    @serialized_mlx
    def unload(self) -> None:
        with self._lock:
            self._model = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    @serialized_mlx
    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        ref = self._reference_path(voice)
        self.load()
        if context is not None:
            context["native_speed"] = True
        with GPU_LOCK:
            audio = self._model.generate(
                text, str(ref), language=_LANG.get(language, "auto"), seed=int(seed) & 0x7FFFFFFF,
                speed=float(speed), max_text_tokens_per_segment=120, interval_silence=200)
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    @serialized_mlx
    def warm_up(self, voice, language: str) -> None:
        """The port loads W2V-BERT and CAMPPlus on its first request and caches
        each reference's speaker features by path; do both now, each step in its
        own GPU_LOCK hold so a dictation can slip in between, then speak one word."""
        ref = self._reference_path(voice)
        self.load()
        m = self._model
        if hasattr(m, "_ensure_pytorch_modules"):
            with GPU_LOCK:
                m._ensure_pytorch_modules()
        if hasattr(m, "_process_reference_audio"):
            with GPU_LOCK:
                m._process_reference_audio(str(ref))
        super().warm_up(voice, language)

    # ── references ───────────────────────────────────────────────────────
    def _reference_path(self, voice) -> Path:
        if isinstance(voice, str):
            slug = voice.split(":", 1)[1] if voice.startswith("indextts:") else None
            clip = next((p for p in load_presets() if p.slug == slug), None)
            if clip is None:
                raise ValueError(f"unknown IndexTTS voice: {voice}")
            return clip.wav
        # ClonePrompt: 24 kHz float32 → a stable temp WAV (the port reads files and
        # caches speaker features per path, so the same prompt must map to one path).
        from thundertalk.core import audio_io
        a = np.asarray(voice.audio, dtype=np.float32).reshape(-1)
        key = hashlib.sha1(a.tobytes()).hexdigest()[:16]
        path = self._tmp / f"{key}.wav"
        if not path.is_file():
            self._tmp.mkdir(parents=True, exist_ok=True)
            audio_io.write_wav(str(path), a, 24000)
        return path
