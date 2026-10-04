"""Private local regression: pass --audio and --models-dir; use lock.py gpu.

Outputs only counts and requested transcript edges. Never copies recordings,
changes source model folders, or writes history/settings in the real HOME.
"""

import argparse
import gc
import json
from pathlib import Path
import time

from thundertalk.core import audio_io
from thundertalk.core.asr import AsrEngine
from thundertalk.core.dictation import recover_final

ap = argparse.ArgumentParser(__doc__)
ap.add_argument('--audio', required=True)
ap.add_argument('--models-dir', required=True)
a = ap.parse_args()
x = audio_io.read_wav(a.audio)[0]
for mid, family in [('funasr-nano-int8', 'Fun-ASR-Nano'), ('qwen3-asr-06b-int8', 'Qwen3-ASR')]:
    eng = AsrEngine()
    # Exercise the tightest memory setting and a full hotword byte allowance.
    eng.set_hotwords(['safety' + str(i) for i in range(40)])
    eng.load_model(str(Path(a.models_dir) / mid), family, 'onnx', 'low')
    try:
        for name, clip in [('limit-first', x[:320000]), ('limit-last', x[-320000:]), ('full', x)]:
            start = time.monotonic()
            result = recover_final(eng, clip)
            row = dict(model=mid, clip=name, audio_s=len(clip)/16000,
                       chars=len(result.text), first80=result.text[:80], last80=result.text[-80:],
                       elapsed_s=round(time.monotonic()-start, 2), source=result.recovery_source,
                       truncated=result.truncated, max_clip_s=eng.max_clip_seconds,
                       prompt_bytes=len(eng._prompt_hotwords().encode()))
            print(json.dumps(row, ensure_ascii=False), flush=True)
            assert result.text.strip() and not result.truncated, row
    finally:
        eng.unload()
        gc.collect()
