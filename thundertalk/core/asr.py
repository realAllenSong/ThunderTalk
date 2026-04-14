"""ASR engine — multi-backend architecture.

Backends (determined per-model variant, not globally):
  - MLX:       mlx-qwen3-asr on Apple Silicon (Metal GPU). RTF ~0.06-0.08.
  - ONNX-CPU:  sherpa-onnx on any platform (CPU only). RTF ~0.3-0.5.
  - ONNX-CUDA: sherpa-onnx with CUDA on NVIDIA GPUs. RTF ~0.05.

load_model() receives the backend from the ModelInfo.backend field.
"""

from __future__ import annotations

import os
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import platform
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Platform detection (used for informational display, not backend selection)
# ---------------------------------------------------------------------------

_SYSTEM = platform.system()
_MACHINE = platform.machine()
_IS_APPLE_SILICON = _SYSTEM == "Darwin" and _MACHINE == "arm64"

_MLX_AVAILABLE: bool | None = None  # None = not yet checked

def _check_mlx() -> bool:
    """Lazy-check MLX availability (import only when first needed)."""
    global _MLX_AVAILABLE
    if _MLX_AVAILABLE is not None:
        return _MLX_AVAILABLE
    if not _IS_APPLE_SILICON:
        _MLX_AVAILABLE = False
        return False
    try:
        import mlx.core  # noqa: F401
        import mlx_qwen3_asr  # noqa: F401
        _MLX_AVAILABLE = True
    except ImportError as e:
        import sys, traceback
        print("MLX Load Error:", e, file=sys.stderr)
        traceback.print_exc()
        _MLX_AVAILABLE = False
    return _MLX_AVAILABLE

_HAS_NVIDIA = False
if _SYSTEM in ("Linux", "Windows"):
    try:
        import subprocess as _sp
        _r = _sp.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=3,
        )
        if _r.returncode == 0 and _r.stdout.strip():
            _HAS_NVIDIA = True
    except (FileNotFoundError, Exception):
        pass


# ---------------------------------------------------------------------------
# sherpa-onnx helpers
# ---------------------------------------------------------------------------

def _detect_threads() -> int:
    n = os.cpu_count() or 4
    if _IS_APPLE_SILICON:
        return max(4, int(n * 0.65))
    elif _SYSTEM == "Windows":
        return max(4, int(n * 0.75))
    return max(4, int(n * 0.80))


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AsrResult:
    text: str
    duration_secs: float
    inference_ms: int
    model: str
    backend: str = ""
    rtf: float = 0.0


# ---------------------------------------------------------------------------
# AsrEngine
# ---------------------------------------------------------------------------

