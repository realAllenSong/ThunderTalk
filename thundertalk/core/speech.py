"""Speech generation for Studio ▸ Speak, over pluggable backends.

The shared, measured pipeline lives in ``thundertalk.core.tts`` (piece
planning, duration band, read-back verification, retries, level matching,
joining, pitch-preserving speed). This module runs that pipeline on whichever
backend owns the chosen voice:

  * ``kokoro``  — Kokoro v1.1 through sherpa-onnx (CPU, small, 100+ voices)
  * ``voxcpm2`` — VoxCPM2 through mlx-audio (Apple GPU, designed voices and cloning)
  * ``indextts`` — IndexTTS-2.5 through the vendored MLX port (Apple GPU, cloning)

The old Qwen3-TTS engine (``tts.TtsEngine``) is no longer offered in the app.
"""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.tts import (
    ClonePrompt,
    SegmentReport,
    SynthResult,
    TtsCancelled,
    TtsModelMissing,
    TtsParams,
    Verifier,
    active_rms,
    assemble,
    detect_language,
    expected_seconds,
    fade,
    plan_segments,
    time_stretch,
    trim_silence,
)
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend

VoiceSel = Union[str, ClonePrompt]          # "kokoro:3" / "voxcpm2:<slug>" or a clone prompt
ProgressCB = Callable[[int, int, str], None]
DEFAULT_CLONE_BACKEND = "voxcpm2"


# ── registry ─────────────────────────────────────────────────────────────

_BACKENDS: dict[str, TtsBackend] = {}


def _make(bid: str) -> TtsBackend:
    if bid == "kokoro":
        from thundertalk.core.tts_backends.kokoro import KokoroBackend
        return KokoroBackend()
    if bid == "voxcpm2":
        from thundertalk.core.tts_backends.voxcpm2 import VoxCPM2Backend
        return VoxCPM2Backend()
    if bid == "indextts":
        from thundertalk.core.tts_backends.indextts import IndexTTSBackend
        return IndexTTSBackend()
    raise KeyError(bid)


BACKEND_ORDER = ("voxcpm2", "indextts", "kokoro")


def backend(bid: str) -> TtsBackend:
    if bid not in _BACKENDS:
        _BACKENDS[bid] = _make(bid)
    return _BACKENDS[bid]


def backends() -> list[TtsBackend]:
    return [backend(b) for b in BACKEND_ORDER]


def clone_backends() -> list[TtsBackend]:
    return [b for b in backends() if b.info.supports_clone]


def backend_id_for(voice: VoiceSel, clone_backend: Optional[str] = None) -> str:
    if isinstance(voice, ClonePrompt):
        return clone_backend or DEFAULT_CLONE_BACKEND
    bid = voice.split(":", 1)[0]
    if bid not in BACKEND_ORDER:
        raise ValueError(f"Unknown voice: {voice}")
    return bid


def all_voices() -> list[BackendVoice]:
    out: list[BackendVoice] = []
    for b in backends():
        out.extend(b.voices())
    return out


# ── downloads ────────────────────────────────────────────────────────────

def download_backend(info: BackendInfo, progress: Optional[Callable[[int, str], None]] = None,
                     cancel: Optional[threading.Event] = None) -> None:
    """Fetch everything ``info`` needs, with byte progress and cancel."""
    from thundertalk.core import models
    total = max(1, sum(d.size_mb for d in info.downloads))
    done = 0

    def sub(d: Download):
        base = done

        def cb(pct: int, msg: str) -> None:
            if progress:
                if pct < 0:
                    progress(-1, msg)
                else:
                    progress(int((base + d.size_mb * pct / 100) * 100 / total), msg)
        return cb

    for d in info.downloads:
        if cancel is not None and cancel.is_set():
            raise models.DownloadCancelled()
        if d.kind == "hf":
            models.download_repo(d.source, None, sub(d), cancel)
        elif d.kind in ("tar", "file"):
            _download_archive(d, sub(d), cancel)
        else:
            raise ValueError(d.kind)
        done += d.size_mb
    if progress:
        progress(100, "Done")


