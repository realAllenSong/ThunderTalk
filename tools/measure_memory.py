"""Short, offline, in-process memory replay. Run under the machine GPU lock.

Uses shipped synthetic speech, never a user's recordings. JSON includes native
physical footprint (including compressed pages), RSS, MLX active/cache/peak,
job latency and raw `footprint` category reports. Idle checkpoints are real
elapsed seconds; --idle 0,60,300 is the default. Notes preparation is local:
provider/server memory is outside this process and requires a separate check.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def physical_footprint(pid=None) -> int:
    # rusage_info_v2: UUID followed by user/system/wakeups/pageins,
    # wired_size, resident_size, phys_footprint (eighth uint64).
    buf = ctypes.create_string_buffer(1024)
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    if lib.proc_pid_rusage(pid or os.getpid(), 2, ctypes.byref(buf)):
        raise OSError("proc_pid_rusage failed")
    return ctypes.c_uint64.from_buffer(buf, 72).value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--idle", default="0,60,300")
    parser.add_argument("--tts", default="voxcpm2", choices=("voxcpm2", "kokoro", "indextts"))
    parser.add_argument("--studio-model", default="moss-transcribe-diarize-mlx")
    parser.add_argument("--skip-translate", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--threaded", action="store_true", help="Use a fresh worker for each job/reload")
    parser.add_argument("--cache-mb", type=int, help="Override cache for speed comparisons")
    parser.add_argument("--timeout", type=float, help="Accelerate the real policy for short reload checks")
    parser.add_argument("--relieve-allocator", action="store_true", help="Measure macOS malloc pressure relief")
    parser.add_argument("--in-process-translate", action="store_true", help="Compare the old native heap lifetime")
    args = parser.parse_args()
    if args.timeout is not None:
        from thundertalk.core.memory_policy import POLICY
        POLICY.timeout = args.timeout
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import psutil
    from PySide6.QtWidgets import QApplication
    from thundertalk.core import audio_io, diarize, meeting_notes, models, speech, transcribe
    from thundertalk.core.asr import AsrEngine
    from thundertalk.core.translate import TranslationEngine
    from thundertalk.core.translate_process import TranslationProcess
    from thundertalk.ui.pages.studio_page import StudioPage
    from thundertalk.ui.studio import speak_tab
    # Drive preload explicitly on this thread, using the same speech path.
    speak_tab.AUTO_PRELOAD = False
    app = QApplication.instance() or QApplication([])
    page = StudioPage()
    engine = AsrEngine()
    translator = TranslationEngine() if args.in_process_translate else TranslationProcess()
    rows = []

    def checkpoint(label, elapsed=None, error=None):
        app.processEvents()
        mx = sys.modules.get("mlx.core")
        child = getattr(translator, "pid", None)
        child_memory_by_pid = {}
        for process in psutil.Process().children(recursive=True):
            try:
                child_memory_by_pid[process.pid] = physical_footprint(process.pid)
            except OSError:
                pass  # an idle worker can exit between enumeration and sampling
        parent_memory = physical_footprint()
        child_memory = sum(child_memory_by_pid.values())
        row = {"step": label, "phys_footprint": parent_memory + child_memory,
               "parent_phys_footprint": parent_memory, "worker_phys_footprint": child_memory,
               "child_phys_footprints": child_memory_by_pid,
               "rss": psutil.Process().memory_info().rss,
               "mlx_active": mx.get_active_memory() if mx else 0,
               "mlx_cache": mx.get_cache_memory() if mx else 0,
               "mlx_peak": mx.get_peak_memory() if mx else 0,
               "seconds": elapsed, "error": error,
               "dictation_loaded": engine.is_loaded,
               "tts_loaded": sorted(speech.get_engine()._loaded),
               "moss_loaded": diarize._MODEL is not None,
               "translate_loaded": translator.is_loaded}
        raw = subprocess.run(["/usr/bin/footprint", "-p", str(os.getpid()), "-f", "bytes"],
                             capture_output=True, text=True, timeout=30)
        row["footprint_report"] = raw.stdout or raw.stderr
        if child:
            worker_raw = subprocess.run(["/usr/bin/footprint", "-p", str(child), "-f", "bytes"],
                                        capture_output=True, text=True, timeout=30)
            row["worker_footprint_report"] = worker_raw.stdout or worker_raw.stderr
        rows.append(row)
        args.output.write_text(json.dumps(rows, indent=2))
        print(json.dumps({k: v for k, v in row.items() if not k.endswith("footprint_report")}), flush=True)

    def step(label, fn):
        start = time.monotonic()
        try:
            if args.threaded:
                result, errors = [], []
                def work():
                    try:
                        result.append(fn())
                    except Exception as exc:
                        errors.append(exc)
                worker = threading.Thread(target=work)
                worker.start()
                while worker.is_alive():
                    app.processEvents()
                    worker.join(0.05)
                if errors:
                    raise errors[0]
                value = result[0]
            else:
                value = fn()
        except Exception as exc:
            checkpoint(label, time.monotonic() - start, f"{type(exc).__name__}: {exc}")
            return None
        checkpoint(label, time.monotonic() - start)
        return value

    try:
        checkpoint("launch UI")
        step("dictation load", lambda: engine.load_model(models.get_model_path("qwen3-asr-06b-int8"),
                                                        "Qwen3-ASR", "onnx"))
        page.set_engine(engine)
        audio = Path(__file__).resolve().parents[1] / "assets/voices/male-en.wav"
        samples = audio_io.decode_audio(str(audio), 16000)[:6 * 16000].copy()
        for i in range(3):
            step(f"dictation {i + 1}", lambda: engine.recognize(samples, 16000))
        if args.cache_mb is not None:
            from thundertalk.core import memory_policy
            memory_policy.MLX_CACHE_BYTES = args.cache_mb << 20
        voice = {"voxcpm2": "voxcpm2:male-en", "indextts": "indextts:male-en", "kokoro": "kokoro:3"}[args.tts]
        step("Speak preload", lambda: speech.get_engine().preload(args.tts, voice, "english"))
        step("Speak synth", lambda: speech.get_engine().synthesize("Hello, this is a short memory test.",
                                                                  voice, language="english", seed=1))
        step("Studio shared ASR", lambda: transcribe.transcribe_file(str(audio), engine,
                                                                    model_id="qwen3-asr-06b-int8"))
        transcript = step("Studio alternate ASR", lambda: transcribe.transcribe_file(
            str(audio), engine, speakers=args.studio_model == diarize.MODEL_ID, model_id=args.studio_model))
        if not args.skip_translate:
            step("translate load", lambda: translator.load_model(models.get_model_path("seamless-m4t-v2-large")))
            step("translate text", lambda: translator.translate_text("Hello, this is a short memory test.", "eng", "spa"))
        if transcript:
            step("meeting notes preparation (no provider)", lambda: meeting_notes.transcript_chunks(transcript))
        start = time.monotonic()
        for target in sorted(int(x) for x in args.idle.split(",")):
            while time.monotonic() - start < target:
                app.processEvents()
                if args.timeout is not None:
                    # Exercise the same sweep without waiting for its 15 s tick.
                    POLICY.sweep()
                time.sleep(0.1)
            checkpoint(f"idle {target}s")
        if args.relieve_allocator:
            def relieve():
                fn = ctypes.CDLL("/usr/lib/libSystem.B.dylib").malloc_zone_pressure_relief
                fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
                fn.restype = ctypes.c_size_t
                print(f"malloc pressure relief freed {fn(None, 0)} bytes", flush=True)
            step("allocator pressure relief", relieve)
        if args.reload:
            step("dictation immediately after idle", lambda: engine.recognize(samples, 16000))
            step("TTS after idle", lambda: speech.get_engine().synthesize("Hello, this is a short memory test.",
                                                                         voice, language="english", seed=1))
            step("Studio after idle", lambda: transcribe.transcribe_file(
                str(audio), engine, speakers=args.studio_model == diarize.MODEL_ID, model_id=args.studio_model))
            if not args.skip_translate:
                step("translation after idle", lambda: translator.translate_text(
                    "Hello, this is a short memory test.", "eng", "spa"))
            step("dictation after idle", lambda: engine.recognize(samples, 16000))
    finally:
        page.shutdown()
        speech.get_engine().unload()
        engine.unload()
        diarize.unload_model()
        translator.unload()


if __name__ == "__main__":
    main()
