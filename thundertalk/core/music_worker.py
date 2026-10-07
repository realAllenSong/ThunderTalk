"""Studio ▸ Compose child process: one job (load → every take → exit).

Spawned by ``core/music_process.py``; never imported with torch in the parent.
The vendored ACE-Step code (``third_party/acestep``) runs here and reports
through ``acestep._hooks``. Every event is a tuple sent over the pipe:

    ("phase", "load" | "plan" | "compose" | "render" | "write", take)
    ("tokens", done, total)   ("step", done, total)   ("paused", "dictation")
    ("take", index, wav_path, meta)   ("done",)   ("cancelled",)
    ("error", code, detail)
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SAMPLE_RATE = 48000
MLX_CACHE_LIMIT = 1 << 30           # Phase 0: an uncapped MLX buffer cache adds 13–17 GB during decode
MIN_TIMEOUT_S = 1800
COMMENT = "AI-generated with ACE-Step 1.5 in ThunderTalk"
PLANNER_FOLDER = "acestep-5Hz-lm-1.7B"


class MusicCancelled(BaseException):
    """Raised from a hook. A BaseException so upstream's ``except Exception``
    wrappers (which turn failures into a result object) let it through."""


@dataclass
class MusicRequest:
    project_root: str                   # ~/.thundertalk/models/ace-step (contains checkpoints/)
    model_folder: str                   # e.g. "acestep-v15-turbo"
    job_dir: str
    caption: str
    lyrics: str = ""
    instrumental: bool = False
    vocal_language: str = "unknown"
    duration: int = 30
    bpm: Optional[int] = None
    steps: int = 8
    seeds: list[int] = field(default_factory=lambda: [42])
    planner: bool = False
    dit_backend: str = "mlx"            # "mlx" | "mps"
    estimate_s: float = 0.0             # per take, for the upstream timeout


def take_seeds(seed: Optional[int], takes: int) -> list[int]:
    """``seed + i`` per take, or one random seed per take when no seed is given."""
    if seed is None:
        return [random.randint(0, 2**32 - 1) for _ in range(takes)]
    return [(seed + i) % 2**32 for i in range(takes)]


def timeout_s(estimate_s: float) -> int:
    return int(max(MIN_TIMEOUT_S, 3 * estimate_s))


def third_party_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "third_party"


def make_gate(send, go, cancel):
    """A hook body: wait while ``go`` is clear (dictation or GPU busy), raise on cancel."""
    def gate():
        if cancel.is_set():
            raise MusicCancelled
        if go.is_set():
            return
        send(("paused", "dictation"))
        while not go.wait(0.1):
            if cancel.is_set():
                raise MusicCancelled
        if cancel.is_set():
            raise MusicCancelled
    return gate


def write_wav(path: Path, samples) -> None:
    """``samples`` is [frames, 2] float; written as 16-bit PCM with the AI disclosure."""
    import soundfile as sf
    with sf.SoundFile(str(path), "w", samplerate=SAMPLE_RATE, channels=samples.shape[1],
                      subtype="PCM_16", format="WAV") as f:
        # libsndfile only stores WAV INFO strings set before the first frame.
        f.comment = COMMENT
        f.software = "ThunderTalk"
        f.write(samples)


def _params(request: MusicRequest, seed: int):
    from acestep.inference import GenerationParams
    return GenerationParams(
        caption=request.caption,
        lyrics="[Instrumental]" if request.instrumental else request.lyrics,
        instrumental=request.instrumental,
        vocal_language="unknown" if request.instrumental else request.vocal_language,
        duration=request.duration, bpm=request.bpm, inference_steps=request.steps, seed=seed,
        thinking=request.planner, use_cot_metas=request.planner,
        use_cot_caption=False, use_cot_language=False, dcw_enabled=False)


def _save_take(request: MusicRequest, index: int, seed: int, audio: dict, result, wall_s: float):
    import numpy as np
    job = Path(request.job_dir)
    name = f"take-{index + 1}"
    samples = audio["tensor"].detach().cpu().float().numpy().T
    if samples.ndim != 2 or not np.isfinite(samples).all():
        raise RuntimeError("invalid audio from the decoder")
    if audio["sample_rate"] != SAMPLE_RATE:
        raise RuntimeError(f"unexpected sample rate {audio['sample_rate']}")
    wav = job / f"{name}.wav"
    write_wav(wav, np.clip(samples, -1.0, 1.0))
    latents_file = ""
    latents = result.extra_outputs.get("pred_latents")
    if latents is not None:
        # Upstream's repaint cache expects [T, C] float32 beside the sidecar.
        latents_file = f"{name}.latents.npy"
        np.save(job / latents_file, latents[0].detach().cpu().float().numpy())
    meta = {
        "seed": seed,
        "repaint_source_latents_file": latents_file,
        "model": request.model_folder,
        "caption": request.caption,
        "lyrics": "" if request.instrumental else request.lyrics,
        "instrumental": request.instrumental,
        "vocal_language": request.vocal_language,
        "duration": len(samples) / SAMPLE_RATE,
        "bpm": request.bpm,
        "steps": request.steps,
        "planner": request.planner,
        "dit_backend": request.dit_backend,
        "lm_metadata": _jsonable(result.extra_outputs.get("lm_metadata")),
        "time_costs": _jsonable(result.extra_outputs.get("time_costs")),
        "wall_seconds": round(wall_s, 2),
        "comment": COMMENT,
    }
    (job / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return wav, meta


def _jsonable(value):
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def _load(request: MusicRequest, send, gate):
    import torch
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

    handler = AceStepHandler()
    status, ok = handler.initialize_service(
        project_root=request.project_root, config_path=request.model_folder, device="mps",
        dtype=torch.bfloat16, use_mlx_dit=request.dit_backend == "mlx", compile_model=False)
    if not ok:
        raise _LoadError(status)
    decode = handler._decode_generate_music_pred_latents

    def gated_decode(*args, **kwargs):
        gate()
        send(("phase", "render", None))
        return decode(*args, **kwargs)

    handler._decode_generate_music_pred_latents = gated_decode
    lm = LLMHandler()
    if request.planner:
        status, ok = lm.initialize(str(Path(request.project_root) / "checkpoints"), PLANNER_FOLDER,
                                   backend="mlx", device="mps")
        if not ok:
            raise _LoadError(status)
    return handler, lm


class _LoadError(RuntimeError):
    pass


def _run(request: MusicRequest, send, go, cancel) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["ACESTEP_PROJECT_ROOT"] = request.project_root
    from thundertalk.core import runtime
    runtime.activate("music")
    tp = str(third_party_dir())
    if tp not in sys.path:
        sys.path.insert(0, tp)
    import mlx.core as mx
    mx.set_cache_limit(MLX_CACHE_LIMIT)
    # Upstream's repaint lookup globs os.getcwd()/gradio_outputs.
    os.chdir(request.job_dir)
    send(("phase", "load", None))
    gate = make_gate(send, go, cancel)
    from acestep import _hooks
    # generate_music_execute copies TIMEOUT_S at import time: set it before the handler import.
    _hooks.TIMEOUT_S = timeout_s(request.estimate_s)

    def on_step(done, total):
        gate()
        send(("step", done, total))

    def on_tokens(done, total):
        gate()
        send(("tokens", done, total))

    _hooks.on_step = on_step
    _hooks.on_tokens = on_tokens
    handler, lm = _load(request, send, gate)
    from acestep.inference import GenerationConfig, generate_music
    for index, seed in enumerate(request.seeds):
        gate()
        send(("phase", "plan" if request.planner else "compose", index))
        start = time.monotonic()
        result = generate_music(handler, lm, _params(request, seed),
                                GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed],
                                                 audio_format="wav"))
        if not result.success or not result.audios:
            raise RuntimeError(result.error or result.status_message)
        send(("phase", "write", index))
        wav, meta = _save_take(request, index, seed, result.audios[0], result, time.monotonic() - start)
        send(("take", index, str(wav), meta))
        del result


class _ParentGone(BaseException):
    pass


def _serve(connection, request: MusicRequest, go, cancel) -> None:
    def send(event):
        try:
            connection.send(event)
        except (BrokenPipeError, EOFError, OSError) as exc:
            raise _ParentGone from exc

    try:
        try:
            _run(request, send, go, cancel)
            send(("done",))
        except MusicCancelled:
            send(("cancelled",))
        except _LoadError as exc:
            send(("error", "music:load_failed", str(exc)))
        except (_ParentGone, KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            send(("error", "music:failed", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"))
    except _ParentGone:
        pass
    finally:
        connection.close()
