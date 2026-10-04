"""Short real-model Studio/link regression check; run under the machine GPU lock.

HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=$PWD python tools/check_threaded_studio.py
Uses public audio only and never writes app settings or permission entries.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import threading
from contextlib import nullcontext
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt

from thundertalk.core import asr, audio_io, diarize, links, models, speech
from thundertalk.core.priority import DICTATION
from thundertalk.ui.studio.workers import PreloadWorker, TranscribeWorker


def run_worker(worker):
    outcome = {}
    worker.done.connect(lambda result: outcome.update(result=result), Qt.ConnectionType.DirectConnection)
    worker.error.connect(lambda error: outcome.update(error=error), Qt.ConnectionType.DirectConnection)
    worker.start()
    if not worker.wait(120_000):
        worker.cancel()
        worker.wait()
        raise RuntimeError("Studio worker timed out")
    if "error" in outcome:
        raise RuntimeError(outcome["error"])
    return outcome["result"]


def check_stream_handoff(shared=True):
    import mlx.core as mx
    import numpy as np
    from thundertalk.core.gpu_lock import GPU_LOCK
    from thundertalk.core.mlx_runtime import mlx_context

    created, consumed = threading.Event(), threading.Event()
    cached = {}

    def produce():
        with GPU_LOCK, mlx_context() if shared else nullcontext():
            with mx.stream(mx.cpu):
                cached["array"] = mx.arange(8).astype(mx.float64) * 2
        created.set()
        if not consumed.wait(10):
            raise RuntimeError("CPU stream consumer timed out")

    def consume():
        if not created.wait(10):
            raise RuntimeError("CPU stream producer timed out")
        try:
            with GPU_LOCK, mlx_context() if shared else nullcontext():
                np.testing.assert_array_equal(np.asarray(cached["array"]), np.arange(8) * 2)
        finally:
            consumed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        producer = pool.submit(produce)
        pool.submit(consume).result()
        producer.result()
    print(json.dumps({"lazy_cpu_handoff": "passed", "shared_streams": shared}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://www.bilibili.com/video/av385356630")
    parser.add_argument("--case", choices=("all", "dictation", "picker", "moss"), default="all")
    parser.add_argument("--preload", action="store_true")
    parser.add_argument("--streams-only", action="store_true", help="Only check lazy CPU graph handoff")
    parser.add_argument("--baseline-streams", action="store_true", help="Reproduce the old native abort in an isolated process")
    args = parser.parse_args()
    if args.streams_only:
        check_stream_handoff(shared=not args.baseline_streams)
        return
    engine = asr.AsrEngine()
    t0 = time.monotonic()
    engine.load_model(models.get_model_path("qwen3-asr-06b-mlx"), "Qwen3-ASR", "mlx")
    print(json.dumps({"load_s": round(time.monotonic() - t0, 3)}), flush=True)
    fetched = links.fetch_audio(args.url)
    try:
        samples = audio_io.decode_audio(fetched.path, 16000)[: 5 * 16000]
        cases = ("dictation", "picker", "moss") if args.case == "all" else (args.case,)
        for case in cases:
            started = threading.Event()
            phases = []

            def studio():
                worker = TranscribeWorker("", engine, case == "moss", url=args.url,
                    model_id={"dictation": "qwen3-asr-06b-mlx", "picker": "qwen3-asr-17b-mlx",
                              "moss": "moss-transcribe-diarize-mlx"}[case])
                def progress(p, message):
                    phases.append(message)
                    if message == "diarize" or re.fullmatch(r"\d+/\d+", message):
                        started.set()
                worker.progress.connect(progress, Qt.ConnectionType.DirectConnection)
                return run_worker(worker)

            def dictate():
                if not started.wait(30):
                    raise RuntimeError("Studio did not reach inference within 30 seconds")
                time.sleep(0.3)
                DICTATION.begin()
                try:
                    return engine.recognize(samples, preview=True)
                finally:
                    DICTATION.end()

            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=3) as pool:
                job = pool.submit(studio)
                recognition = pool.submit(dictate)
                preload = pool.submit(run_worker, PreloadWorker("voxcpm2", "voxcpm2:warm-female-zh")) if args.preload else None
                failures = []
                for name, future in (("studio", job), ("dictation", recognition), ("preload", preload)):
                    if future is not None:
                        try:
                            future.result()
                        except Exception as exc:
                            failures.append(exc)
                            print(json.dumps({"case": case, "failed_job": name, "error": str(exc)}), flush=True)
                if failures:
                    raise failures[0]
                tr, r = job.result(), recognition.result()
            print(json.dumps({"case": case, "elapsed_s": round(time.monotonic() - t0, 3),
                "duration_s": tr.duration, "model_id": tr.model_id, "segments": len(tr.segments),
                "speakers": tr.has_speakers, "chars": sum(len(s.text) for s in tr.segments), "dictation_chars": len(r.text),
                "dictation_ms": r.inference_ms, "yield_count": phases.count("yield")}, ensure_ascii=False), flush=True)
            diarize.unload_model()
            speech.get_engine().unload()
    finally:
        shutil.rmtree(fetched.workdir, ignore_errors=True)
        engine.unload()
        diarize.unload_model()
        speech.get_engine().unload()


if __name__ == "__main__":
    main()
