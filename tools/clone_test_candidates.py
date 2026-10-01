"""Have VoxCPM2 and IndexTTS speak a new line in the top candidates' voices —
what users will actually hear — and score it like the candidates.

    .venv/bin/python tools/clone_test_candidates.py CANDS_DIR [--top 2]

Reads CANDS_DIR/scores.json, writes CANDS_DIR/clones/<engine>/<slug>-<seed>.wav
and CANDS_DIR/clone_scores.json. One engine is loaded at a time (GPU).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from score_voice_candidates import BY, pitch_range  # noqa: E402

import utmos  # noqa: E402
from thundertalk.core import audio_io, speech, tts, tts_verify  # noqa: E402
from thundertalk.core.asr import AsrEngine  # noqa: E402

TEST = {
    "chinese": "今天想跟大家聊一聊，怎么用最简单的办法，把平平常常的每一天，过得更有意思一点。",
    "english": "Today I want to talk about a simple way to make every ordinary day a little more interesting.",
}


def pick(scores: dict, top: int) -> list[dict]:
    """Per style: drop clips the recogniser can't follow, then best UTMOS."""
    by: dict[str, list[dict]] = {}
    for r in scores.values():
        by.setdefault(r["slug"], []).append(r)
    chosen = []
    for rows in by.values():
        ok = [r for r in rows if r["read_back"] is not None and r["read_back"] <= 0.15] or rows
        chosen += sorted(ok, key=lambda r: -r["utmos"])[:top]
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--top", type=int, default=2)
    a = ap.parse_args()
    root = Path(a.root)
    chosen = pick(json.loads((root / "scores.json").read_text("utf-8")), a.top)
    asr = AsrEngine()
    asr.load_model("hf://Qwen/Qwen3-ASR-1.7B", "Qwen3-ASR", "mlx")
    asr._itn_enabled = False
    verify = tts_verify.make_verifier(asr)
    eng = speech.get_engine()
    path = root / "clone_scores.json"
    out = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    for bid in ("voxcpm2", "indextts"):
        for r in chosen:
            s = BY[r["slug"]]
            dst = root / "clones" / bid / f"{s.slug}-{r['seed']}.wav"
            if dst.is_file() and str(dst.relative_to(root)) in out:
                continue
            ref = audio_io.decode_audio(str(root / r["file"]), tts.SR)
            prompt = tts.ClonePrompt(ref, s.line, s.slug)
            t0 = time.time()
            res = eng.synthesize(TEST[s.language], prompt, language=s.language, seed=7, verifier=verify,
                                 clone_backend=bid)
            x = audio_io.resample(res.audio, res.sample_rate, 24000)
            dst.parent.mkdir(parents=True, exist_ok=True)
            audio_io.write_wav(str(dst), x, 24000)
            err = verify(x, TEST[s.language], s.language)
            rec = {"engine": bid, "slug": s.slug, "seed": r["seed"], "file": str(dst.relative_to(root)),
                   "utmos": round(utmos.score(str(dst)), 2), "read_back": None if err is None else round(err, 3),
                   "pitch_st": round(pitch_range(dst), 1), "seconds": round(len(x) / 24000, 1)}
            out[rec["file"]] = rec
            path.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
            print(f"[{bid}] {s.slug:18s} {r['seed']} utmos={rec['utmos']:.2f} err={rec['read_back']} "
                  f"pitch={rec['pitch_st']}st ({time.time() - t0:.1f}s)", flush=True)
        eng.unload()


if __name__ == "__main__":
    main()
