"""Short real CPU checks; run through lock.py gpu with an isolated HOME.

Reuses the benchmark cache read-only. Uses public sherpa archive examples (a smoke test, not an accuracy benchmark).
"""

import gc
import json
import os
from pathlib import Path
import time

import numpy as np

from thundertalk.core import audio_io, transcribe
from thundertalk.core.asr import AsrEngine
from thundertalk.core.models import BUILTIN_MODELS

root = Path(os.environ["BENCH_MODELS"])
out = Path("artifacts/models-v2")
models = {
    "funasr-nano-int8": "sherpa-onnx-funasr-nano-int8-2025-12-30",
}
dataset = root / "datasets"
fixtures = {
    lang: json.loads((dataset / f"{lang}.json").read_text())[index]
    for lang, index in (("zh", 0), ("en", 0), ("mixed", 1))
}
clips = {
    lang: audio_io.decode_audio(str(dataset / (item["id"] + ".wav")), 16000)
    for lang, item in fixtures.items()
}
zh = clips["zh"]
results = []
for mid, folder in models.items():
    info = next(m for m in BUILTIN_MODELS if m.id == mid)
    eng = AsrEngine()
    eng.set_hotwords(["开放时间", "ThunderTalk"])
    t0 = time.monotonic()
    eng.load_model(str(root / folder), info.family, info.backend, "low")
    load = time.monotonic() - t0
    try:
        for lang, clip in clips.items():
            r = eng.recognize(clip, 16000)
            row = dict(
                model=mid,
                clip=lang,
                source=fixtures[lang]["dataset"],
                fixture=fixtures[lang]["id"],
                reference=fixtures[lang]["reference"],
                text=r.text,
                audio_s=r.duration_secs,
                inference_ms=r.inference_ms,
                rtf=r.rtf,
                native_timestamps=len(r.token_timestamps),
                load_s=load,
            )
            print(json.dumps(row, ensure_ascii=False), flush=True)
            results.append(row)
        # >18 seconds proves the Studio span/chunk path and export times.
        clip = np.concatenate([zh, np.zeros(16000, np.float32)] * 5)
        wav = out / "public-long.wav"
        audio_io.write_wav(str(wav), clip, 16000)
        tr = transcribe.transcribe_file(str(wav), eng)
        row = dict(
            model=mid,
            clip="studio-long",
            audio_s=len(clip) / 16000,
            segments=[dict(start=s.start, end=s.end, text=s.text) for s in tr.segments],
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
        results.append(row)
    finally:
        eng.unload()
        gc.collect()
(out / "real-results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2)
)
