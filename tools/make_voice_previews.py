"""Render the short preview clip for every built-in voice of every engine
(assets/voices/previews/<engine>/<voice>.flac), so Speak can play a voice
without generating anything. Each clip is spoken by that engine and checked
by reading it back with Qwen3-ASR 1.7B (retries on a mismatch).

    .venv/bin/python tools/make_voice_previews.py [--engine kokoro] [--force]

One engine is loaded at a time; run nothing else on the GPU meanwhile.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from thundertalk.core import audio_io, speech, tts_verify  # noqa: E402
from thundertalk.core.asr import AsrEngine  # noqa: E402
from thundertalk.core.tts_backends.previews import PREVIEW_SR, PREVIEW_TEXT, preview_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", action="append", choices=speech.BACKEND_ORDER)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    asr = AsrEngine()
    asr.load_model("hf://Qwen/Qwen3-ASR-1.7B", "Qwen3-ASR", "mlx")
    asr._itn_enabled = False
    verify = tts_verify.make_verifier(asr)
    eng = speech.get_engine()
    failed = []
    for bid in args.engine or speech.BACKEND_ORDER:
        b = speech.backend(bid)
        if not b.is_ready():
            print(f"[{bid}] not downloaded — skipped")
            continue
        for v in b.voices():
            out = preview_path(v.id, root=Path(__file__).resolve().parents[1])
            if out.is_file() and not args.force:
                continue
            text = PREVIEW_TEXT[v.language]
            best = None
            for seed in (7, 8, 9, 10):
                t0 = time.time()
                res = eng.synthesize(text, v.id, language=v.language, seed=seed, verifier=verify)
                x = audio_io.resample(res.audio.astype(np.float32), res.sample_rate, PREVIEW_SR)
                err = verify(audio_io.resample(x, PREVIEW_SR, 24000), text, v.language)
                print(f"[{bid}] {v.id:28s} seed={seed} {len(x) / PREVIEW_SR:4.1f}s "
                      f"err={err if err is None else round(err, 3)} ({time.time() - t0:.1f}s)", flush=True)
                if best is None or (err or 0) < best[1]:
                    best = (x, err or 0)
                if (err or 0) <= 0.05:
                    break
            if best[1] > 0.05:
                failed.append((v.id, best[1]))
            x = best[0] / max(1e-6, float(np.max(np.abs(best[0])))) * 0.89   # peak −1 dBFS
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out), x, PREVIEW_SR, format="FLAC", subtype="PCM_16")
        eng.unload()
    print("FAILED:", failed if failed else "none")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
