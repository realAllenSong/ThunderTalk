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
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.memory_policy import managed
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
    from thundertalk.core.runtime import SIZE_MB, install, needed
    component_mb = SIZE_MB if info.id == "indextts" and needed() else 0
    total = max(1, sum(d.size_mb for d in info.downloads) + component_mb)
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

    if info.id == "indextts":
        install(sub(Download(kind="component", source="", size_mb=component_mb)), cancel)
        done += component_mb
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

def voice_key(voice: VoiceSel) -> str:
    """A stable key for a voice: its id, or a hash of a clone prompt's audio
    (the Speak tab builds a fresh ``ClonePrompt`` object for every request)."""
    if isinstance(voice, ClonePrompt):
        import hashlib
        a = np.ascontiguousarray(np.asarray(voice.audio, dtype=np.float32).reshape(-1))
        return "clone:" + hashlib.sha1(a.tobytes()).hexdigest()[:16] + ":" + voice.text
    return voice


class SpeechEngine:
    """Runs the shared pipeline on a backend. Keeps at most one GPU backend
    loaded at a time (each holds several GB).

    A GPU backend is loaded, warmed up and used under one re-entrant lock, so a
    second caller (Generate clicked while the Speak tab is still preloading)
    waits for the load already in flight instead of starting another, a
    preload of another engine never swaps a model out mid-synthesis, and two
    GPU models are never in memory together."""

    def __init__(self) -> None:
        self._loaded: set[str] = set()
        self._swap = threading.RLock()         # held while a GPU backend is loaded or in use
        self._mu = threading.Lock()            # guards the bookkeeping below
        self._loading: set[str] = set()
        self._warm: set[tuple[str, str]] = set()   # (backend id, voice key) already warmed up
        self._active = 0                       # synthesize/preload calls running

    def is_available(self, voice: VoiceSel, clone_backend: Optional[str] = None) -> bool:
        return backend(backend_id_for(voice, clone_backend)).is_ready()

    def is_loaded(self, bid: str) -> bool:
        with self._mu:
            return bid in self._loaded

    def is_loading(self, bid: Optional[str] = None) -> bool:
        with self._mu:
            return bool(self._loading) if bid is None else bid in self._loading

    def is_warm(self, bid: str, voice: VoiceSel) -> bool:
        with self._mu:
            return bid in self._loaded and (bid, voice_key(voice)) in self._warm

    def is_busy(self) -> bool:
        """A load, warm-up or synthesis is running."""
        with self._mu:
            return self._active > 0 or bool(self._loading)

    def _forget(self, bid: str) -> None:
        with self._mu:
            self._loaded.discard(bid)
            self._warm = {k for k in self._warm if k[0] != bid}

    def unload(self) -> None:
        # A closing window may race a preload/synthesis worker. Keep model
        # destruction under the same slot and Metal locks as its use.
        with self._swap, GPU_LOCK:
            for bid in list(self._loaded):
                backend(bid).unload()
                self._forget(bid)
            from thundertalk.core.memory_policy import trim_caches
            trim_caches()

    def release_idle(self) -> bool:
        if not self._swap.acquire(blocking=False):
            return False
        try:
            if self.is_busy():
                return False
            self.unload()
            return True
        finally:
            self._swap.release()

    def release_gpu(self) -> bool:
        """Free the GPU voice model so another big model (MOSS, a dictation
        model, the translator) can load. Never waits: returns False and keeps
        it when it is being loaded or used."""
        if not self._swap.acquire(blocking=False):
            return False
        try:
            gpu = [b for b in list(self._loaded) if backend(b).info.needs_gpu]
            for bid in gpu:
                backend(bid).unload()
                self._forget(bid)
        finally:
            self._swap.release()
        if gpu:
            from thundertalk.core.memory_policy import trim_caches
            trim_caches()
        return True

    @contextmanager
    def _holding(self, b: TtsBackend, cancel: Optional[threading.Event]):
        """Own the GPU slot for ``b``; ``cancel`` stops the wait for it."""
        if not b.info.needs_gpu:
            yield
            return
        while not self._swap.acquire(timeout=0.1):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
        try:
            yield
        finally:
            self._swap.release()

    @contextmanager
    def _running(self):
        from thundertalk.core.memory_policy import using, trim_caches
        with using(self, release="release_idle"):
            with self._mu:
                self._active += 1
            try:
                yield
            finally:
                try:
                    trim_caches()
                finally:
                    with self._mu:
                        self._active -= 1

    @managed(release="release_idle")
    def load_backend(self, bid: str, cancel: Optional[threading.Event] = None) -> TtsBackend:
        """Load ``bid`` (idempotent), attaching to a load already in flight.
        ``cancel`` only stops the *wait*; a load that has started finishes."""
        b = backend(bid)
        if not b.is_ready():
            src = next((d.source for d in b.info.downloads), bid)
            raise TtsModelMissing(src)
        with self._mu:
            self._loading.add(bid)
        try:
            with self._holding(b, cancel):
                if b.info.needs_gpu:
                    for other in list(self._loaded):
                        if other != bid and backend(other).info.needs_gpu:
                            backend(other).unload()
                            self._forget(other)
                    with GPU_LOCK:
                        b.load()
                else:
                    b.load()
                with self._mu:
                    self._loaded.add(bid)
        finally:
            with self._mu:
                self._loading.discard(bid)
        return b

    _ensure = load_backend

    def preload(self, bid: str, voice: Optional[VoiceSel] = None, language: str = "chinese",
                warm: bool = True, cancel: Optional[threading.Event] = None) -> bool:
        """Load ``bid`` ahead of a request and, with ``warm``, speak one short
        word in ``voice`` so the first real request pays no first-call costs.
        Returns False when cancelled; whatever finished loading stays loaded."""
        cancelled = lambda: cancel is not None and cancel.is_set()      # noqa: E731
        try:
            with self._running(), self._holding(backend(bid), cancel):
                b = self.load_backend(bid, cancel)
                if not warm or voice is None or cancelled():
                    return not cancelled()
                key = (bid, voice_key(voice))
                with self._mu:
                    if key in self._warm:
                        return True
                b.warm_up(voice, language)          # backends take GPU_LOCK per call
                with self._mu:
                    self._warm.add(key)
                return True
        except TtsCancelled:
            return False

    def _gen(self, b: TtsBackend, text: str, voice: VoiceSel, lang: str, seed: int, speed: float,
             ctx: dict) -> np.ndarray:
        if b.info.needs_gpu:
            from thundertalk.core.mlx_runtime import mlx_context
            with GPU_LOCK, mlx_context():
                a = b.generate(text, voice, lang, seed=seed, speed=speed, context=ctx)
                return np.asarray(a, dtype=np.float32).reshape(-1)
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
        with self._running(), self._holding(backend(bid), cancel):
            b = self.load_backend(bid, cancel)
            res = self._synthesize(b, text, voice, language, speed, params, seed, progress, cancel, verifier)
            with self._mu:
                if bid in self._loaded:
                    self._warm.add((bid, voice_key(voice)))
            return res

    def _synthesize(self, b: TtsBackend, text: str, voice: VoiceSel, language: Optional[str], speed: float,
                    params: Optional[TtsParams], seed: Optional[int], progress: Optional[ProgressCB],
                    cancel: Optional[threading.Event], verifier: Optional[Verifier]) -> SynthResult:
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


def release_gpu() -> bool:
    """Call before loading another big GPU model: frees the idle GPU voice
    model, if any (see ``SpeechEngine.release_gpu``)."""
    return _ENGINE.release_gpu() if _ENGINE is not None else True


def cache_root() -> Path:
    return Path.home() / ".thundertalk"
