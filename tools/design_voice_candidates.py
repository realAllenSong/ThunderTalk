"""Design several candidate reference clips per voice style with VoxCPM2,
for scoring (tools/score_voice_candidates.py) and listening.

    .venv/bin/python tools/design_voice_candidates.py OUT_DIR [--seeds 4] [slug ...]

Writes OUT_DIR/<slug>/<seed>.wav (24 kHz) and OUT_DIR/<slug>/<seed>.json.
Uses the GPU: run nothing else on it meanwhile.
"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from thundertalk.core import audio_io  # noqa: E402
from thundertalk.core.tts_backends import voxcpm2 as vx  # noqa: E402
from voice_styles import STYLES  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--seeds", type=int, default=4)
    a = ap.parse_args()
    out = Path(a.out)
    b = vx.VoxCPM2Backend(cache_dir=Path(tempfile.mkdtemp()), use_shipped=False)
    b.load()
    for s in STYLES:
        if a.slugs and s.slug not in a.slugs:
            continue
        d = out / s.slug
        d.mkdir(parents=True, exist_ok=True)
        for k in range(a.seeds):
            seed = 101 + k
            if (d / f"{seed}.wav").is_file():
                continue
            t0 = time.time()
            x = vx._trim_edges(b._run(s.line, seed, instruct=s.instruct))
            x24 = audio_io.resample(x, vx.SR, 24000)
            audio_io.write_wav(str(d / f"{seed}.wav"), x24, 24000)
            (d / f"{seed}.json").write_text(json.dumps({"slug": s.slug, "seed": seed, "text": s.line,
                                                        "instruct": s.instruct}, ensure_ascii=False), "utf-8")
            print(f"{s.slug:18s} seed={seed} {len(x24) / 24000:5.1f}s ({time.time() - t0:.1f}s)", flush=True)
    b.unload()


if __name__ == "__main__":
    main()
