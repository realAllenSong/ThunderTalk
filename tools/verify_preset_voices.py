"""Check every built-in reference voice: the recogniser must read back its
transcript (≤ 5 % error) and the median pitch must fit the labelled gender.

    .venv/bin/python tools/verify_preset_voices.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from thundertalk.core import audio_io, tts_verify  # noqa: E402
from thundertalk.core.asr import AsrEngine  # noqa: E402

D = Path(__file__).resolve().parents[1] / "assets" / "voices"


def median_f0(x: np.ndarray, sr: int = 16000) -> float:
    """Autocorrelation pitch over voiced 40 ms frames (no extra dependencies)."""
    n, hop, out = int(0.04 * sr), int(0.01 * sr), []
    for i in range(0, len(x) - n, hop):
        f = x[i:i + n] - x[i:i + n].mean()
        if np.sqrt(np.mean(f ** 2)) < 0.02:
            continue
        ac = np.correlate(f, f, "full")[n - 1:]
        lo, hi = sr // 400, sr // 60
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            out.append(sr / lag)
    return float(np.median(out)) if out else 0.0


def main() -> int:
    asr = AsrEngine()
    asr.load_model("hf://Qwen/Qwen3-ASR-1.7B", "Qwen3-ASR", "mlx")
    asr._itn_enabled = False
    ok_all = True
    for it in json.loads((D / "voices.json").read_text("utf-8")):
        x = audio_io.decode_audio(str(D / it["file"]), 16000)
        heard = asr.recognize(x, 16000).text
        err = tts_verify.error_rate(it["text"], heard, it["language"]) or 0.0
        f0 = median_f0(x)
        # Adult male speech is ~85–180 Hz; energetic delivery (YouTuber) sits at the top of it.
        gender_ok = (f0 >= 150) if it["gender"] == "f" else (60 <= f0 < 185)
        ok = err <= 0.05 and gender_ok
        ok_all &= ok
        print(f"{'OK ' if ok else 'BAD'} {it['slug']:18} {it['duration_s']:4.1f}s  err {100 * err:4.1f}%  "
              f"F0 {f0:5.0f} Hz ({it['gender']})  | {heard}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