class AsrEngine:
    def __init__(self) -> None:
        self._recognizer = None          # sherpa-onnx recognizer (ONNX backends)
        self._mlx_model = None           # pre-loaded mlx model object
        self._model_id: Optional[str] = None
        self._hotwords: str = ""
        self._model_dir: str = ""
        self._model_family: str = ""
        self._active_backend: str = ""   # actual backend of the currently loaded model
        self._language: Optional[str] = None  # forced language (None = auto-detect)
        self._itn_enabled: bool = True   # Inverse Text Normalization

        print(f"[ASR] Platform: {_SYSTEM}/{_MACHINE}  "
              f"mlx=lazy  "
              f"nvidia={'yes' if _HAS_NVIDIA else 'no'}")

    @property
    def is_loaded(self) -> bool:
        return self._recognizer is not None or self._mlx_model is not None

    @property
    def current_model(self) -> Optional[str]:
        return self._model_id

    @property
    def active_backend(self) -> str:
        return self._active_backend

    @property
    def needs_reload_for_hotwords(self) -> bool:
        """True if the loaded model is sherpa-onnx (hotwords baked at load time)."""
        return self._recognizer is not None

    def set_hotwords(self, words: list[str]) -> None:
        """Store hotwords. Does NOT reload the model — caller must handle reload."""
        self._hotwords = "/".join(w.strip() for w in words if w.strip())

    def set_language(self, language: str) -> None:
        """Set forced language for transcription. 'auto' = auto-detect."""
        if language in ("auto", ""):
            self._language = None
        else:
            # Map our settings codes to mlx_qwen3_asr language names
            _LANG_MAP = {
                "en": "English", "zh": "Chinese", "ja": "Japanese",
                "ko": "Korean", "es": "Spanish", "fr": "French",
                "de": "German", "ar": "Arabic", "hi": "Hindi",
                "it": "Italian", "pt": "Portuguese", "ru": "Russian",
                "nl": "Dutch", "tr": "Turkish",
            }
            self._language = _LANG_MAP.get(language, language)
        print(f"[ASR] Language set to: {self._language or 'auto-detect'}")

    def unload(self) -> None:
        self._recognizer = None
        self._mlx_model = None
        self._model_id = None

    # -- Loading ----------------------------------------------------------

    def load_model(self, model_dir: str, family: str, backend: str = "onnx") -> None:
        """Load model using the specified backend (from ModelInfo.backend)."""
        self.unload()
        self._model_dir = model_dir
        self._model_family = family
        self._active_backend = backend

        if backend == "mlx":
            if not _check_mlx():
                raise RuntimeError("MLX is not available on this system")
            self._load_mlx_qwen3(model_dir)
        elif family == "SenseVoice":
            self._load_sherpa_sensevoice(model_dir, backend)
        elif family in ("Qwen3-ASR", "Qwen3-ASR-1.7B"):
            self._load_sherpa_qwen3(model_dir, backend)
        else:
            raise ValueError(f"Unknown model family: {family}")

    def _onnx_provider(self, backend: str) -> str:
        if backend == "onnx-cuda" and _HAS_NVIDIA:
            return "cuda"
        return "cpu"

    def _load_mlx_qwen3(self, model_dir: str) -> None:
        import mlx_qwen3_asr
        import mlx.core as mx

        mx.metal.set_cache_limit(0)

        hf_repo = model_dir
        if hf_repo.startswith("hf://"):
            hf_repo = hf_repo[5:]

        print(f"[ASR-MLX] Calling load_model({hf_repo!r})...")
        model, _cfg = mlx_qwen3_asr.load_model(hf_repo, dtype=mx.float16)
        mx.metal.clear_cache()
        print("[ASR-MLX] load_model returned")
        self._mlx_model = model
        self._model_id = hf_repo.split("/")[-1] if "/" in hf_repo else os.path.basename(model_dir)
        print(f"[ASR] Loaded {hf_repo} via MLX (Metal GPU)  "
              f"hotwords={len(self._hotwords.split('/')) if self._hotwords else 0}")

    def _load_sherpa_sensevoice(self, model_dir: str, backend: str) -> None:
        import sherpa_onnx
        model_file = _find(model_dir, "model", ".onnx")
        tokens_file = os.path.join(model_dir, "tokens.txt")
        provider = self._onnx_provider(backend)
        threads = _detect_threads()

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_file,
            tokens=tokens_file,
            language="auto",
            use_itn=True,
            num_threads=threads,
            provider=provider,
        )
        self._model_id = os.path.basename(model_dir)
        print(f"[ASR] Loaded SenseVoice  threads={threads}  provider={provider}")

    def _load_sherpa_qwen3(self, model_dir: str, backend: str) -> None:
        import sherpa_onnx
        encoder = _find(model_dir, "encoder", ".onnx")
        decoder = _find(model_dir, "decoder", ".onnx")
        conv_frontend = _find(model_dir, "conv_frontend", ".onnx")
        tokenizer_dir = os.path.join(model_dir, "tokenizer")
        provider = self._onnx_provider(backend)
        threads = _detect_threads()

        self._recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
            encoder=encoder,
            decoder=decoder,
            conv_frontend=conv_frontend,
            tokenizer=tokenizer_dir if os.path.isdir(tokenizer_dir) else "",
            num_threads=threads,
            provider=provider,
            hotwords=self._hotwords,
            max_total_len=4096,
            max_new_tokens=2048,
        )
        self._model_id = os.path.basename(model_dir)
        print(f"[ASR] Loaded Qwen3-ASR (sherpa-onnx)  threads={threads}  provider={provider}")

    # -- Inference --------------------------------------------------------

    def recognize(self, samples: np.ndarray, sample_rate: int = 16000) -> AsrResult:
        if not self.is_loaded:
            raise RuntimeError("No model loaded")
        if len(samples) == 0:
            raise ValueError("Empty audio")

        if self._mlx_model is not None:
            return self._recognize_mlx(samples, sample_rate)

        from thundertalk.core.vad import segment_audio
        segments = segment_audio(samples, sr=sample_rate)
        if len(segments) == 1:
            return self._recognize_sherpa(segments[0], sample_rate)

        all_text: list[str] = []
        total_ms = 0
        total_dur = 0.0
        for seg in segments:
            r = self._recognize_sherpa(seg, sample_rate)
            if r.text:
                all_text.append(r.text)
            total_ms += r.inference_ms
            total_dur += r.duration_secs

        merged_text = " ".join(all_text)
        rtf = (total_ms / 1000) / total_dur if total_dur > 0 else 0
        return AsrResult(
            text=merged_text,
            duration_secs=total_dur,
            inference_ms=total_ms,
            model=self._model_id or "unknown",
            backend=self._active_backend,
            rtf=rtf,
        )

    def _recognize_mlx(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        import mlx.core as mx
        import mlx_qwen3_asr
        from thundertalk.core.itn import normalize_numbers

        duration_secs = len(samples) / sample_rate
        context = self._hotwords.replace("/", " ") if self._hotwords else ""

        lang_info = f", lang={self._language}" if self._language else ""
        print(f"[ASR-MLX] Starting transcribe ({len(samples)} samples, {duration_secs:.1f}s{lang_info})...")
        t0 = time.perf_counter()
        result = mlx_qwen3_asr.transcribe(
            (samples, sample_rate),
            model=self._mlx_model,
            context=context,
            language=self._language,
            max_new_tokens=4096,
        )
        mx.eval(result.text) if hasattr(result.text, '__mlx_array__') else None
        inference_ms = int((time.perf_counter() - t0) * 1000)
        print(f"[ASR-MLX] Transcribe done in {inference_ms}ms")

        mx.metal.clear_cache()
        rtf = (inference_ms / 1000) / duration_secs if duration_secs > 0 else 0

        text = result.text.strip()

        # Apply Inverse Text Normalization (一千 → 1000, etc.)
        if self._itn_enabled and text:
            raw = text
            text = normalize_numbers(text)
            if text != raw:
                print(f"[ASR-ITN] '{raw}' → '{text}'")

        return AsrResult(
            text=text,
            duration_secs=duration_secs,
            inference_ms=inference_ms,
            model=self._model_id or "unknown",
            backend="mlx",
            rtf=rtf,
        )

    def _recognize_sherpa(self, samples: np.ndarray, sample_rate: int) -> AsrResult:
        duration_secs = len(samples) / sample_rate
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)

        t0 = time.perf_counter()
        self._recognizer.decode_stream(stream)
        inference_ms = int((time.perf_counter() - t0) * 1000)

        text = stream.result.text.strip()
        rtf = (inference_ms / 1000) / duration_secs if duration_secs > 0 else 0

        if self._itn_enabled and text:
            from thundertalk.core.itn import normalize_numbers
            raw = text
            text = normalize_numbers(text)
            if text != raw:
                print(f"[ASR-ITN] '{raw}' → '{text}'")

        return AsrResult(
            text=text,
            duration_secs=duration_secs,
            inference_ms=inference_ms,
            model=self._model_id or "unknown",
            backend=self._active_backend,
            rtf=rtf,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find(directory: str, keyword: str, ext: str) -> str:
    kw_lower = keyword.lower()
    for fname in os.listdir(directory):
        if fname.lower().endswith(ext) and kw_lower in fname.lower():
            return os.path.join(directory, fname)
    exact = os.path.join(directory, f"{keyword}{ext}")
    if os.path.isfile(exact):
        return exact
    raise FileNotFoundError(f"No {ext} file matching '{keyword}' in {directory}")


