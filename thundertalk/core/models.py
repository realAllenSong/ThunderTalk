"""Model registry and download management.

Each model family can have multiple **variants** — different formats
targeting different hardware:
  - MLX fp16    → Apple Silicon (Metal GPU)
  - ONNX int8   → All platforms (CPU)

The UI presents variants grouped by family, with a "Recommended" badge
on the best variant for the detected hardware.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ModelInfo:
    id: str
    family: str          # "Qwen3-ASR", "Qwen3-ASR-1.7B", "SenseVoice"
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
    # For hf:// snapshots stored under models/<id>: only fetch files matching
    # these globs (keeps us from pulling duplicate checkpoint formats).
    hf_allow: tuple = ()


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
        size_mb=1881,
        language_count=52,
        accuracy_stars=5,
        download_url="hf://Qwen/Qwen3-ASR-0.6B",
        hotword_support=True,
        platform="apple-silicon",
        notes="Metal GPU · ~11x real time on M3 Max",
    ),
    ModelInfo(
        id="qwen3-asr-06b-int8",
        family="Qwen3-ASR",
        name="Qwen3-ASR-0.6B",
        variant="ONNX int8",
        backend="onnx",
        size_mb=879,
        language_count=52,
        accuracy_stars=5,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25.tar.bz2",
        hotword_support=True,
        platform="all",
        notes="CPU · ~12x real time on M3 Max · Works on all platforms",
    ),
    # ── Qwen3-ASR 1.7B ─────────────────────────────────────────────────
    ModelInfo(
        id="qwen3-asr-17b-mlx",
        family="Qwen3-ASR-1.7B",
        name="Qwen3-ASR-1.7B",
        variant="MLX fp16",
        backend="mlx",
        size_mb=4703,
        language_count=52,
        accuracy_stars=5,
        download_url="hf://Qwen/Qwen3-ASR-1.7B",
        hotword_support=True,
        platform="apple-silicon",
        notes="Metal GPU · Higher accuracy · Needs 4 GB+ RAM",
    ),
    # ── NVIDIA Parakeet-TDT 0.6B v3 (multilingual) ─────────────────────
    ModelInfo(
        id="parakeet-tdt-06b-v3-int8",
        family="Parakeet-TDT-v3",
        name="Parakeet-TDT 0.6B v3",
        variant="ONNX int8",
        backend="onnx",
        size_mb=487,
        language_count=25,
        accuracy_stars=5,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
        hotword_support=False,
        platform="all",
        notes="CPU · 25 European languages · Punctuation + casing built in",
    ),
    # ── NVIDIA Parakeet-TDT 0.6B v2 (English) ──────────────────────────
    ModelInfo(
        id="parakeet-tdt-06b-v2-int8",
        family="Parakeet-TDT-v2",
        name="Parakeet-TDT 0.6B v2",
        variant="ONNX int8",
        backend="onnx",
        size_mb=482,
        language_count=1,
        accuracy_stars=5,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2",
        hotword_support=False,
        platform="all",
        notes="CPU · English only · ~50x real time on M3 Max · Punctuation + casing",
    ),
    # ── SenseVoice-Small ────────────────────────────────────────────────
    ModelInfo(
        id="sensevoice-small-int8",
        family="SenseVoice",
        name="SenseVoice-Small",
        variant="ONNX int8",
        backend="onnx",
        size_mb=163,
        language_count=5,
        accuracy_stars=3,
        download_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        platform="all",
        notes="CPU · Lightweight · Fast on all platforms",
    ),
    # ── MOSS-Transcribe-Diarize (Studio: multi-speaker transcription) ─────
    ModelInfo(
        id="moss-transcribe-diarize-mlx",
        family="MOSS-Transcribe-Diarize",
        name="MOSS-Transcribe-Diarize 0.9B",
        variant="MLX bf16",
        backend="mlx-moss",
        size_mb=1833,
        language_count=50,
        accuracy_stars=5,
        download_url="hf://OpenMOSS-Team/MOSS-Transcribe-Diarize",
        hotword_support=False,
        platform="apple-silicon",
        notes="Multi-speaker ASR · Diarization + timestamps in Studio · Metal GPU",
    ),
    # ── SeamlessM4T v2 (translation) ────────────────────────────────────
    ModelInfo(
        id="seamless-m4t-v2-large",
        family="SeamlessM4T-v2",
        name="SeamlessM4T v2 Large",
        variant="PyTorch fp16",
        backend="seamless-torch",
        size_mb=9258,
        language_count=96,
        accuracy_stars=5,
        download_url="hf://facebook/seamless-m4t-v2-large",
        hotword_support=False,
        platform="all",
        notes="Direct speech→translated-text. Required for Translation feature.",
        # The repo also holds ~20 GB of fairseq .pt checkpoints that the
        # transformers loader never reads; without this filter the download
        # was 29.9 GB instead of 9.3 GB.
        hf_allow=("*.json", "*.safetensors", "*.txt", "*.model"),
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
        # MLX weights live in the shared HuggingFace cache (that is where
        # mlx-qwen3-asr resolves them). This used to return True
        # unconditionally, so a fresh install showed "Activate" and the
        # multi-GB download happened invisibly inside load_model().
        return _hf_cache_has(info.download_url[len("hf://"):])
    if info and info.backend == "mlx-moss":
        d = get_models_dir() / model_id
        if d.is_dir() and any(f.suffix == ".safetensors" for f in d.iterdir()):
            return True
        # Already fetched into the HuggingFace cache (e.g. by a Studio run)
        repo = info.download_url[len("hf://"):].replace("/", "--")
        cache = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo}"
        return cache.is_dir() and any(cache.rglob("*.safetensors"))
    d = get_models_dir() / model_id
    if not d.is_dir():
        return False
    # SeamlessM4T (and other HF snapshot models) use .safetensors weights.
    # ASR models (sherpa-onnx) use .onnx; some llama-cpp-style models use .gguf.
    return any(
        f.suffix in (".onnx", ".gguf", ".safetensors")
        for f in d.iterdir() if f.is_file()
    )


def get_model_path(model_id: str) -> Optional[str]:
    info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
    if info and info.backend == "mlx":
        return info.download_url  # "hf://Qwen/Qwen3-ASR-0.6B" — resolved by asr.py
    if info and info.backend == "mlx-moss":
        d = get_models_dir() / model_id
        if d.is_dir():
            return str(d)
        return info.download_url  # HF cache copy — resolved by diarize.load_model
    d = get_models_dir() / model_id
    if d.is_dir():
        return str(d)
    return None


class DownloadCancelled(Exception):
    """Raised inside a download when the user pressed Cancel."""


def _hf_cache_has(repo_id: str) -> bool:
    """True when a complete snapshot (config + weights) is in the HF cache."""
    try:
        from huggingface_hub import constants
        root = (Path(constants.HF_HUB_CACHE)
                / ("models--" + repo_id.replace("/", "--")) / "snapshots")
    except Exception:
        root = (Path.home() / ".cache" / "huggingface" / "hub"
                / ("models--" + repo_id.replace("/", "--")) / "snapshots")
    if not root.is_dir():
        return False
    for snap in root.iterdir():
        # Snapshot entries are symlinks into blobs/. glob() happily returns
        # dangling links, but .exists() follows them — so a half-written
        # weight file is (correctly) treated as "not downloaded".
        if (snap / "config.json").exists() and any(
            f.exists() for f in snap.glob("*.safetensors")
        ):
            return True
    return False


def _content_length(url: str) -> int:
    """Final Content-Length after redirects (0 when the server won't say)."""
    try:
        r = subprocess.run(
            ["curl", "-sIL", "--max-time", "15", url],
            capture_output=True, text=True,
        )
        n = 0
        for line in r.stdout.splitlines():
            if line.lower().startswith("content-length:"):
                try:
                    n = int(line.split(":", 1)[1].strip()) or n
                except ValueError:
                    pass
        return n
    except Exception:
        return 0


def _curl_download(url: str, dest: Path, progress_cb, cancel, lo: int, hi: int) -> None:
    """curl into ``dest`` while reporting real byte progress.

    ``curl`` (not urllib) on purpose: it uses the macOS trust store, so it
    works in the frozen app where Python has no CA bundle."""
    total = _content_length(url)
    proc = subprocess.Popen(
        ["curl", "-L", "-f", "-sS", "-o", str(dest), url],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                proc.wait()
                raise DownloadCancelled()
            size = dest.stat().st_size if dest.exists() else 0
            if progress_cb:
                if total:
                    frac = min(1.0, size / total)
                    progress_cb(int(lo + (hi - lo) * frac),
                                f"{size / 1e6:.0f} / {total / 1e6:.0f} MB")
                else:
                    progress_cb(-1, f"{size / 1e6:.0f} MB")
            time.sleep(0.25)
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else "").strip()
            raise RuntimeError(err or f"download failed (curl exit {proc.returncode})")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


def hf_snapshot_dir(repo_id: str) -> Optional[Path]:
    """The newest snapshot folder of ``repo_id`` in the HF cache, if any."""
    try:
        from huggingface_hub import constants
        root = (Path(constants.HF_HUB_CACHE)
                / ("models--" + repo_id.replace("/", "--")) / "snapshots")
    except Exception:
        root = (Path.home() / ".cache" / "huggingface" / "hub"
                / ("models--" + repo_id.replace("/", "--")) / "snapshots")
    if not root.is_dir():
        return None
    snaps = [d for d in root.iterdir() if d.is_dir()]
    return max(snaps, key=lambda d: d.stat().st_mtime) if snaps else None


def _hf_snapshot(repo_id: str, local_dir, allow, ignore, progress_cb, cancel) -> None:
    """snapshot_download with real byte progress and cancel support."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import tqdm as hf_tqdm

    kwargs: dict = {"repo_id": repo_id}
    if allow:
        kwargs["allow_patterns"] = allow
    if ignore:
        kwargs["ignore_patterns"] = ignore
    if local_dir is not None:
        kwargs["local_dir"] = str(local_dir)

    total = 0
    try:
        plan = snapshot_download(dry_run=True, **kwargs)
        total = sum(i.file_size for i in plan if i.will_download)
    except Exception:
        pass  # unknown size → indeterminate progress

    devnull = open(os.devnull, "w")

    class _Progress(hf_tqdm):
        """Receives the aggregated *bytes* bar (unit "B", total grows as file
        metadata arrives) plus the outer per-file bar (unit "it").

        tqdm auto-disables itself on non-TTY streams (i.e. always, in the
        packaged app) and returns from __init__ before setting attributes, so
        we force it on (output to devnull) and remember the kwargs ourselves.
        """

        def __init__(self, *a, **kw):
            kw["disable"] = False
            kw["file"] = devnull
            self._tt_bytes = kw.get("unit") == "B"
            super().__init__(*a, **kw)

        def update(self, n=1):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelled()
            out = super().update(n)
            if self._tt_bytes and progress_cb:
                d = int(self.n or 0)
                tot = total or int(self.total or 0)
                if tot:
                    progress_cb(int(5 + 90 * min(1.0, d / tot)),
                                f"{d / 1e6:.0f} / {tot / 1e6:.0f} MB")
                else:
                    progress_cb(-1, f"{d / 1e6:.0f} MB")
            return out

    if progress_cb:
        progress_cb(2, f"Connecting to {repo_id}…")

    # The Xet transfer backend downloads inside native code and reports
    # progress in huge, rare bursts, so a Cancel click could take minutes to
    # be noticed (measured: 205 s on a 4.7 GB repo). Plain chunked HTTP calls
    # our progress hook every 10 MB, which makes Cancel effectively instant.
    from huggingface_hub import constants as hf_constants
    prev_xet = getattr(hf_constants, "HF_HUB_DISABLE_XET", False)
    hf_constants.HF_HUB_DISABLE_XET = True
    try:
        snapshot_download(tqdm_class=_Progress, **kwargs)
    except DownloadCancelled:
        raise
    except Exception as exc:  # HF wraps worker exceptions; unwrap cancel
        if isinstance(exc.__cause__, DownloadCancelled):
            raise exc.__cause__
        raise
    finally:
        hf_constants.HF_HUB_DISABLE_XET = prev_xet
        devnull.close()


_MLX_ALLOW = ["*.json", "*.safetensors", "*.txt", "*.model"]


def download_repo(repo_id: str, ready=None, progress_cb=None,
                  cancel: "threading.Event | None" = None) -> None:
    """Fetch a whole MLX repo into the shared HuggingFace cache with real byte
    progress and instant cancel. ``ready`` is an optional predicate that says
    whether the repo is already complete (defaults to the weights check)."""
    if (ready or _hf_cache_has)(repo_id):
        if progress_cb:
            progress_cb(100, "Done")
        return
    _hf_snapshot(repo_id, None, _MLX_ALLOW, None, progress_cb, cancel)
    if progress_cb:
        progress_cb(100, "Done")


def download_model(
    info: ModelInfo,
    progress_cb=None,
    cancel: "threading.Event | None" = None,
) -> None:
    """Download and extract a model.

    ``progress_cb(percent, msg)`` — percent is 0-100, or -1 when the total
    size is unknown (show an indeterminate bar). ``cancel`` is a
    ``threading.Event``; setting it aborts with ``DownloadCancelled``.
    """
    url = info.download_url
    if not url:
        raise ValueError("No download URL")

    if info.backend == "mlx":
        # Pre-fetch into the shared HF cache with exactly the patterns
        # mlx_qwen3_asr uses, so load_model() finds everything locally
        # instead of downloading (invisibly) at activation time.
        repo_id = url[len("hf://"):]
        if _hf_cache_has(repo_id):
            if progress_cb:
                progress_cb(100, "Done")
            return
        _hf_snapshot(repo_id, None, _MLX_ALLOW, None, progress_cb, cancel)
        if progress_cb:
            progress_cb(100, "Done")
        return

    models_dir = get_models_dir()
    target = models_dir / info.id

    # A leftover directory from a cancelled/failed attempt is NOT a finished
    # download — only skip when the weights are really there. (HF snapshots
    # resume into the same directory; tar extractions are cleaned below.)
    if is_downloaded(info.id):
        return

    is_tar = ".tar.bz2" in url or ".tar.gz" in url
    is_hf_git = "huggingface.co" in url and not is_tar
    is_hf_snapshot = url.startswith("hf://")

    if is_tar:
        ext = ".tar.bz2" if ".tar.bz2" in url else ".tar.gz"
        tmp = models_dir / f"{info.id}{ext}"

        if progress_cb:
            progress_cb(2, "Starting download…")
        _curl_download(url, tmp, progress_cb, cancel, 5, 82)

        if progress_cb:
            progress_cb(88, "Extracting…")

        dirs_before = {d.name for d in models_dir.iterdir() if d.is_dir()}

        flag = "xjf" if ".tar.bz2" in url else "xzf"
        try:
            subprocess.run(
                ["tar", flag, str(tmp), "-C", str(models_dir)],
                check=True,
                capture_output=True,
            )
        except BaseException:
            import shutil
            for d in models_dir.iterdir():
                if d.is_dir() and d.name not in dirs_before:
                    shutil.rmtree(d, ignore_errors=True)
            raise
        finally:
            tmp.unlink(missing_ok=True)

        if not target.exists():
            for d in models_dir.iterdir():
                if d.is_dir() and d.name not in dirs_before and d.name != info.id:
                    d.rename(target)
                    break

    elif is_hf_git:
        if progress_cb:
            progress_cb(-1, "Cloning from HuggingFace…")

        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(target)],
                check=True,
                capture_output=True,
            )
        except BaseException:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            raise

    elif is_hf_snapshot:
        repo_id = url[len("hf://"):]
        # Blocking; callers run this on a worker QThread.
        allow = list(info.hf_allow) or None
        _hf_snapshot(
            repo_id, target,
            allow,
            # Skip pytorch_model.bin variants — we use safetensors.
            None if allow else ["*.bin", "*.h5", "*.msgpack", "*.ot", "*.gguf"],
            progress_cb, cancel,
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
