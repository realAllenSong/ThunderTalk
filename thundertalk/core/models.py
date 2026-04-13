"""Model registry and download management.

Each model family (Qwen3-ASR, SenseVoice, etc.) can have multiple
**variants** — different formats targeting different hardware:
  - MLX fp16    → Apple Silicon (Metal GPU)
  - ONNX int8   → All platforms (CPU)
  - ONNX+GGUF   → All platforms (CPU + llama.cpp)

The UI presents variants grouped by family, with a "Recommended" badge
on the best variant for the detected hardware.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ModelInfo:
    id: str
    family: str          # "Qwen3-ASR", "FunASR-Nano", "SenseVoice"
    name: str            # family display name (shared by variants in the same group)
    variant: str         # "MLX fp16", "ONNX int8", "GGUF Q4", etc.
    backend: str         # "mlx" | "onnx" | "onnx-cuda" — tells asr.py which loader to use
    size_mb: int
    language_count: int
    accuracy_stars: int
    download_url: str
    notes: str = ""
    hotword_support: bool = False
    platform: str = "all"  # "apple-silicon" | "nvidia" | "all"


# ---------------------------------------------------------------------------
# Built-in model registry
# ---------------------------------------------------------------------------

BUILTIN_MODELS: list[ModelInfo] = [
    # ── Qwen3-ASR 0.6B ─────────────────────────────────────────────────
    ModelInfo(
        id="qwen3-asr-06b-mlx",
        family="Qwen3-ASR",
        name="Qwen3-ASR-0.6B",
        variant="MLX fp16",
        backend="mlx",
        size_mb=1200,
        language_count=52,
        accuracy_stars=5,
        download_url="hf://Qwen/Qwen3-ASR-0.6B",
        hotword_support=True,
        platform="apple-silicon",
        notes="Metal GPU · RTF ~0.06 · Fastest on Apple Silicon",
    ),
    ModelInfo(
        id="qwen3-asr-06b-int8",
        family="Qwen3-ASR",
        name="Qwen3-ASR-0.6B",
        variant="ONNX int8",
        backend="onnx",
        size_mb=940,
        language_count=52,
        accuracy_stars=5,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2",
        hotword_support=True,
        platform="all",
        notes="CPU · RTF ~0.3 · Works on all platforms",
    ),
    # ── Qwen3-ASR 1.7B ─────────────────────────────────────────────────
    ModelInfo(
        id="qwen3-asr-17b-mlx",
        family="Qwen3-ASR-1.7B",
        name="Qwen3-ASR-1.7B",
        variant="MLX fp16",
        backend="mlx",
        size_mb=3400,
        language_count=52,
        accuracy_stars=5,
        download_url="hf://Qwen/Qwen3-ASR-1.7B",
        hotword_support=True,
        platform="apple-silicon",
        notes="Metal GPU · Higher accuracy · Needs 4 GB+ RAM",
    ),
    ModelInfo(
        id="qwen3-asr-17b-onnx",
        family="Qwen3-ASR-1.7B",
        name="Qwen3-ASR-1.7B",
        variant="ONNX",
        backend="onnx",
        size_mb=3000,
        language_count=52,
        accuracy_stars=5,
        download_url="",
        hotword_support=True,
        platform="all",
        notes="Coming Soon",
    ),
    # ── SenseVoice-Small ────────────────────────────────────────────────
    ModelInfo(
        id="sensevoice-small-int8",
        family="SenseVoice",
        name="SenseVoice-Small",
        variant="ONNX int8",
        backend="onnx",
        size_mb=241,
        language_count=5,
        accuracy_stars=3,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2",
        platform="all",
        notes="CPU · Lightweight · Fast on all platforms",
    ),
    # ── FunASR-Nano-MLT ─────────────────────────────────────────────────
    ModelInfo(
        id="funasr-nano-mlt-int8",
        family="FunASR-Nano",
        name="FunASR-Nano-MLT",
        variant="ONNX + GGUF",
        backend="onnx",
        size_mb=2100,
        language_count=31,
        accuracy_stars=4,
        download_url="",
        hotword_support=True,
        platform="all",
        notes="Unavailable — waiting for compatible sherpa-onnx model export",
    ),
]


# ---------------------------------------------------------------------------
# Grouping & recommendation
# ---------------------------------------------------------------------------

def get_families() -> OrderedDict[str, list[ModelInfo]]:
    """Group BUILTIN_MODELS by family, preserving insertion order."""
    groups: OrderedDict[str, list[ModelInfo]] = OrderedDict()
    for m in BUILTIN_MODELS:
        groups.setdefault(m.family, []).append(m)
    return groups


def _detect_platform() -> str:
    """Return current platform tag: 'apple-silicon', 'nvidia', or 'all'."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "apple-silicon"
    if platform.system() in ("Linux", "Windows"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                return "nvidia"
        except (FileNotFoundError, Exception):
            pass
    return "all"


_CURRENT_PLATFORM = _detect_platform()


def is_variant_compatible(info: ModelInfo) -> bool:
    """Can this variant run on the current hardware?"""
    if info.platform == "all":
        return True
    if info.platform == "apple-silicon" and _CURRENT_PLATFORM == "apple-silicon":
        return True
    if info.platform == "nvidia" and _CURRENT_PLATFORM == "nvidia":
        return True
    return False


_PLATFORM_BACKEND_PRIORITY = {
    "apple-silicon": ["mlx", "onnx", "onnx-cuda"],
    "nvidia": ["onnx-cuda", "onnx", "mlx"],
    "all": ["onnx", "onnx-cuda", "mlx"],
}


def get_recommended_id(family: str) -> Optional[str]:
    """Return the recommended model id for a family on current hardware."""
    families = get_families()
    variants = families.get(family)
    if not variants:
        return None

    priority = _PLATFORM_BACKEND_PRIORITY.get(_CURRENT_PLATFORM, ["onnx"])
    for be in priority:
        for v in variants:
            if v.backend == be and is_variant_compatible(v) and v.download_url:
                return v.id
    # Fallback: first compatible variant with a download URL
    for v in variants:
        if is_variant_compatible(v) and v.download_url:
            return v.id
    return None


# ---------------------------------------------------------------------------
# Download & path management
# ---------------------------------------------------------------------------

def get_models_dir() -> Path:
    base = Path.home() / ".thundertalk" / "models"
    base.mkdir(parents=True, exist_ok=True)
    return base


def is_downloaded(model_id: str) -> bool:
    info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
    if info and info.backend == "mlx":
        # MLX models are auto-downloaded by mlx-qwen3-asr from HuggingFace cache
        return True
    d = get_models_dir() / model_id
    if not d.is_dir():
        return False
    return any(
        f.suffix in (".onnx", ".gguf") for f in d.iterdir() if f.is_file()
    )


def get_model_path(model_id: str) -> Optional[str]:
    info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
    if info and info.backend == "mlx":
        return info.download_url  # "hf://Qwen/Qwen3-ASR-0.6B" — resolved by asr.py
    d = get_models_dir() / model_id
    if d.is_dir():
        return str(d)
    return None


def download_model(
    info: ModelInfo,
    progress_cb=None,
) -> None:
    """Download and extract a model. Calls *progress_cb(percent, msg)*."""
    if info.backend == "mlx":
        # MLX models are downloaded lazily by mlx_qwen3_asr.load_model()
        if progress_cb:
            progress_cb(100, "MLX model will download on first use")
        return

    models_dir = get_models_dir()
    target = models_dir / info.id

    if target.exists():
        return

    url = info.download_url
    if not url:
        raise ValueError("No download URL")

    is_tar = ".tar.bz2" in url or ".tar.gz" in url
    is_hf = "huggingface.co" in url and not is_tar

    if is_tar:
        ext = ".tar.bz2" if ".tar.bz2" in url else ".tar.gz"
        tmp = models_dir / f"{info.id}{ext}"

        if progress_cb:
            progress_cb(5, "Downloading...")

        subprocess.run(
            ["curl", "-L", "-f", "-o", str(tmp), url],
            check=True,
            capture_output=True,
        )

        if progress_cb:
            progress_cb(80, "Extracting...")

        dirs_before = {d.name for d in models_dir.iterdir() if d.is_dir()}

        flag = "xjf" if ".tar.bz2" in url else "xzf"
        subprocess.run(
            ["tar", flag, str(tmp), "-C", str(models_dir)],
            check=True,
            capture_output=True,
        )
        tmp.unlink(missing_ok=True)

        if not target.exists():
            for d in models_dir.iterdir():
                if d.is_dir() and d.name not in dirs_before and d.name != info.id:
                    d.rename(target)
                    break

    elif is_hf:
        if progress_cb:
            progress_cb(10, "Cloning from HuggingFace...")

        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            check=True,
            capture_output=True,
        )
    else:
        raise ValueError(f"Unsupported URL format: {url}")

    if progress_cb:
        progress_cb(100, "Done")


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

@dataclass
class HardwareInfo:
    cpu: str = "Unknown"
    memory_gb: float = 0
    gpu: str = "Unknown"
    platform_tag: str = "all"


def detect_hardware() -> HardwareInfo:
    info = HardwareInfo(platform_tag=_CURRENT_PLATFORM)
    system = platform.system()

    if system == "Darwin":
        try:
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True,
            )
            info.cpu = r.stdout.strip() or "Apple Silicon"
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True,
            )
            info.memory_gb = int(r.stdout.strip()) / 1024**3
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-detailLevel", "mini"],
                capture_output=True, text=True,
            )
            for line in r.stdout.splitlines():
                if "Chipset Model:" in line or "Chip:" in line:
                    info.gpu = line.split(":")[-1].strip()
                    break
        except Exception:
            pass

    elif system in ("Linux", "Windows"):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                info.gpu = r.stdout.strip().split("\n")[0]
        except (FileNotFoundError, Exception):
            pass

    return info
