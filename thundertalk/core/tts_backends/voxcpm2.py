"""VoxCPM2 (OpenBMB, Apache-2.0) on MLX via mlx-audio: designed voices + cloning.

VoxCPM2 can *design* a voice from a short description ("A young woman, warm
and gentle voice"), but every call invents a new person: two pieces of one
passage generated with the same description sound like two different speakers.
To keep one voice, a designed voice is pinned to a single reference clip:

1. The first time a voice is used, one fixed sentence in the voice's language
   is spoken with ``instruct=<description>`` and a fixed per-voice seed.
2. That clip (48 kHz, ≤ 12 s) and its text are saved under
   ``~/.thundertalk/voices_cache/voxcpm2/<slug>.wav`` / ``.json`` and kept in
   the per-synthesis ``context``.
3. Every piece — the first one included — is then generated as an "ultimate
   clone" of that clip (``ref_audio`` + ``prompt_audio`` + ``prompt_text``),
   so a whole passage, and every later session, hears the same person.

A user's own recording (``ClonePrompt``, 24 kHz) is resampled to 48 kHz and
used the same way with its transcript.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.tts_backends.base import (
    BackendInfo,
    BackendVoice,
    Download,
    TtsBackend,
)

log = logging.getLogger(__name__)

REPO = "mlx-community/VoxCPM2-8bit"
SR = 48000                       # model output (and clone input) sample rate
_PATCHES_PER_S = 6.25            # 16 kHz encoder, hop 640, patch 4
_MAX_REF_S = 12.0                # longest reference clip kept for cloning
_CTX_REF = "voxcpm2_ref"         # context key: (voice key, audio48, text)

# One sentence per language, spoken once per designed voice to become its
# reference. Short (≈ 6 s), neutral, with a comma and a full stop so the clip
# carries a natural pause and a sentence-final fall.
_REF_TEXT = {
    "chinese": "你好，很高兴认识你。今天我来为你朗读，我们慢慢说，一句一句讲清楚。",
    "english": "Hello, it's nice to meet you. Today I'll read this for you, slowly and clearly, one sentence at a time.",
}


@dataclass(frozen=True)
class _Design:
    slug: str
    name: str
    language: str
    gender: str
    instruct: str                # what the model is asked for
    blurb_en: str
    blurb_zh: str
    seed: int                    # fixed, so a lost cache regrows the same clip


_DESIGNS: tuple[_Design, ...] = (
    _Design("warm-female-zh", "温婉女声", "chinese", "f",
            "A young Chinese woman, warm and gentle voice, speaking Mandarin softly at a relaxed pace",
            "Asked for: a young woman, warm and gentle, soft relaxed Mandarin.",
            "设定：年轻女性，温暖柔和，语速舒缓的普通话。", 11),
    _Design("calm-male-zh", "沉稳男声", "chinese", "m",
            "A middle-aged Chinese man, calm and steady voice, clear Mandarin at an even pace",
            "Asked for: a middle-aged man, calm and steady, even-paced clear Mandarin.",
            "设定：中年男性，沉稳平和，语速均匀、吐字清楚。", 13),
    _Design("news-anchor-zh", "新闻播音", "chinese", "f",
            "A professional Chinese news anchor woman, formal, crisp and authoritative standard Mandarin broadcast voice",
            "Asked for: a professional female news anchor, formal, crisp standard Mandarin.",
            "设定：专业女新闻主播，正式、干脆、字正腔圆的标准普通话。", 15),
    _Design("male-en", "Arthur (EN)", "english", "m",
            "A British man in his forties, calm, warm and articulate narrator voice",
            "Asked for: a British man in his forties, calm, warm, articulate narration.",
            "设定：四十多岁的英国男性，沉稳温暖、吐字清晰的旁白。", 18),
)
_BY_ID = {f"voxcpm2:{d.slug}": d for d in _DESIGNS}

_HAN = re.compile(r"[㐀-䶿一-鿿]")
_WORD = re.compile(r"[A-Za-z0-9À-ɏ']+")


def _expected_s(text: str) -> float:
    """Rough speaking time, only used to cap generation length."""
    return max(1.0, len(_HAN.findall(text)) / 4.3 + len(_WORD.findall(text)) / 2.6)


def _default_cache_dir() -> Path:
    return Path.home() / ".thundertalk" / "voices_cache" / "voxcpm2"


def _cache_complete(repo: str) -> bool:
    from thundertalk.core.models import _hf_cache_has, hf_snapshot_dir
    if not _hf_cache_has(repo):
        return False
    snap = hf_snapshot_dir(repo)
    return bool(snap and all((snap / f).exists()
                             for f in ("config.json", "model.safetensors", "tokenizer.json")))


class VoxCPM2Backend(TtsBackend):
    info = BackendInfo(
        id="voxcpm2",
        name="VoxCPM2",
        blurb_en=("OpenBMB VoxCPM2 (8-bit MLX, Apache-2.0). Studio-quality 48 kHz; "
                  "voices designed from a description, or cloned from a recording. "
                  "About real time on Apple silicon."),
        blurb_zh=("OpenBMB VoxCPM2（8-bit MLX，Apache-2.0）。48 kHz 高音质；"
                  "可按文字描述设计音色，或从录音克隆。在 Apple 芯片上约为实时速度。"),
        languages=("chinese", "english", "multi"),
        supports_presets=True,
        supports_clone=True,
        needs_gpu=True,
        downloads=(Download(kind="hf", source=REPO, size_mb=3230),),
    )
    sample_rate = SR

    def __init__(self, cache_dir: Optional[Path] = None, repo: str = REPO, use_shipped: bool = True) -> None:
        self.repo = repo
        self.use_shipped = use_shipped          # False only when regenerating the shipped clips
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        self._model = None
        self._refs: dict[str, tuple[np.ndarray, str]] = {}   # slug → (audio48, text)
        self._lock = threading.Lock()

    # ── contract ─────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return _cache_complete(self.repo)

    def voices(self) -> list[BackendVoice]:
        """The shipped reference voices (assets/voices, shared with IndexTTS)."""
        from thundertalk.core.tts_backends.presets import load_presets
        presets = load_presets()
        if not presets or not self.use_shipped:          # regenerating: the designs themselves
            return [BackendVoice(id=f"voxcpm2:{d.slug}", name=d.name, language=d.language,
                                 gender=d.gender, blurb_en=d.blurb_en, blurb_zh=d.blurb_zh)
                    for d in _DESIGNS]
        return [BackendVoice(id=f"voxcpm2:{p.slug}", name=p.name, language=p.language, gender=p.gender,
                             blurb_en=p.blurb_en, blurb_zh=p.blurb_zh) for p in presets]

    def load(self) -> None:
        if self._model is not None:
            return
        from thundertalk.core.tts import TtsModelMissing
        if not self.is_ready():
            raise TtsModelMissing(self.repo)
        with self._lock, GPU_LOCK:
            if self._model is None:
                from mlx_audio.tts.utils import load_model
                from thundertalk.core.models import hf_snapshot_dir
                snap = hf_snapshot_dir(self.repo)
                self._model = load_model(str(snap) if snap else self.repo)

    def unload(self) -> None:
        with self._lock:
            self._model = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        self.load()
        ctx = context if context is not None else {}
        if isinstance(voice, str):
            ref, ref_text = self._preset_ref(voice, ctx)
        else:                                    # ClonePrompt (24 kHz)
            ref, ref_text = self._clone_ref(voice, ctx)
        return self._run(text, seed, ref_audio=ref, prompt_audio=ref, prompt_text=ref_text)

    # ── references ───────────────────────────────────────────────────────

    def _clone_ref(self, prompt, ctx: dict) -> tuple[np.ndarray, str]:
        held = ctx.get(_CTX_REF)
        if held and held[0] is prompt:
            return held[1], held[2]
        from thundertalk.core import audio_io
        from thundertalk.core.tts import SR as CLONE_SR
        a = audio_io.resample(np.asarray(prompt.audio, np.float32).reshape(-1), CLONE_SR, SR)
        ctx[_CTX_REF] = (prompt, a, prompt.text)
        return a, prompt.text

    def _preset_ref(self, voice: str, ctx: dict) -> tuple[np.ndarray, str]:
        """A built-in voice: its shipped clip; failing that (a source checkout
        without assets, or regenerating them) the design it came from."""
        slug = voice.split(":", 1)[1] if voice.startswith("voxcpm2:") else ""
        held = ctx.get(_CTX_REF)
        if held and held[0] == slug:
            return held[1], held[2]
        ref = self._refs.get(slug) or self._read_shipped_slug(slug)
        if ref is None:
            design = _BY_ID.get(voice)
            if design is None:
                raise ValueError(f"unknown VoxCPM2 voice: {voice}")
            return self._designed_ref(design, ctx)
        self._refs[slug] = ref
        ctx[_CTX_REF] = (slug, ref[0], ref[1])
        return ref

    def _designed_ref(self, d: _Design, ctx: dict) -> tuple[np.ndarray, str]:
        held = ctx.get(_CTX_REF)
        if held and held[0] == d.slug:
            return held[1], held[2]
        ref = self._refs.get(d.slug) or self._read_shipped(d) or self._read_cached(d) or self._make_ref(d)
        self._refs[d.slug] = ref
        ctx[_CTX_REF] = (d.slug, ref[0], ref[1])
        return ref

    def _paths(self, d: _Design) -> tuple[Path, Path]:
        return self.cache_dir / f"{d.slug}.wav", self.cache_dir / f"{d.slug}.json"

    def _read_shipped(self, d: _Design) -> Optional[tuple[np.ndarray, str]]:
        return self._read_shipped_slug(d.slug)

    def _read_shipped_slug(self, slug: str) -> Optional[tuple[np.ndarray, str]]:
        """The reference clip shipped in assets/voices (the same one
        IndexTTS uses), so a built-in voice sounds the same in every engine and
        never has to be designed on the user's machine."""
        from thundertalk.core.tts_backends.presets import load_presets
        clip = next((p for p in load_presets() if p.slug == slug), None)
        if clip is None or not self.use_shipped:
            return None
        try:
            from thundertalk.core import audio_io
            x, sr = audio_io.read_wav(str(clip.wav))
            x = x.mean(axis=1) if x.ndim > 1 else x
            return audio_io.resample(np.asarray(x, np.float32), sr, SR), clip.text
        except Exception:
            return None

    def _read_cached(self, d: _Design) -> Optional[tuple[np.ndarray, str]]:
        wav, meta = self._paths(d)
        try:
            info = json.loads(meta.read_text("utf-8"))
            if info.get("instruct") != d.instruct:     # description changed → regrow
                return None
            import soundfile as sf
            a, sr = sf.read(str(wav), dtype="float32")
            if sr != SR or a.ndim != 1 or len(a) < SR // 2:
                return None
            return a, str(info["text"])
        except Exception:
            return None

    def _make_ref(self, d: _Design) -> tuple[np.ndarray, str]:
        """Speak the reference sentence in the designed voice; keep the first
        attempt whose length is plausible and which isn't near-silent."""
        text = _REF_TEXT.get(d.language, _REF_TEXT["english"])
        exp = _expected_s(text)
        best = None
        for attempt in range(3):
            a = self._run(text, d.seed + 1000 * attempt, instruct=d.instruct)
            a = _trim_edges(a)
            dur = len(a) / SR
            ok = 0.6 * exp <= dur <= min(_MAX_REF_S, 2.0 * exp) and _rms(a) > 1e-3
            if ok:
                best = a
                break
            if best is None or abs(dur - exp) < abs(len(best) / SR - exp):
                best = a
        a = best[: int(_MAX_REF_S * SR)].astype(np.float32)
        self._write_cached(d, a, text)
        return a, text

    def _write_cached(self, d: _Design, a: np.ndarray, text: str) -> None:
        wav, meta = self._paths(d)
        try:
            import soundfile as sf
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = wav.with_suffix(".tmp.wav")
            sf.write(str(tmp), a, SR, subtype="PCM_16")
            tmp.replace(wav)
            meta.write_text(json.dumps({"text": text, "instruct": d.instruct, "seed": d.seed},
                                       ensure_ascii=False), "utf-8")
        except Exception:
            log.warning("could not cache VoxCPM2 reference for %s", d.slug, exc_info=True)

    # ── model call ───────────────────────────────────────────────────────

    def _run(self, text: str, seed: int, **kw) -> np.ndarray:
        prompt_text = kw.get("prompt_text") or ""
        cap = int(_PATCHES_PER_S * 3.0 * _expected_s(text)) + 25
        with GPU_LOCK:
            try:
                import mlx.core as mx
                mx.random.seed(int(seed) & 0xFFFFFFFF)
            except Exception:
                pass
            if prompt_text and prompt_text[-1].isascii() and not prompt_text[-1].isspace():
                kw["prompt_text"] = prompt_text + " "     # "time. Hello", not "time.Hello"
            parts = [np.asarray(r.audio, dtype=np.float32).reshape(-1)
                     for r in self._model.generate(text=text, max_tokens=min(cap, 2000), **kw)]
        return np.concatenate(parts) if parts else np.zeros(0, np.float32)


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(a)))) if len(a) else 0.0


def _trim_edges(a: np.ndarray, thr_db: float = -45.0, keep_s: float = 0.08) -> np.ndarray:
    """Drop leading/trailing near-silence (20 ms frames), keeping a short margin."""
    frame = SR // 50
    n = len(a) // frame
    if n < 3:
        return a
    rms = np.sqrt(np.mean(np.square(a[: n * frame].reshape(n, frame)), axis=1) + 1e-12)
    loud = np.nonzero(20 * np.log10(rms) > thr_db)[0]
    if len(loud) == 0:
        return a
    keep = int(keep_s * SR)
    lo = max(0, loud[0] * frame - keep)
    hi = min(len(a), (loud[-1] + 1) * frame + keep)
    return a[lo:hi]