def _download_archive(d: Download, cb, cancel) -> None:
    import shutil
    import subprocess
    from thundertalk.core import models
    root = models.get_models_dir()
    target = root / d.dest
    if d.kind == "file":
        if target.is_file():
            return
        tmp = target.with_suffix(target.suffix + ".part")
        models._curl_download(d.source, tmp, cb, cancel, 0, 99)
        tmp.replace(target)
        return
    if target.is_dir() and any(target.iterdir()):
        return
    ext = ".tar.bz2" if d.source.endswith(".tar.bz2") else ".tar.gz"
    tmp = root / f"{d.dest}{ext}.part"
    models._curl_download(d.source, tmp, cb, cancel, 0, 92)
    cb(95, "Extracting…")
    staging = root / f".{d.dest}.extracting"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        subprocess.run(["tar", "xjf" if ext == ".tar.bz2" else "xzf", str(tmp), "-C", str(staging)],
                       check=True, capture_output=True)
        inner = [p for p in staging.iterdir() if p.is_dir()]
        src = inner[0] if len(inner) == 1 else staging
        shutil.rmtree(target, ignore_errors=True)
        src.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        tmp.unlink(missing_ok=True)


# ── engine ───────────────────────────────────────────────────────────────

class SpeechEngine:
    """Runs the shared pipeline on a backend. Keeps at most one GPU backend
    loaded at a time (each holds several GB)."""

    def __init__(self) -> None:
        self._loaded: set[str] = set()

    def is_available(self, voice: VoiceSel, clone_backend: Optional[str] = None) -> bool:
        return backend(backend_id_for(voice, clone_backend)).is_ready()

    def unload(self) -> None:
        for bid in list(self._loaded):
            backend(bid).unload()
        self._loaded.clear()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass

    def _ensure(self, bid: str) -> TtsBackend:
        b = backend(bid)
        if not b.is_ready():
            src = next((d.source for d in b.info.downloads), bid)
            raise TtsModelMissing(src)
        if b.info.needs_gpu:
            for other in list(self._loaded):
                if other != bid and backend(other).info.needs_gpu:
                    backend(other).unload()
                    self._loaded.discard(other)
            with GPU_LOCK:
                b.load()
        else:
            b.load()
        self._loaded.add(bid)
        return b

    def _gen(self, b: TtsBackend, text: str, voice: VoiceSel, lang: str, seed: int, speed: float,
             ctx: dict) -> np.ndarray:
        if b.info.needs_gpu:
            with GPU_LOCK:
                a = b.generate(text, voice, lang, seed=seed, speed=speed, context=ctx)
        else:
            a = b.generate(text, voice, lang, seed=seed, speed=speed, context=ctx)
        return np.asarray(a, dtype=np.float32).reshape(-1)

    def _render_piece(self, b: TtsBackend, seg: str, voice: VoiceSel, lang: str, params: TtsParams,
                      seed: int, speed: float, ctx: dict, verifier: Optional[Verifier], state: dict,
                      cancel: Optional[threading.Event]) -> tuple[np.ndarray, SegmentReport]:
        sr = b.sample_rate
        attempts = params.max_attempts if getattr(b, "stochastic", True) else 1
        best, best_pen, best_ratio, best_err, used, misses = None, 1e9, 0.0, None, 0, 0
        for attempt in range(attempts):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            used = attempt + 1
            audio = trim_silence(self._gen(b, seg, voice, lang, seed + attempt * 7919, speed, ctx), sr)
            native = bool(ctx.get("native_speed"))
            exp = expected_seconds(seg, lang) / (speed if native else 1.0)
            ratio = (len(audio) / sr) / exp
            if getattr(b, "stochastic", True):
                # With a read-back verifier, a short take is judged by what it says:
                # fast voices (and clones of fast speakers) legitimately run below the
                # duration estimate, and the verifier catches dropped sentences and
                # endings exactly. Without one, the duration band is the only guard.
                floor = 0.45 if (verifier is not None and not state["off"]) else params.ratio_min
                dur_ok = floor <= ratio <= params.ratio_max and len(audio) / sr > 0.25
            else:   # non-autoregressive: can't run away or drop a sentence; only its pace differs
                dur_ok = len(audio) / sr > 0.25
                ratio = min(max(ratio, params.ratio_min), params.ratio_max)
            err: Optional[float] = None
            if dur_ok and verifier is not None and not state["off"]:
                try:
                    from thundertalk.core import audio_io
                    err = verifier(audio_io.resample(audio, sr, 24000) if sr != 24000 else audio, seg, lang)
                except Exception:
                    err = None
            misread = err is not None and err > params.verify_max_err
            pen = (0.0 if dur_ok else 1.0) + 2.0 * (err or 0.0) + 0.1 * abs(math.log(max(ratio, 1e-3)))
            if pen < best_pen:
                best, best_pen, best_ratio, best_err = audio, pen, ratio, err
            if dur_ok and not misread:
                break
            if dur_ok and err is not None and err > 0.6:
                misses += 1
                if misses >= 2:
                    state["off"] = True
                    break
        floor = 0.45 if (best_err is not None and not state["off"]) else params.ratio_min
        ok = (floor <= best_ratio <= params.ratio_max
              and (state["off"] or best_err is None or best_err <= params.verify_max_err))
        audio = best if best is not None else np.zeros(int(0.2 * sr), np.float32)
        return audio, SegmentReport(seg, expected_seconds(seg, lang), len(audio) / sr, used, ok, best_err)

    def synthesize(self, text: str, voice: VoiceSel, language: Optional[str] = None, speed: float = 1.0,
                   params: Optional[TtsParams] = None, seed: Optional[int] = None,
                   progress: Optional[ProgressCB] = None, cancel: Optional[threading.Event] = None,
                   verifier: Optional[Verifier] = None, clone_backend: Optional[str] = None) -> SynthResult:
        text = text.strip()
        if not text:
            raise ValueError("Nothing to say — the text is empty.")
        bid = backend_id_for(voice, clone_backend)
        b = self._ensure(bid)
        if isinstance(voice, str) and voice not in {v.id for v in b.voices()}:
            raise ValueError(f"Unknown voice: {voice}")
        params = params or TtsParams()
        lang = language if language and language != "auto" else detect_language(text)
        base_seed = seed if seed is not None else int(time.time()) & 0x7FFFFFFF
        sr = b.sample_rate
        plan = plan_segments(text, lang, params.chunk_scale)
        t0 = time.monotonic()
        ctx: dict = {}
        reports: list[SegmentReport] = []
        pieces: list[np.ndarray] = []
        pauses: list[float] = []
        state = {"off": False}
        for idx, (seg, pause) in enumerate(plan):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            if progress:
                progress(idx, len(plan), seg)
            audio, rep = self._render_piece(b, seg, voice, lang, params, base_seed + idx * 1009, speed, ctx,
                                            verifier, state, cancel)
            reports.append(rep)
            pieces.append(fade(audio, sr=sr))
            pauses.append(pause)
            if progress:
                progress(idx + 1, len(plan), seg)
        rms = [active_rms(p, sr) for p in pieces]
        target = float(np.median(rms)) if rms else 1.0
        leveled = [p * min(max(target / max(r, 1e-6), 0.5), 2.0) for p, r in zip(pieces, rms)]
        mix = assemble(leveled, pauses[:-1], sr)
        if abs(speed - 1.0) >= 0.02 and not ctx.get("native_speed"):
            mix = time_stretch(mix, speed, sr)
        peak = float(np.max(np.abs(mix))) if len(mix) else 0.0
        if peak > 1e-6:
            mix = mix * (0.891 / peak)
        return SynthResult(mix.astype(np.float32), sr, lang, reports, time.monotonic() - t0)


_ENGINE: Optional[SpeechEngine] = None


def get_engine() -> SpeechEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SpeechEngine()
    return _ENGINE


def cache_root() -> Path:
    return Path.home() / ".thundertalk"
