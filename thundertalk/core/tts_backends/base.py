"""The contract every TTS backend implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class Download:
    """One thing to fetch before the backend can run.

    ``kind`` is "hf" (a HuggingFace repo fetched into the shared HF cache with
    ``models.download_repo``) or "tar" (an archive extracted under
    ``~/.thundertalk/models/<dest>``) or "file" (a single file saved to
    ``~/.thundertalk/models/<dest>``)."""
    kind: str
    source: str                 # repo id or URL
    dest: str = ""              # folder / file name under ~/.thundertalk/models for tar/file
    size_mb: int = 0


@dataclass(frozen=True)
class BackendVoice:
    """A ready-made voice the backend offers (no reference recording needed)."""
    id: str                     # unique across all backends, e.g. "kokoro:3", "voxcpm2:warm-female-zh"
    name: str                   # short display name
    language: str               # "chinese" | "english" | "multi"
    gender: str                 # "f" | "m"
    blurb_en: str = ""
    blurb_zh: str = ""


@dataclass(frozen=True)
class BackendInfo:
    id: str                     # "kokoro" | "voxcpm2" | "indextts"
    name: str                   # display name, e.g. "VoxCPM2"
    blurb_en: str
    blurb_zh: str
    languages: tuple[str, ...]
    supports_presets: bool
    supports_clone: bool
    needs_gpu: bool             # True → MLX / Apple GPU (serialise with GPU_LOCK)
    downloads: tuple[Download, ...] = field(default_factory=tuple)

    @property
    def size_mb(self) -> int:
        return sum(d.size_mb for d in self.downloads)


class TtsBackend:
    """Subclasses implement the four methods below. They must be thread-safe
    for one caller at a time (the engine never calls them concurrently)."""

    info: BackendInfo
    sample_rate: int

    def is_ready(self) -> bool:
        """True when every download is present and complete."""
        raise NotImplementedError

    def voices(self) -> list[BackendVoice]:
        """Built-in voices; empty for clone-only backends."""
        return []

    def load(self) -> None:
        """Load weights (idempotent). Raise ``TtsModelMissing`` if not ready."""
        raise NotImplementedError

    def unload(self) -> None:
        pass

    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        """Speak one short piece (a few sentences) and return mono float32 at
        ``sample_rate``.

        ``voice`` is a ``BackendVoice.id`` string or a
        ``thundertalk.core.tts.ClonePrompt`` (audio at 24 kHz + transcript).
        ``language`` is "chinese" | "english" | …; ``speed`` may be honoured
        natively (else the engine time-stretches afterwards — return
        ``native_speed=True`` via ``context`` if you applied it).
        ``context`` is a per-synthesis dict the backend may use to keep the
        voice consistent across pieces (e.g. store the first piece's audio as
        a reference for the next ones)."""
        raise NotImplementedError
