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

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.mlx_runtime import evaluate_model, serialized_mlx
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
        self._clone_refs: dict[str, np.ndarray] = {}          # sha1 of a clone prompt → audio48
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

    @serialized_mlx
    def load(self) -> None:
        if self._model is not None:
            return
        from thundertalk.core.tts import TtsModelMissing
        if not self.is_ready():
            raise TtsModelMissing(self.repo)
        # Python imports are CPU work: keep them outside GPU_LOCK so dictation
        # isn't held up behind them.
        from mlx_audio.tts.models.voxcpm2 import voxcpm2 as arch
        from mlx_audio.tts.utils import load_model
        from thundertalk.core.models import hf_snapshot_dir
        with self._lock, GPU_LOCK:
            if self._model is None:
                snap = hf_snapshot_dir(self.repo)
                with _fast_tokenizer_hook(arch.Model):
                    model = load_model(str(snap) if snap else self.repo)
                _cache_encodes(model)
                evaluate_model(model)
                self._model = model

    @serialized_mlx
    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._refs.clear()
            self._clone_refs.clear()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    @serialized_mlx
    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        self.load()
        ref, ref_text = self._reference(voice, context if context is not None else {})
        return self._run(text, seed, ref_audio=ref, prompt_audio=ref, prompt_text=ref_text)

    @serialized_mlx
    def warm_up(self, voice, language: str) -> None:
        """Prepare and VAE-encode ``voice``'s reference (cached for later
        requests) and run a few decoding steps so every kernel is built."""
        self.load()
        ref, ref_text = self._reference(voice, {})
        self._run("你好。" if language == "chinese" else "Hello.", 0, max_tokens=6,
                  ref_audio=ref, prompt_audio=ref, prompt_text=ref_text)

    # ── references ───────────────────────────────────────────────────────

    def _reference(self, voice, ctx: dict) -> tuple[np.ndarray, str]:
        if isinstance(voice, str):
            return self._preset_ref(voice, ctx)
        return self._clone_ref(voice, ctx)          # ClonePrompt (24 kHz)

    def _clone_ref(self, prompt, ctx: dict) -> tuple[np.ndarray, str]:
        held = ctx.get(_CTX_REF)
        if held and held[0] is prompt:
            return held[1], held[2]
        # Keyed by content: the Speak tab builds a new ClonePrompt per request,
        # and the same 48 kHz array lets the encoded reference be reused.
        x = np.ascontiguousarray(np.asarray(prompt.audio, np.float32).reshape(-1))
        key = hashlib.sha1(x.tobytes()).hexdigest()
        a = self._clone_refs.get(key)
        if a is None:
            from thundertalk.core import audio_io
            from thundertalk.core.tts import SR as CLONE_SR
            a = audio_io.resample(x, CLONE_SR, SR)
            self._clone_refs[key] = a
            while len(self._clone_refs) > 4:
                self._clone_refs.pop(next(iter(self._clone_refs)))
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

    def _run(self, text: str, seed: int, max_tokens: Optional[int] = None, **kw) -> np.ndarray:
        prompt_text = kw.get("prompt_text") or ""
        cap = max_tokens or int(_PATCHES_PER_S * 3.0 * _expected_s(text)) + 25
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


class _FastTokenizer:
    """The two tokenizer calls VoxCPM2 makes, on the Rust ``tokenizers``
    library directly. mlx-audio loads it through transformers'
    ``AutoTokenizer``, whose imports alone cost 4–5 s on an idle M3 Max and
    far more on a busy machine. Configured the way transformers 5 builds
    ``LlamaTokenizer`` (no normalizer, Metaspace with prepend "always", the
    added tokens of tokenizer_config.json): the same tokens on ordinary text."""

    def __init__(self, path: Path) -> None:
        from tokenizers import AddedToken, Tokenizer, normalizers, pre_tokenizers
        cfg = json.loads((path / "tokenizer_config.json").read_text("utf-8"))
        if cfg.get("tokenizer_class") not in ("LlamaTokenizer", "LlamaTokenizerFast"):
            raise ValueError(f"unexpected tokenizer {cfg.get('tokenizer_class')}")
        tk = Tokenizer.from_file(str(path / "tokenizer.json"))
        tk.normalizer = normalizers.Sequence([])
        tk.pre_tokenizer = pre_tokenizers.Metaspace(replacement="▁", prepend_scheme="always", split=False)
        added = sorted(cfg.get("added_tokens_decoder", {}).items(), key=lambda kv: int(kv[0]))
        tk.add_special_tokens([AddedToken(v["content"], single_word=bool(v.get("single_word")),
                                          lstrip=bool(v.get("lstrip")), rstrip=bool(v.get("rstrip")),
                                          normalized=False, special=True) for _k, v in added])
        unk = cfg.get("unk_token") or "<unk>"
        self.unk_token_id = tk.token_to_id(unk["content"] if isinstance(unk, dict) else unk) or 0
        self._tk = tk

    def tokenize(self, text: str) -> list[str]:
        return self._tk.encode(text, add_special_tokens=False).tokens

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        ids = (self._tk.token_to_id(t) for t in tokens)
        return [self.unk_token_id if i is None else i for i in ids]


@contextmanager
def _fast_tokenizer_hook(model_cls):
    """Give the model a ``_FastTokenizer`` while mlx-audio loads it; mlx-audio's
    own hook (transformers) still runs if that fails."""
    orig = model_cls.__dict__.get("post_load_hook")
    if orig is None:
        yield
        return

    def hook(cls, model, model_path):
        try:
            model.tokenizer = _FastTokenizer(Path(model_path))
            return model
        except Exception:
            log.warning("VoxCPM2: fast tokenizer unavailable, using transformers", exc_info=True)
            return orig.__func__(cls, model, model_path)

    model_cls.post_load_hook = classmethod(hook)
    try:
        yield
    finally:
        model_cls.post_load_hook = orig


def _cache_encodes(model, keep: int = 8) -> None:
    """Remember the VAE encoding of each reference clip. mlx-audio re-encodes
    the reference twice on every call (as ``ref_audio`` and ``prompt_audio``),
    ~0.2–1.5 s per piece; a voice's clip never changes while the model is loaded."""
    orig = model._encode_wav
    cache: dict[tuple, object] = {}

    def encode(audio_input, padding_mode: str = "right", trim_silence_vad: bool = False):
        if not isinstance(audio_input, np.ndarray):
            return orig(audio_input, padding_mode=padding_mode, trim_silence_vad=trim_silence_vad)
        a = np.ascontiguousarray(audio_input, dtype=np.float32)
        key = (hashlib.sha1(a.tobytes()).hexdigest(), a.shape, padding_mode, trim_silence_vad)
        feat = cache.pop(key, None)
        if feat is None:
            import mlx.core as mx
            feat = orig(a, padding_mode=padding_mode, trim_silence_vad=trim_silence_vad)
            mx.eval(feat)
        cache[key] = feat                       # most recently used last
        while len(cache) > keep:
            cache.pop(next(iter(cache)))
        return feat

    model._encode_wav = encode


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
