"""Saved voices for cloning.

A cloned voice is just a short clean reference recording plus its transcript;
Qwen3-TTS (Base) imitates it at synthesis time. We keep the prepared 24 kHz
reference on disk so the user records/imports once and reuses it forever:

    ~/.thundertalk/voices/<id>/voice.json     {"name", "ref_text", "duration", "created"}
    ~/.thundertalk/voices/<id>/ref.wav        16-bit mono, 24 kHz, level-normalised
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core import audio_io
from thundertalk.core.tts import SR, ClonePrompt, active_rms, trim_silence

MIN_REF_SECONDS = 3.0
IDEAL_MIN, IDEAL_MAX = 5.0, 15.0
MAX_REF_SECONDS = 20.0


def voices_dir() -> Path:
    d = Path.home() / ".thundertalk" / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class SavedVoice:
    id: str
    name: str
    ref_text: str
    duration: float
    created: float

    @property
    def wav_path(self) -> Path:
        return voices_dir() / self.id / "ref.wav"


@dataclass
class ReferenceCheck:
    audio: np.ndarray                 # prepared, 24 kHz mono
    duration: float
    warnings: list[str]
    ok: bool                          # False → too short/quiet to clone from


def prepare_reference(x: np.ndarray, sr: int) -> ReferenceCheck:
    """Turn a raw recording into a usable cloning reference and say what's wrong
    with it, in words a user can act on (message keys are stable identifiers)."""
    warnings: list[str] = []
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = audio_io.resample(x.astype(np.float32), sr, SR)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 0.999:
        warnings.append("clipping")
    x = trim_silence(x, SR, thr_db=-40.0, keep_head=0.1, keep_tail=0.2)
    dur = len(x) / SR
    if dur > MAX_REF_SECONDS:
        # Cut at the quietest moment near the limit so we don't end mid-word.
        limit = int(MAX_REF_SECONDS * SR)
        lo, hi = int((MAX_REF_SECONDS - 3.0) * SR), limit
        frame = int(0.02 * SR)
        seg = x[lo:hi]
        n = len(seg) // frame
        if n > 1:
            e = np.sqrt(np.mean(seg[: n * frame].reshape(n, frame) ** 2, axis=1))
            cut = lo + int(np.argmin(e)) * frame + frame // 2
        else:
            cut = limit
        x = x[:cut]
        dur = len(x) / SR
        warnings.append("trimmed_long")
    rms = active_rms(x, SR) if len(x) else 0.0
    if rms < 0.004:
        warnings.append("too_quiet")
    if dur < IDEAL_MIN:
        warnings.append("short")
    # level-normalise so quiet phone recordings and loud ones behave alike
    if rms > 1e-5:
        x = x * min(0.08 / rms, 8.0)
        p = float(np.max(np.abs(x)))
        if p > 0.95:
            x = x * (0.95 / p)
    ok = dur >= MIN_REF_SECONDS and "too_quiet" not in warnings
    return ReferenceCheck(x.astype(np.float32), dur, warnings, ok)


def _slug(name: str) -> str:
    s = re.sub(r"[^\w一-鿿-]+", "-", name.strip().lower()).strip("-")
    return (s or "voice")[:32]


class VoiceLibrary:
    def list(self) -> list[SavedVoice]:
        out: list[SavedVoice] = []
        for d in sorted(voices_dir().iterdir()):
            meta = d / "voice.json"
            if d.is_dir() and meta.is_file() and (d / "ref.wav").is_file():
                try:
                    j = json.loads(meta.read_text(encoding="utf-8"))
                    out.append(SavedVoice(d.name, j["name"], j.get("ref_text", ""),
                                          float(j.get("duration", 0.0)), float(j.get("created", 0.0))))
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(out, key=lambda v: v.created)

    def get(self, voice_id: str) -> Optional[SavedVoice]:
        return next((v for v in self.list() if v.id == voice_id), None)

    def add(self, name: str, audio: np.ndarray, ref_text: str) -> SavedVoice:
        base = _slug(name)
        vid, n = base, 2
        while (voices_dir() / vid).exists():
            vid, n = f"{base}-{n}", n + 1
        d = voices_dir() / vid
        d.mkdir(parents=True)
        audio_io.write_wav(str(d / "ref.wav"), audio, SR)
        v = SavedVoice(vid, name.strip() or vid, ref_text.strip(), len(audio) / SR, time.time())
        (d / "voice.json").write_text(json.dumps({
            "name": v.name, "ref_text": v.ref_text, "duration": v.duration, "created": v.created,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return v

    def update_text(self, voice_id: str, ref_text: str) -> None:
        meta = voices_dir() / voice_id / "voice.json"
        j = json.loads(meta.read_text(encoding="utf-8"))
        j["ref_text"] = ref_text.strip()
        meta.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")

    def rename(self, voice_id: str, name: str) -> None:
        meta = voices_dir() / voice_id / "voice.json"
        j = json.loads(meta.read_text(encoding="utf-8"))
        j["name"] = name.strip() or j["name"]
        meta.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, voice_id: str) -> None:
        d = voices_dir() / voice_id
        if d.is_dir() and d.parent == voices_dir():
            shutil.rmtree(d, ignore_errors=True)

    def prompt(self, voice_id: str) -> ClonePrompt:
        v = self.get(voice_id)
        if v is None:
            raise KeyError(voice_id)
        x, sr = audio_io.read_wav(str(v.wav_path))
        if x.ndim > 1:
            x = x.mean(axis=1)
        if sr != SR:
            x = audio_io.resample(x, sr, SR)
        return ClonePrompt(x.astype(np.float32), v.ref_text, v.name)
