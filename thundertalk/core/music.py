"""Studio ▸ Compose: ACE-Step 1.5 model catalog, hardware tiers and job folders.

Pure data and rules, no torch: the generation itself runs in a spawned worker
(``core/music_worker.py``). Sizes are the repo file sizes; memory and timing figures are measured on the
36 GiB M3 Max (``docs/verification/2026-10-music-perf.md``)."""

from __future__ import annotations

import platform
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from thundertalk.core.model_metadata import ACESTEP_LANGUAGES
from thundertalk.core.tts_backends.base import BackendInfo, Download

REPO = "ACE-Step/Ace-Step1.5"
XL_REPO = "ACE-Step/acestep-v15-xl-sft"
CHECKPOINTS = "ace-step/checkpoints"        # under ~/.thundertalk/models; initialize_service's project_root is its parent
COMPONENTS = ("torch", "music")

MIN_MEMORY_GB = 16
XL_MEMORY_GB = 32
PLANNER_MEMORY_GB = 24
PLANNER_DISK_LEFT_GB = 20
MIN_SECONDS = 10
MAX_SECONDS = 600
SMALL_MAC_MAX_SECONDS = 240
XL_DEFAULT_MAX_SECONDS = 300
MAX_TAKES = 5
STYLE_MAX_CHARS = 512
LYRICS_MAX_CHARS = 4096


def _sub(folder: str, *globs: str) -> tuple[str, ...]:
    return tuple(f"{folder}/{g}" for g in globs)


VAE = Download("hf-dir", REPO, dest=CHECKPOINTS, size_mb=338, allow=_sub("vae", "*.json", "*.safetensors"))
TEXT_ENCODER = Download("hf-dir", REPO, dest=CHECKPOINTS, size_mb=1208,
                        allow=_sub("Qwen3-Embedding-0.6B", "*.json", "*.safetensors", "*.txt", "*.jinja"))
PLANNER = Download("hf-dir", REPO, dest=CHECKPOINTS, size_mb=3756,
                   allow=_sub("acestep-5Hz-lm-1.7B", "*.json", "*.safetensors", "*.txt", "*.jinja"))
PLANNER_RATE = 0.17  # measured upper planning/conditioning seconds per audio second


@dataclass(frozen=True)
class MusicModel:
    info: BackendInfo
    folder: str                 # config_path for initialize_service
    steps: int
    ram_mb: int                 # measured MiB peak, including planner/repaint; conservative when off
    min_memory_gb: int
    rate: float = 0.3           # measured diffusion/decode/write seconds per audio second
    convert_bf16: bool = False  # F32 checkpoint converted once after download (decision 3)


def _model(id_, name, params, blurb_en, blurb_zh, dit: Download, folder, steps, ram_mb, min_memory_gb,
           rate, convert_bf16=False, speed="unmeasured") -> MusicModel:
    info = BackendInfo(id=id_, name=name, blurb_en=blurb_en, blurb_zh=blurb_zh, languages=(),
                       supports_presets=False, supports_clone=False, needs_gpu=True, params=params,
                       language_codes=ACESTEP_LANGUAGES, downloads=(dit, VAE, TEXT_ENCODER),
                       components=COMPONENTS, speed=speed)
    return MusicModel(info, folder, steps, ram_mb, min_memory_gb, rate, convert_bf16)


XL = _model(
    "acestep-xl", "ACE-Step 1.5 XL", "4B",
    "Best quality. Songs with vocals or instrumentals, 50 languages. Needs a Mac with 32 GB or more.",
    "音质最好。可生成带人声的歌曲或纯音乐，支持 50 种语言。需要 32 GB 及以上内存的 Mac。",
    Download("hf-dir", XL_REPO, dest=f"{CHECKPOINTS}/acestep-v15-xl-sft", size_mb=19950,
             allow=("*.json", "*.safetensors", "*.pt"), convert_bf16=True),
    "acestep-v15-xl-sft", steps=50, ram_mb=18500, min_memory_gb=XL_MEMORY_GB, rate=1.95, convert_bf16=True, speed="music_xl")

TURBO = _model(
    "acestep-turbo", "ACE-Step 1.5 Turbo", "2B",
    "Light and fast (8 steps). Songs with vocals or instrumentals, 50 languages.",
    "轻量、快速（8 步）。可生成带人声的歌曲或纯音乐，支持 50 种语言。",
    Download("hf-dir", REPO, dest=CHECKPOINTS, size_mb=4792,
             allow=_sub("acestep-v15-turbo", "*.json", "*.safetensors", "*.pt")),
    "acestep-v15-turbo", steps=8, ram_mb=14100, min_memory_gb=MIN_MEMORY_GB, rate=0.21, speed="music_turbo")

MUSIC_MODELS: tuple[MusicModel, ...] = (XL, TURBO)


