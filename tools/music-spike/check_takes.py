"""Phase 4 calibration: run the lyric check on real takes with the app's Qwen3-ASR.

Usage (app venv, under lock.py gpu): check_takes.py DIR [DIR ...]
Prints per line: status, error, shown text, what the recogniser heard; then a
JSON summary line per take (for docs/verification).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from thundertalk.core import music_check
from thundertalk.core.asr import AsrEngine
from thundertalk.core.models import get_model_path, is_downloaded

MODEL_ID = "qwen3-asr-06b-int8"


def main(dirs: list[str]) -> int:
    if not is_downloaded(MODEL_ID):
        print(f"{MODEL_ID} not installed")
        return 2
    asr = AsrEngine()
    t0 = time.perf_counter()
    asr.load_model(get_model_path(MODEL_ID), "Qwen3-ASR", "onnx")
    print(f"loaded {MODEL_ID} in {time.perf_counter() - t0:.1f} s")
    try:
        for d in dirs:
            for wav in sorted(Path(d).glob("take-*.wav")):
                meta = json.loads(music_check.sidecar_path(wav).read_text(encoding="utf-8"))
                asr.set_language(meta.get("vocal_language") or "auto")
                t1 = time.perf_counter()
                result = music_check.check_take(wav, lambda a: asr.recognize(a, 16000).text, meta)
                took = time.perf_counter() - t1
                if result is None:
                    print(f"\n{wav}: no lyrics")
                    continue
                print(f"\n{wav}  timed={result.timed}  windows={len(result.windows)}  {took:.1f} s")
                for line, c in zip(result.lines, result.checks):
                    err = "  -  " if c.error is None else f"{c.error:.2f}"
                    print(f"  {c.status:9} {err}  {line.text}  | heard: {c.heard}")
                clear, total = result.summary
                print(json.dumps({"take": str(wav), "timed": result.timed, "clear": clear, "total": total,
                                  "errors": [c.error for c in result.checks], "check_s": round(took, 1)},
                                 ensure_ascii=False))
    finally:
        asr.unload()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
