#!/usr/bin/env python3
"""Light concurrent picker/dictation check with existing synthetic WAVs.

Run through lock.py gpu with PYTHONPATH=$PWD and HF_HUB_OFFLINE=1,
TRANSFORMERS_OFFLINE=1. Only loads the CPU dictation model and one MLX
Studio model, once. Clips and results stay local; all models exit afterwards.
"""

import argparse
import json
from pathlib import Path
import resource
import sys
import tempfile
import threading
import time

import psutil

from thundertalk.core import audio_io, models, transcribe
from thundertalk.core.asr import AsrEngine
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.priority import DICTATION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio", type=Path, help="Existing synthetic speech WAV (at least 60 s)")
    parser.add_argument("dictation", type=Path, help="Existing synthetic speech WAV (at least 8 s)")
    parser.add_argument("--dictation-seconds", type=float, default=8,
                        help="Dictation excerpt length; 9.2 keeps the complete synthetic test sentence")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    info = next(m for m in models.BUILTIN_MODELS if m.id == "qwen3-asr-06b-mlx")
    if not transcribe._has_model_headroom(info):
        raise RuntimeError("Not enough memory for the two-model check; no models loaded")
    for mid in (info.id, "qwen3-asr-06b-int8"):
        if not models.is_downloaded(mid):
            raise RuntimeError(f"Model is not downloaded: {mid}")
    studio_audio = audio_io.decode_audio(str(args.studio), transcribe.SR)[:60 * transcribe.SR]
    dictation_audio = audio_io.decode_audio(str(args.dictation), transcribe.SR)[:int(args.dictation_seconds * transcribe.SR)]
    if len(studio_audio) < 60 * transcribe.SR or len(dictation_audio) < 8 * transcribe.SR:
        raise ValueError("Synthetic inputs are shorter than the check requires")
    engine = AsrEngine()
    result = {"studio_audio_s": 60, "dictation_audio_s": len(dictation_audio) / transcribe.SR}
    events, failures = [], []
    span_started, finished = threading.Event(), threading.Event()
    cancel = threading.Event()
    worker = None
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    sampling_done = threading.Event()

    def sample_memory():
        nonlocal peak_rss
        while not sampling_done.wait(0.05):
            peak_rss = max(peak_rss, process.memory_info().rss)

    sampler = threading.Thread(target=sample_memory, name="memory-sampler")
    sampler.start()
    cache_dir = Path.cwd() / ".thundertalk"
    cache_dir.mkdir(exist_ok=True)
    try:
        t0 = time.perf_counter()
        engine.load_model(models.get_model_path("qwen3-asr-06b-int8"), "Qwen3-ASR", "onnx", memory_mode="low")
        result["dictation_load_s"] = time.perf_counter() - t0
        # A fresh memory check inside selected_engine also runs after this load.
        with tempfile.TemporaryDirectory(dir=cache_dir) as tmp:
            clip = Path(tmp) / "studio.wav"
            audio_io.write_wav(str(clip), studio_audio, transcribe.SR)

            def progress(pct, message):
                events.append(message)
                if "/" in message:
                    span_started.set()

            def studio_job():
                started = time.perf_counter()
                try:
                    tr = transcribe.transcribe_file(str(clip), engine, model_id=info.id,
                                                   progress=progress, cancel=cancel)
                    result.update(studio_text=tr.to_text(), studio_model=tr.model_id,
                                  studio_spans=len(tr.segments))
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    result["studio_wall_s"] = time.perf_counter() - started
                    finished.set()

            worker = threading.Thread(target=studio_job, name="studio-check")
            worker.start()
            deadline = time.monotonic() + 90
            while not span_started.wait(0.1):
                if finished.is_set() or time.monotonic() > deadline:
                    raise RuntimeError(f"Studio did not start a span: {failures}")
            if any(m.endswith("_fallback") for m in events):
                raise RuntimeError("Memory guard fell back; cannot claim alternate-model concurrency")
            deadline = time.monotonic() + 5
            while True:
                acquired = GPU_LOCK.acquire(blocking=False)
                if acquired:
                    GPU_LOCK.release()
                else:
                    break
                if finished.is_set() or time.monotonic() > deadline:
                    raise RuntimeError("No in-flight Studio GPU span observed")
                time.sleep(0.01)
            DICTATION.begin()
            try:
                t0 = time.perf_counter()
                decoded = engine.recognize(dictation_audio)
                result.update(dictation_text=decoded.text, dictation_latency_s=time.perf_counter() - t0,
                              dictation_model=decoded.model, dictation_backend=decoded.backend,
                              dictation_completed_during_studio=not finished.is_set())
            finally:
                DICTATION.end()
            worker.join(90)
            if worker.is_alive() or failures:
                raise RuntimeError(f"Studio failed or timed out: {failures}")
            assert result["studio_model"] == info.id
            assert result["dictation_model"] == "qwen3-asr-06b-int8"
            assert result["dictation_completed_during_studio"]
            assert engine.current_model == "qwen3-asr-06b-int8" and engine.is_loaded
    finally:
        cancel.set()
        if worker is not None and worker.is_alive():
            worker.join()  # finish the current bounded span before unloading
        engine.unload()
        sampling_done.set()
        sampler.join()
    result.update(sampled_peak_rss_gib=peak_rss / 1024**3,
                  peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**3,
                  studio_yields=events.count("yield"))  # macOS ru_maxrss is bytes
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        result["peak_metal_gib"] = mx.get_peak_memory() / 1024**3
    output = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.write_text(output + "\n", encoding="utf-8")
    print(output, flush=True)


if __name__ == "__main__":
    main()
