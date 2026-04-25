"""Speech translation engine — SeamlessM4T v2 via transformers + torch.

Separate from AsrEngine because:
  1. Different runtime (torch, not sherpa-onnx or mlx-qwen3-asr)
  2. Different I/O (takes target language code, not just audio)
  3. Different model format (HF Hub repo, not ONNX dir)

Loading is lazy and heavy (~8.6GB on disk, ~4.3GB GPU memory at fp16).
load_model() blocks the calling thread; callers should run it on a
background QThread.
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

_IS_APPLE_SILICON = platform.system() == "Darwin" and platform.machine() == "arm64"

# RMS below this is considered silent and short-circuits inference.
# Matches the threshold used in AsrEngine.recognize().
_SILENCE_RMS_THRESHOLD = 0.003


def detect_src_lang(text: str) -> str:
    """Heuristic: guess SeamlessM4T src_lang ISO-639-3 code from text contents.

    Used for T2TT when the upstream ASR doesn't expose a language code.
    Detection is character-set based — fast and deterministic.
    Returns "eng" as fallback (empty / Latin-only / unknown).
    """
    if not text:
        return "eng"

    hiragana = sum(1 for c in text if "぀" <= c <= "ゟ")
    katakana = sum(1 for c in text if "゠" <= c <= "ヿ")
    hangul = sum(1 for c in text if "가" <= c <= "힯")
    cjk_ideo = sum(
        1
        for c in text
        if "一" <= c <= "鿿" or "㐀" <= c <= "䶿"
    )

    # Hiragana/katakana presence is a strong signal for Japanese, even mixed
    # with kanji. Threshold low (1 char) because Korean/Chinese never use kana.
    if hiragana + katakana >= 1:
        return "jpn"
    if hangul >= 1:
        return "kor"
    # Pure CJK without kana → Mandarin (most common in our user base).
    if cjk_ideo >= 1:
        return "cmn"
    return "eng"


@dataclass
class TranslationResult:
    """Mirrors AsrResult fields used downstream so the Pipeline's
    `_on_asr_done` handler can duck-type on either result type."""

    text: str
    duration_secs: float
    inference_ms: int
    model: str
    tgt_lang: str


class TranslationEngine:
    """Wraps SeamlessM4T v2 for speech-to-text translation (S2TT).

    Usage:
      engine = TranslationEngine()
      engine.load_model("hf://facebook/seamless-m4t-v2-large")
      result = engine.translate(samples_float32, tgt_lang="spa")
    """

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._model_id: Optional[str] = None
        self._device: str = "cpu"
        self._dtype = None  # torch.float16 on GPU, torch.float32 on CPU

    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    @property
    def current_model(self) -> Optional[str]:
        return self._model_id

    def unload(self) -> None:
        self._model = None
        self._processor = None
        self._model_id = None

    def load_model(self, model_dir_or_repo: str) -> None:
        """Load SeamlessM4T v2 from a local dir OR an `hf://` repo ID.

        Imports torch/transformers lazily so the base app doesn't pay the
        startup cost when translation is off.
        """
        import torch
        from transformers import AutoProcessor, SeamlessM4Tv2Model

        self.unload()

        if model_dir_or_repo.startswith("hf://"):
            pretrained = model_dir_or_repo[len("hf://"):]
        else:
            pretrained = model_dir_or_repo

        # Device / dtype selection
        if _IS_APPLE_SILICON and torch.backends.mps.is_available():
            self._device = "mps"
            self._dtype = torch.float16
        elif torch.cuda.is_available():
            self._device = "cuda"
            self._dtype = torch.float16
        else:
            self._device = "cpu"
            self._dtype = torch.float32

        print(
            f"[Translate] Loading {pretrained} on {self._device} "
            f"({self._dtype})..."
        )
        t0 = time.perf_counter()

        self._processor = AutoProcessor.from_pretrained(pretrained)
        self._model = SeamlessM4Tv2Model.from_pretrained(
            pretrained,
            torch_dtype=self._dtype,
        ).to(self._device)
        self._model.eval()

        self._model_id = (
            os.path.basename(pretrained.rstrip("/")) or pretrained
        )
        elapsed = time.perf_counter() - t0
        print(f"[Translate] Loaded in {elapsed:.1f}s")

    def translate(
        self,
        samples: np.ndarray,
        tgt_lang: str,
        sample_rate: int = 16_000,
    ) -> TranslationResult:
        """Speech-to-text translation.

        Args:
          samples: mono float32 audio in [-1, 1]
          tgt_lang: ISO-639-3 code (e.g. "eng", "spa", "jpn", "cmn")
          sample_rate: input sample rate (passed through to the processor;
            the model expects 16kHz)
        """
        if not self.is_loaded:
            raise RuntimeError("No translation model loaded")
        if len(samples) == 0:
            raise RuntimeError("Empty audio")

        duration = len(samples) / sample_rate
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < _SILENCE_RMS_THRESHOLD:
            print(f"[Translate] Audio too quiet (rms={rms:.5f}), skipping")
            return TranslationResult(
                text="",
                duration_secs=duration,
                inference_ms=0,
                model=self._model_id or "unknown",
                tgt_lang=tgt_lang,
            )

        import torch

        t0 = time.perf_counter()
        # NOTE: keyword is `audio=` (singular). transformers 5.x raises a
        # ValueError on the legacy `audios=` (plural) form.
        inputs = self._processor(
            audio=samples,
            sampling_rate=sample_rate,
            return_tensors="pt",
        )
        # Move tensors onto the model device. Don't cast input ints to
        # float; only float tensors should adopt _dtype.
        moved = {}
        for k, v in inputs.items():
            if hasattr(v, "to"):
                if v.dtype == torch.float32 or v.dtype == torch.float16:
                    moved[k] = v.to(self._device, self._dtype)
                else:
                    moved[k] = v.to(self._device)
            else:
                moved[k] = v
        inputs = moved

        with torch.no_grad():
            output_tokens = self._model.generate(
                **inputs,
                tgt_lang=tgt_lang,
                generate_speech=False,
            )

        # Spike-confirmed: output_tokens is a tensor of shape [1, 1, seq_len].
        # output_tokens[0].tolist()[0] yields the list of token ids.
        token_ids = output_tokens[0].tolist()[0]
        text = self._processor.decode(token_ids, skip_special_tokens=True)

        inference_ms = int((time.perf_counter() - t0) * 1000)
        rtf = (inference_ms / 1000) / duration if duration > 0 else 0.0
        print(
            f"[Translate] {duration:.1f}s → {tgt_lang}  "
            f"{inference_ms}ms  rtf={rtf:.2f}"
        )

        return TranslationResult(
            text=text,
            duration_secs=duration,
            inference_ms=inference_ms,
            model=self._model_id or "unknown",
            tgt_lang=tgt_lang,
        )

    def translate_text(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
    ) -> TranslationResult:
        """Text-to-text translation (T2TT) using the same loaded model.

        Args:
          text: input text in `src_lang`
          src_lang: ISO-639-3 source language code (e.g. "cmn", "eng")
          tgt_lang: ISO-639-3 target language code

        The loaded SeamlessM4T v2 model handles both S2TT and T2TT — only
        the processor input shape differs (text= + src_lang= for T2TT vs
        audio= for S2TT). Returns the same TranslationResult shape, with
        duration_secs set to 0.0 (no audio) and the result.tgt_lang set
        to the target.
        """
        if not self.is_loaded:
            raise RuntimeError("No translation model loaded")
        stripped = text.strip()
        if not stripped:
            raise RuntimeError("Empty text")

        import torch

        t0 = time.perf_counter()
        inputs = self._processor(
            text=stripped,
            src_lang=src_lang,
            return_tensors="pt",
        )
        # Same float-only dtype move pattern as translate()
        moved = {}
        for k, v in inputs.items():
            if hasattr(v, "to"):
                if v.dtype == torch.float32 or v.dtype == torch.float16:
                    moved[k] = v.to(self._device, self._dtype)
                else:
                    moved[k] = v.to(self._device)
            else:
                moved[k] = v
        inputs = moved

        with torch.no_grad():
            output_tokens = self._model.generate(
                **inputs,
                tgt_lang=tgt_lang,
                generate_speech=False,
            )

        token_ids = output_tokens[0].tolist()[0]
        out_text = self._processor.decode(token_ids, skip_special_tokens=True)

        inference_ms = int((time.perf_counter() - t0) * 1000)
        print(
            f"[Translate] T2TT {src_lang}→{tgt_lang}: "
            f"{len(stripped)} chars → {len(out_text)} chars  "
            f"{inference_ms}ms"
        )

        return TranslationResult(
            text=out_text,
            duration_secs=0.0,  # not applicable for text input
            inference_ms=inference_ms,
            model=self._model_id or "unknown",
            tgt_lang=tgt_lang,
        )
