"""Speech generation for Studio ▸ Speak, over pluggable backends.

The shared, measured pipeline lives in ``thundertalk.core.tts`` (piece
planning, duration band, read-back verification, retries, level matching,
joining, pitch-preserving speed). This module runs that pipeline on whichever
backend owns the chosen voice:

  * ``kokoro``  — Kokoro v1.1 through sherpa-onnx (CPU, small, 100+ voices)
  * ``voxcpm2`` — VoxCPM2 through mlx-audio (Apple GPU, designed voices and cloning)
  * ``indextts`` — IndexTTS-2.5 through the vendored MLX port (Apple GPU, cloning)
  * ``longcat`` — LongCat-AudioDiT 1B through mlx-audio (Apple GPU, cloning, zh/en)
  * ``apple``   — the voices built into macOS (AVSpeechSynthesizer, no download)

The old Qwen3-TTS engine (``tts.TtsEngine``) is no longer offered in the app.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
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
    PAUSE_BREAK,
    assemble,
    close_piece,
    detect_language,
    ends_cut,
    expected_seconds,
    fade,
    median_semitones,
    pick_takes,
    plan_segments,
    plan_sentences,
    time_stretch,
    trim_silence,
)
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, Download, TtsBackend

VoiceSel = Union[str, ClonePrompt]          # "kokoro:3" / "voxcpm2:<slug>" or a clone prompt
POLISH_TAKES = 4                 # takes per sentence in polished output
# Cost per semitone two neighbouring sentences differ beyond half a semitone.
# A three-semitone jump then weighs as much as a tenth of the sentence misheard.
POLISH_PITCH_WEIGHT = 0.08
# Speaker similarity of a take to the voice's reference (CAM++, core/speaker_check).
# 288 takes of 6 voices (4 built in, 2 the user's own) on VoxCPM2, LongCat and IndexTTS,
# 2026-11-22: a take vs its own voice 0.63–0.94 (median 0.87), vs the closest other
# voice at most 0.40; no take was nearer another voice. Short sentences score lower
# across the board, so takes are compared with the best take of their own sentence:
# the four takes of one sentence differed by 0.03 (median) to 0.18 (most).
SIM_FLOOR = 0.5              # below this a take sounds like someone else: as bad as a misread
SIM_WEIGHT = 1.0             # 0.1 short of the sentence's best costs about a 1.3-semitone jump
SIM_SLACK = 0.03             # ordinary take-to-take variation, free


@dataclass
class _Take:
    audio: np.ndarray
    pen: float
    ratio: float
    err: Optional[float]
    cut: bool
    dur_ok: bool
    misread: bool
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
    if bid == "longcat":
        from thundertalk.core.tts_backends.longcat import LongCatBackend
        return LongCatBackend()
    if bid == "apple":
        from thundertalk.core.tts_backends.apple import AppleBackend
        return AppleBackend()
    from thundertalk.core import cloud
    if bid in cloud.TTS_PROVIDERS:
        from thundertalk.core.tts_backends.cloud import CloudBackend
        return CloudBackend(bid)
    raise KeyError(bid)


BACKEND_ORDER = ("voxcpm2", "indextts", "longcat", "kokoro", "apple")


def backend(bid: str) -> TtsBackend:
    if bid not in _BACKENDS:
        _BACKENDS[bid] = _make(bid)
    return _BACKENDS[bid]


def cloud_ids() -> tuple[str, ...]:
    from thundertalk.core import cloud
    return cloud.TTS_PROVIDERS


def backends() -> list[TtsBackend]:
    """Local engines, then cloud engines that have a key (never unconfigured ones)."""
    from thundertalk.core import cloud
    return [backend(b) for b in BACKEND_ORDER] + [backend(b) for b in cloud.TTS_PROVIDERS
                                                  if cloud.is_configured(b)]


def clone_backends() -> list[TtsBackend]:
    return [b for b in backends() if b.info.supports_clone]


def backend_id_for(voice: VoiceSel, clone_backend: Optional[str] = None) -> str:
    if isinstance(voice, ClonePrompt):
        return clone_backend or DEFAULT_CLONE_BACKEND
    bid = voice.split(":", 1)[0]
    if bid not in BACKEND_ORDER and bid not in cloud_ids():
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
    import errno
    import shutil
    from thundertalk.core import models
    from thundertalk.core.runtime import COMPONENTS, install, needed, size_mb
    for c in info.components:
        if c not in COMPONENTS:
            raise ValueError(c)
    # Installing the music component also installs torch; plain torch keeps the old call shape.
    kw = {"component": "music"} if "music" in info.components else {}
    component_mb = size_mb(**kw) if info.components and needed(**kw) else 0
    total = max(1, sum(d.size_mb for d in info.downloads) + component_mb)
    pending_mb = component_mb + sum(_pending_size_mb(d) for d in info.downloads if not download_ready(d))
    # HF may retain an incomplete blob next to the finished file.
    if pending_mb and shutil.disk_usage(Path.home()).free < pending_mb * 1_000_000 * 1.2:
        raise OSError(errno.ENOSPC, "Not enough disk space (disk full)")
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

    if info.components:
        install(sub(Download(kind="component", source="", size_mb=component_mb)), cancel, **kw)
        done += component_mb
    for d in info.downloads:
        if cancel is not None and cancel.is_set():
            raise models.DownloadCancelled()
        if d.kind == "hf":
            ready = (lambda repo, files=d.required: models.hf_snapshot_has(repo, files)) if d.required else None
            allow = (*models._MLX_ALLOW, *d.allow) if d.allow else None
            models.download_repo(d.source, ready, sub(d), cancel, allow=allow)
        elif d.kind == "hf-dir":
            _download_hf_dir(d, sub(d), cancel)
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


def _hf_dir_marker(d: Download) -> str:
    """Several hf-dir downloads may share one folder (ACE-Step's checkpoints/),
    so each gets its own completion marker."""
    import hashlib
    key = "\n".join((d.source, *d.allow)).encode()
    return f".complete-{hashlib.sha1(key).hexdigest()[:12]}"


def download_ready(d: Download) -> bool:
    from thundertalk.core import models
    target = models.get_models_dir() / d.dest
    if d.kind == "hf":
        if d.required:
            return models.hf_snapshot_has(d.source, d.required)
        return models._hf_cache_has(d.source)
    if d.kind == "hf-dir":
        return (target / _hf_dir_marker(d)).is_file()
    if d.kind == "file":
        return target.is_file()
    return target.is_dir() and any(target.iterdir())


def _pending_size_mb(d: Download) -> int:
    from thundertalk.core import models
    target = models.get_models_dir() / d.dest
    if d.convert_bf16 and (target / (_hf_dir_marker(d) + ".fetched")).is_file():
        # The F32 download exists; only one bf16 staging shard needs disk space.
        return max((p.stat().st_size for p in target.glob("*.safetensors")), default=0) // 2_000_000
    return d.size_mb


def _download_hf_dir(d: Download, cb, cancel) -> None:
    """Copy the allowed files of a repo into ~/.thundertalk/models/<dest>.

    An explicit allow-list keeps repo code (``*.py``) off the disk; the marker
    is written only after every file arrived, so an interrupted copy resumes."""
    from thundertalk.core import models
    if not d.dest or not d.allow or any(g.endswith(".py") or g in ("*", "**") for g in d.allow):
        raise ValueError(f"hf-dir needs a dest and a code-free allow list: {d.source}")
    if download_ready(d):
        return
    target = models.get_models_dir() / d.dest
    target.mkdir(parents=True, exist_ok=True)
    fetched = target / (_hf_dir_marker(d) + ".fetched")
    if not d.convert_bf16 or not fetched.is_file():
        models._hf_snapshot(d.source, target, list(d.allow), None, cb, cancel)
        if d.convert_bf16:
            fetched.write_text(d.source)
    if d.convert_bf16:
        from thundertalk.core.i18n import t
        from thundertalk.core.music_checkpoint import convert_process
        convert_process(target, lambda pct: cb(-1, t("studio.compose.converting").format(pct=pct)), cancel)
    (target / _hf_dir_marker(d)).write_text(d.source)


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
            from thundertalk.core.priority import alternate_job
            try:
                with alternate_job(cancel):
                    yield
            except RuntimeError as exc:
                if str(exc) == "cancelled":
                    raise TtsCancelled() from exc
                raise
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
                        if bid not in self._loaded:
                            from thundertalk.core.priority import background_headroom
                            background_headroom(getattr(b.info, "size_mb", 0))
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

    def _take(self, b: TtsBackend, seg: str, said: str, voice: VoiceSel, lang: str, params: TtsParams,
              seed: int, speed: float, ctx: dict, verifier: Optional[Verifier], state: dict) -> "_Take":
        """Generate one take of a piece and judge it: length, read-back, ending."""
        sr = b.sample_rate
        stochastic = getattr(b, "stochastic", True)
        raw = self._gen(b, said, voice, lang, seed, speed, ctx)
        cut = stochastic and ends_cut(raw, sr)
        audio = trim_silence(raw, sr)
        native = bool(ctx.get("native_speed"))
        exp = expected_seconds(seg, lang) / (speed if native else 1.0)
        ratio = (len(audio) / sr) / exp
        if stochastic:
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
        pen = ((0.0 if dur_ok else 1.0) + 2.0 * (err or 0.0) + 0.1 * abs(math.log(max(ratio, 1e-3)))
               + (0.15 if cut else 0.0))        # a clipped last word: worse than a few misheard characters
        return _Take(audio, pen, ratio, err, cut, dur_ok, misread)

    def _report(self, seg: str, lang: str, sr: int, t: Optional["_Take"], used: int, params: TtsParams,
                state: dict, pitch: Optional[float] = None, sim: Optional[float] = None
                ) -> tuple[np.ndarray, SegmentReport]:
        if t is None:
            audio = np.zeros(int(0.2 * sr), np.float32)
            return audio, SegmentReport(seg, expected_seconds(seg, lang), len(audio) / sr, used, False, None)
        floor = 0.45 if (t.err is not None and not state["off"]) else params.ratio_min
        ok = (floor <= t.ratio <= params.ratio_max
              and (state["off"] or t.err is None or t.err <= params.verify_max_err))
        return t.audio, SegmentReport(seg, expected_seconds(seg, lang), len(t.audio) / sr, used, ok, t.err,
                                      bool(t.cut), pitch, sim)

    @staticmethod
    def _note_miss(t: "_Take", state: dict, misses: list) -> None:
        """Two takes the recogniser hears as something else entirely: it can't
        follow this voice (a whisper, a heavy accent), so stop asking it."""
        if t.dur_ok and t.err is not None and t.err > 0.6:
            misses.append(1)
            if len(misses) >= 2:
                state["off"] = True

    def _render_piece(self, b: TtsBackend, seg: str, voice: VoiceSel, lang: str, params: TtsParams,
                      seed: int, speed: float, ctx: dict, verifier: Optional[Verifier], state: dict,
                      cancel: Optional[threading.Event]) -> tuple[np.ndarray, SegmentReport]:
        """The first take that passes, retried up to ``max_attempts`` times."""
        attempts = params.max_attempts if getattr(b, "stochastic", True) else 1
        said = close_piece(seg, lang)        # what the model is given; ``seg`` is what it is checked against
        best: Optional[_Take] = None
        used, misses = 0, []
        for attempt in range(attempts):
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            used = attempt + 1
            t = self._take(b, seg, said, voice, lang, params, seed + attempt * 7919, speed, ctx, verifier, state)
            if best is None or t.pen < best.pen:
                best = t
            if t.dur_ok and not t.misread and not t.cut:
                break
            self._note_miss(t, state, misses)
            if state["off"]:
                break
        return self._report(seg, lang, b.sample_rate, best, used, params, state)

    def _takes(self, b: TtsBackend, seg: str, voice: VoiceSel, lang: str, params: TtsParams, seed: int,
               speed: float, ctx: dict, verifier: Optional[Verifier], state: dict,
               cancel: Optional[threading.Event], keep: int = 3) -> tuple[list["_Take"], int]:
        """``params.takes`` takes of one piece (two more if none passes), the
        best ``keep`` of them kept for choosing against the neighbours."""
        said = close_piece(seg, lang)
        takes: list[_Take] = []
        misses: list = []
        k = 0
        while k < params.takes + 2:
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            t = self._take(b, seg, said, voice, lang, params, seed + k * 7919, speed, ctx, verifier, state)
            takes.append(t)
            k += 1
            self._note_miss(t, state, misses)
            if k >= params.takes and any(x.dur_ok and not x.misread for x in takes):
                break
        takes.sort(key=lambda x: x.pen + (1.0 if x.misread else 0.0))
        return takes[:keep], k

    @staticmethod
    def _similarities(voice: VoiceSel, options: list[list["_Take"]], sr: int,
                      ctx: dict) -> list[list[Optional[float]]]:
        """Each take's speaker similarity to the voice's reference recording;
        None throughout when there is no reference or no model (offline)."""
        none = [[None] * len(takes) for takes in options]
        ref = _reference_audio(voice)
        if ref is None:
            return none
        from thundertalk.core import speaker_check
        if not speaker_check.ensure():
            ctx["speaker_check"] = "unavailable"
            return none
        try:
            r = speaker_check.embed(*ref)
            return [[speaker_check.similarity(speaker_check.embed(t.audio, sr), r) for t in takes]
                    for takes in options]
        except Exception:
            ctx["speaker_check"] = "failed"
            return none

    def _synthesize_polished(self, b: TtsBackend, text: str, voice: VoiceSel, lang: str, speed: float,
                             params: TtsParams, base_seed: int, progress: Optional[ProgressCB],
                             cancel: Optional[threading.Event], verifier: Optional[Verifier]
                             ) -> tuple[list[np.ndarray], list[float], list[SegmentReport], dict]:
        """One piece per sentence, several takes each, then one take per
        sentence chosen for the whole text: read correctly first, then in
        step with its neighbours' pitch."""
        from thundertalk.core.priority import DICTATION
        sr = b.sample_rate
        plan = plan_sentences(text, lang, b.chunk_scale(voice, lang, 1.0))
        ctx: dict = {}
        state = {"off": False}
        options: list[list[_Take]] = []
        used: list[int] = []
        for idx, (seg, _pause) in enumerate(plan):
            if not b.preemptible:            # a preemptible engine steps aside inside the sentence
                DICTATION.wait_clear(cancel)
            if cancel is not None and cancel.is_set():
                raise TtsCancelled()
            if progress:
                progress(idx, len(plan), seg)
            takes, n = self._takes(b, seg, voice, lang, params, base_seed + idx * 1009, speed, ctx, verifier,
                                   state, cancel)
            options.append(takes)
            used.append(n)
            if progress:
                progress(idx + 1, len(plan), seg)
        pitches = [[median_semitones(t.audio, sr) for t in takes] for takes in options]
        sims = self._similarities(voice, options, sr, ctx)
        costs = [[t.pen + (1.0 if t.misread else 0.0) + _similarity_cost(s, row) for t, s in zip(takes, row)]
                 for takes, row in zip(options, sims)]
        # Across a paragraph break a change of register is natural: half the weight.
        weights = [POLISH_PITCH_WEIGHT * (0.5 if pause >= PAUSE_BREAK else 1.0) for _, pause in plan[:-1]]
        chosen = pick_takes(costs, pitches, weights)
        pieces, reports = [], []
        for (seg, _), takes, k, n, pitch, row in zip(plan, options, chosen, used, pitches, sims):
            audio, rep = self._report(seg, lang, sr, takes[k], n, params, state, pitch[k], row[k])
            pieces.append(audio)
            reports.append(rep)
        return pieces, [p for _, p in plan], reports, ctx

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
        if (isinstance(voice, str) and not getattr(b, "open_voices", False)
                and voice not in {v.id for v in b.voices()}):
            raise ValueError(f"Unknown voice: {voice}")
        params = params or TtsParams()
        lang = language if language and language != "auto" else detect_language(text)
        base_seed = seed if seed is not None else int(time.time()) & 0x7FFFFFFF
        sr = b.sample_rate
        t0 = time.monotonic()
        if params.takes > 1 and getattr(b, "stochastic", True):
            raw, pauses, reports, ctx = self._synthesize_polished(b, text, voice, lang, speed, params, base_seed,
                                                                  progress, cancel, verifier)
            return self._finish([fade(p, sr=sr) for p in raw], pauses, reports, ctx, sr, speed, lang, t0)
        plan = plan_segments(text, lang, b.chunk_scale(voice, lang, params.chunk_scale))
        ctx: dict = {}
        reports: list[SegmentReport] = []
        pieces: list[np.ndarray] = []
        pauses: list[float] = []
        state = {"off": False}
        for idx, (seg, pause) in enumerate(plan):
            from thundertalk.core.priority import DICTATION
            if not b.preemptible:            # a preemptible engine steps aside inside the sentence
                DICTATION.wait_clear(cancel)
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
        return self._finish(pieces, pauses, reports, ctx, sr, speed, lang, t0)

    @staticmethod
    def _finish(pieces: list[np.ndarray], pauses: list[float], reports: list[SegmentReport], ctx: dict, sr: int,
                speed: float, lang: str, t0: float) -> SynthResult:
        # Pieces are matched by loudness, and the whole clip leaves at one loudness
        # (-16 LUFS): two clips for the same video sound equally loud whatever the
        # voice. It used to be peak-normalised, which follows a clip's sharpest
        # consonant instead of how loud it sounds.
        from thundertalk.core import loudness
        leveled = [p * g for p, g in zip(pieces, loudness.match_gains(pieces, sr))]
        mix = assemble(leveled, pauses[:-1], sr)
        if abs(speed - 1.0) >= 0.02 and not ctx.get("native_speed"):
            mix = time_stretch(mix, speed, sr)
        mix, _ = loudness.normalize(mix, sr)
        notes = (["Voice check skipped: the speaker model could not be fetched (offline?)"]
                 if ctx.get("speaker_check") else [])
        return SynthResult(mix.astype(np.float32), sr, lang, reports, time.monotonic() - t0, notes,
                           SIM_FLOOR if any(r.similarity is not None for r in reports) else None)


def _similarity_cost(sim: Optional[float], row: list[Optional[float]]) -> float:
    """What a take's distance from the voice costs when choosing among a sentence's takes."""
    if sim is None:
        return 0.0
    best = max(s for s in row if s is not None)
    cost = SIM_WEIGHT * max(0.0, best - sim - SIM_SLACK)
    return cost + (1.0 if sim < SIM_FLOOR else 0.0)


def _reference_audio(voice: VoiceSel) -> Optional[tuple[np.ndarray, int]]:
    """The recording a voice is cloned from: the user's own, or a built-in voice's shipped clip."""
    from thundertalk.core.tts import SR as CLONE_SR
    if isinstance(voice, ClonePrompt):
        return np.asarray(voice.audio, dtype=np.float32).reshape(-1), CLONE_SR
    from thundertalk.core.tts_backends.presets import load_presets
    slug = voice.split(":", 1)[1] if ":" in voice else ""
    clip = next((p for p in load_presets() if p.slug == slug), None)
    if clip is None:
        return None
    from thundertalk.core import audio_io
    return np.asarray(audio_io.decode_audio(str(clip.wav), CLONE_SR), dtype=np.float32), CLONE_SR


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
