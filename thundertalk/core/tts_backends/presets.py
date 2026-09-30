"""Built-in reference voices shared by the cloning engines (IndexTTS, VoxCPM2).

Neither engine ships named speakers: both clone whatever reference recording
they are given. The built-in voices are therefore short reference clips that
ship inside the app (assets/voices), one per voice, with their exact transcripts.

Every clip was *designed* with VoxCPM2 (Apache-2.0) from the text description
below — none is a recording of a real person, which is why they can be shipped.
They were generated once with ``tools/make_preset_voices.py`` and verified with
a speech recogniser before being committed.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PresetClip:
    slug: str
    name: str
    language: str        # "chinese" | "english"
    gender: str          # "f" | "m"
    description: str     # what VoxCPM2 was asked for
    blurb_en: str
    blurb_zh: str
    wav: Path            # 24 kHz mono reference
    text: str            # exactly what is said in ``wav``


def presets_dir() -> Path:
    """assets/voices, in source checkouts and frozen builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return base / "assets" / "voices"


@lru_cache(maxsize=1)
def load_presets(root: Optional[str] = None) -> tuple[PresetClip, ...]:
    d = Path(root) if root else presets_dir()
    try:
        items = json.loads((d / "voices.json").read_text("utf-8"))
    except (OSError, ValueError):
        return ()
    out = []
    for it in items:
        wav = d / it["file"]
        if wav.is_file():
            out.append(PresetClip(it["slug"], it["name"], it["language"], it["gender"], it["description"],
                                  it["blurb_en"], it["blurb_zh"], wav, it["text"]))
    return tuple(out)
