"""Time a speech engine from a fresh process: imports, cold load (split into
phases for VoxCPM2), first audio for a short sentence, and a warm second run.

    python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- \\
        .venv/bin/python tools/bench_tts_load.py voxcpm2 [--warmup]

``--warmup`` runs ``SpeechEngine.preload(..., warm=True)`` first, like the
Speak tab does when it becomes visible, and reports the time of the first
*real* request after it. Prints one JSON line at the end.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

T0 = time.monotonic()
MARKS: list[tuple[str, float]] = []


def mark(label: str, since: float) -> float:
    now = time.monotonic()
    MARKS.append((label, now - since))
    print(f"[{now - T0:7.2f}s] {label:<34} {now - since:7.2f}s", flush=True)
    return now


VOICES = {"voxcpm2": "voxcpm2:warm-female-zh", "indextts": "indextts:warm-female-zh", "kokoro": "kokoro:3"}
TEXT = "今天天气很好，我们去公园散步。"          # 15 characters
TEXT2 = "明天下午三点，我们在门口见面。"


def _instrument_mlx_audio() -> None:
    """Split mlx_audio's base_load_model into its phases."""
    import mlx.core as mx
    import mlx_audio.utils as mu

    def timed(name, fn):
        def wrap(*a, **k):
            t = time.monotonic()
            r = fn(*a, **k)
            mark(f"  load: {name}", t)
            return r
        return wrap

    mu.load_weights = timed("read safetensors (lazy)", mu.load_weights)
    mu.apply_quantization = timed("apply_quantization", mu.apply_quantization)
    orig_eval = mx.eval

    def ev(*a, **k):
        t = time.monotonic()
        r = orig_eval(*a, **k)
        if time.monotonic() - t > 0.05:
            mark("  load: mx.eval(params) = weights→GPU", t)
        return r
    mu.mx.eval = ev
    from mlx_audio.tts.models.voxcpm2 import voxcpm2 as vm
    vm.Model.sanitize = timed("sanitize", vm.Model.sanitize)
    hook = vm.Model.post_load_hook.__func__
    vm.Model.post_load_hook = classmethod(timed("tokenizer (transformers)", hook))
    enc = vm.Model._encode_wav

    def enc_t(self, *a, **k):
        t = time.monotonic()
        r = enc(self, *a, **k)
        mx.eval(r)
        mark("  gen: _encode_wav (ref → VAE)", t)
        return r
    vm.Model._encode_wav = enc_t


def main() -> int:
    bid = sys.argv[1] if len(sys.argv) > 1 else "voxcpm2"
    warm = "--warmup" in sys.argv
    load_avg = os.getloadavg()
    print(f"load average: {load_avg[0]:.1f} {load_avg[1]:.1f} {load_avg[2]:.1f}", flush=True)
    t = T0
    from thundertalk.core import speech
    t = mark("import thundertalk.core.speech", t)
    eng = speech.get_engine()
    b = speech.backend(bid)
    if not b.is_ready():
        print(f"{bid} is not downloaded")
        return 2
    if bid == "voxcpm2":
        import mlx.core  # noqa: F401
        t = mark("import mlx.core", t)
        import mlx_audio.tts.utils  # noqa: F401
        t = mark("import mlx_audio.tts.utils", t)
        _instrument_mlx_audio()
    t_load = time.monotonic()
    if warm:
        eng.preload(bid, voice=VOICES[bid], language="chinese", warm=True)
        t = mark("preload + warm-up", t_load)
    else:
        (getattr(eng, "load_backend", None) or eng._ensure)(bid)
        t = mark("load backend (total)", t_load)
    print(f"transformers imported: {'transformers' in sys.modules}", flush=True)
    cold_load = time.monotonic() - t_load
    t1 = time.monotonic()
    r1 = eng.synthesize(TEXT, VOICES[bid], language="chinese", seed=1)
    first = mark(f"first synth ({len(TEXT)} chars → {r1.duration:.1f}s audio)", t1) - t1
    t2 = time.monotonic()
    r2 = eng.synthesize(TEXT2, VOICES[bid], language="chinese", seed=2)
    second = mark(f"second synth (warm, {r2.duration:.1f}s audio)", t2) - t2
    total = time.monotonic() - T0
    eng.unload()
    print(json.dumps({"backend": bid, "warmup": warm, "load_avg_1m": round(load_avg[0], 1),
                      "process_to_ready_s": round(cold_load + (t_load - T0), 2), "load_s": round(cold_load, 2),
                      "first_synth_s": round(first, 2), "first_audio_from_start_s": round(t_load - T0 + cold_load
                                                                                         + first, 2),
                      "second_synth_s": round(second, 2), "total_s": round(total, 2),
                      "marks": [(k, round(v, 2)) for k, v in MARKS]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
