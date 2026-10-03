#!/usr/bin/env python3
"""Replay local dictation: full recognition, live windows, conservative merge.

Run under the machine-wide GPU lock, even for ONNX, e.g.:
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=$PWD \
    python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- \
    /Users/songallen/Desktop/ThunderTalk/.venv/bin/python \
    tools/replay_dictation.py ~/.thundertalk/recordings/NAME.wav

Sidecar settings are reused; --model/--language/--hotword override them.
Simulation uses LivePreview's commit, guard and adaptive-window logic. Each
snapshot advances 1.2 audio seconds; it intentionally bypasses wall-clock
scheduling/slow-model shutdown so every window can be inspected. No uploads.
"""

import argparse
import json
from pathlib import Path
import time

from PySide6.QtCore import QCoreApplication

from thundertalk.core import models
from thundertalk.core.asr import AsrEngine
from thundertalk.core.audio_io import decode_audio
from thundertalk.core.live_preview import LivePreview
from thundertalk.core.text_merge import merge_preview_terms


def replay(engine, audio, *, step=1.2):
    n = 0
    preview = LivePreview(lambda: audio[:n], lambda s: engine.recognize(s, preview=True).text)
    preview.start()
    preview._timer.stop()
    windows = []
    t0 = time.perf_counter()
    for n in range(int(step * 16000), len(audio) + int(step * 16000), int(step * 16000)):
        n = min(n, len(audio))
        text = preview._decode(preview._gen)
        if text is not None:
            preview._on_result(preview._gen, text)
            windows.append({"audio_seconds": round(n / 16000, 2), "text": text})
    clean = preview.last_clean_text()
    preview.stop()
    preview_seconds = time.perf_counter() - t0
    t0 = time.perf_counter()
    final = engine.recognize(audio).text
    final_seconds = time.perf_counter() - t0
    return {"duration": len(audio) / 16000, "full": final, "chunked": clean,
            "merged": merge_preview_terms(final, clean), "loop_detected": preview.loop_detected,
            "preview_seconds": round(preview_seconds, 3),
            "final_seconds": round(final_seconds, 3), "windows": windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--hotword", action="append")
    parser.add_argument("--step", type=float, default=1.2)
    parser.add_argument("--limit-seconds", type=float, help="Replay only the beginning of a long recording")
    parser.add_argument("--output", type=Path, help="Save replay JSON locally")
    args = parser.parse_args()
    if args.step < 0.6:
        parser.error("--step must be at least 0.6 seconds")
    if args.limit_seconds is not None and args.limit_seconds < 0.6:
        parser.error("--limit-seconds must be at least 0.6 seconds")
    sidecar = args.recording.with_suffix(".json")
    metadata = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    model_id = args.model or metadata.get("model") or "qwen3-asr-06b-int8"
    info = next((m for m in models.BUILTIN_MODELS if m.id == model_id), None)
    if info is None:
        parser.error(f"Unknown model ID: {model_id}; specify --model")
    path = models.get_model_path(model_id)
    if not path:
        parser.error(f"Model not downloaded: {model_id}")
    app = QCoreApplication.instance() or QCoreApplication([])
    engine = AsrEngine()
    engine.set_hotwords(args.hotword if args.hotword is not None else metadata.get("hotwords", []))
    engine.set_language(args.language or metadata.get("language", "auto"))
    try:
        engine.load_model(path, info.family, info.backend)
        audio = decode_audio(str(args.recording), 16000)
        if args.limit_seconds is not None:
            audio = audio[:int(args.limit_seconds * 16000)]
        result = replay(engine, audio, step=args.step)
        result.update(model=model_id, language=args.language or metadata.get("language", "auto"))
        output = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
    finally:
        engine.unload()
    del app


if __name__ == "__main__":
    main()