def model(model_id: str) -> MusicModel:
    for m in MUSIC_MODELS:
        if m.info.id == model_id:
            return m
    raise KeyError(model_id)


def with_planner(m: MusicModel) -> BackendInfo:
    """``m``'s BackendInfo with the planner LM appended, for one combined download."""
    return replace(m.info, downloads=m.info.downloads + (PLANNER,))


@dataclass(frozen=True)
class Tier:
    """What Compose offers on a Mac with this much memory (decisions 5–7)."""
    models: tuple[str, ...]             # offered model ids, best first
    disabled: tuple[str, ...]           # shown greyed out with "needs N GB"
    recommended: str
    planner: bool                       # planner checkbox offered
    takes: int                          # default takes
    max_seconds: int                    # hard upper bound for the length field
    experimental: bool = False
    dit_backend: str = "mlx"            # "mlx" | "mps" (lower peak, Phase 0)

    def default_max_seconds(self, model_id: str) -> int:
        return min(self.max_seconds, XL_DEFAULT_MAX_SECONDS) if model_id == XL.info.id else self.max_seconds


def tier(memory_gb: float) -> Optional[Tier]:
    """None below 16 GB: Compose is hidden there (decision 7)."""
    # sysctl reports GiB-exact sizes; allow a little slack for rounding.
    gb = memory_gb + 0.5
    if gb < MIN_MEMORY_GB:
        return None
    if gb < PLANNER_MEMORY_GB:
        return Tier(models=(TURBO.info.id,), disabled=(XL.info.id,), recommended=TURBO.info.id, planner=False,
                    takes=1, max_seconds=SMALL_MAC_MAX_SECONDS, experimental=True, dit_backend="mps")
    if gb < XL_MEMORY_GB:
        return Tier(models=(TURBO.info.id,), disabled=(XL.info.id,), recommended=TURBO.info.id, planner=True,
                    takes=2, max_seconds=MAX_SECONDS)
    return Tier(models=(XL.info.id, TURBO.info.id), disabled=(), recommended=XL.info.id, planner=True,
                takes=2, max_seconds=MAX_SECONDS)


def supported(memory_gb: float, apple_silicon: Optional[bool] = None) -> bool:
    """Show Compose at all: Apple Silicon (MLX / MPS only) with a tier for this memory."""
    if apple_silicon is None:
        apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    return apple_silicon and tier(memory_gb) is not None


def planner_pre_ticked(memory_gb: float, disk_free_mb: int, pending_mb: int) -> bool:
    """Download the planner with the DiT only on ≥ 32 GB Macs that keep ≥ 20 GB
    free after the whole download (``pending_mb`` includes the planner)."""
    return memory_gb + 0.5 >= XL_MEMORY_GB and disk_free_mb - pending_mb >= PLANNER_DISK_LEFT_GB * 1000


def ram_need_mb(m: MusicModel, planner: bool) -> int:
    """Measured full peak, including planner; do not add its memory twice.

    Use the same conservative bound without planning until separately measured.
    """
    return m.ram_mb


def estimate_take_s(m: MusicModel, seconds: int, planner: bool) -> float:
    """M3 Max reference after loading, from measured 60/170 s upper rates.

    Other hardware and lengths are predictions, not additional measurements.
    """
    return seconds * (m.rate + (PLANNER_RATE if planner else 0.0))


def project_root() -> Path:
    """``initialize_service``'s project root: the folder holding ``checkpoints/``."""
    from thundertalk.core import models
    return models.get_models_dir() / Path(CHECKPOINTS).parent


def missing(m: MusicModel, planner: bool) -> list[Download]:
    """The downloads ``m`` (and the planner, if wanted) still needs."""
    from thundertalk.core.speech import download_ready
    return [d for d in m.info.downloads + ((PLANNER,) if planner else ()) if not download_ready(d)]


def clamp_seconds(seconds: float, t: Tier) -> int:
    return int(max(MIN_SECONDS, min(t.max_seconds, round(seconds))))


# ── takes on disk (decision 8) ───────────────────────────────────────────

def takes_root() -> Path:
    return Path.home() / "Music" / "ThunderTalk"


def slug(text: str, limit: int = 32) -> str:
    """A folder-safe name from the style or first lyric line; keeps CJK."""
    s = re.sub(r"[^\w\u3400-\u9fff]+", "-", text.strip().lower()).strip("-_")
    return s[:limit].rstrip("-_") or "song"


def new_job_dir(title: str, now: Optional[float] = None) -> Path:
    """``~/Music/ThunderTalk/<YYYY-MM-DD-HHMM>-<slug>``, never reusing a folder."""
    stamp = time.strftime("%Y-%m-%d-%H%M", time.localtime(now))
    base = takes_root() / f"{stamp}-{slug(title)}"
    path, n = base, 2
    while path.exists():
        path, n = base.with_name(f"{base.name}-{n}"), n + 1
    path.mkdir(parents=True)
    return path
