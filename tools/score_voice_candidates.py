"""Score designed candidates (tools/design_voice_candidates.py):
UTMOS naturalness (CPU), read-back error with Qwen3-ASR 1.7B (GPU), pitch
range in semitones (monotone vs expressive) and length.

    .venv/bin/python tools/score_voice_candidates.py CANDS_DIR

Writes CANDS_DIR/scores.json. Uses the GPU for the recogniser.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

import utmos  # noqa: E402
from thundertalk.core import audio_io, tts_verify  # noqa: E402
from thundertalk.core.asr import AsrEngine  # noqa: E402
from voice_styles import STYLES  # noqa: E402

BY = {s.slug: s for s in STYLES}


def pitch_range(path: Path) -> float:
    """Spread of voiced pitch (10th–90th percentile) in semitones."""
    import parselmouth
    snd = parselmouth.Sound(str(path))
    f0 = snd.to_pitch(time_step=0.01, pitch_floor=60, pitch_ceiling=600).selected_array["frequency"]
    f0 = f0[f0 > 0]
    if len(f0) < 20:
        return 0.0
    lo, hi = np.percentile(f0, [10, 90])
    return float(12 * np.log2(hi / lo))


def main(root: Path) -> None:
    asr = AsrEngine()
    asr.load_model("hf://Qwen/Qwen3-ASR-1.7B", "Qwen3-ASR", "mlx")
    asr._itn_enabled = False
    out = {}
    for wav in sorted(root.glob("*/*.wav")):
        meta = json.loads(wav.with_suffix(".json").read_text("utf-8"))
        s = BY[meta["slug"]]
        x16 = audio_io.decode_audio(str(wav), 16000)
        heard = asr.recognize(x16, 16000).text or ""
        err = tts_verify.error_rate(meta["text"], heard, s.language)
        rec = {"slug": s.slug, "seed": meta["seed"], "file": str(wav.relative_to(root)),
               "seconds": round(len(x16) / 16000, 2), "utmos": round(utmos.score(str(wav)), 2),
               "read_back": None if err is None else round(err, 3), "heard": heard,
               "pitch_st": round(pitch_range(wav), 1)}
        out[rec["file"]] = rec
        print(f"{rec['file']:28s} utmos={rec['utmos']:.2f} err={rec['read_back']} pitch={rec['pitch_st']}st "
              f"{rec['seconds']}s", flush=True)
    (root / "scores.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
