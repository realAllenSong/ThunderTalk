"""Regenerate the built-in reference voices in assets/voices.

Each voice is designed by VoxCPM2 (Apache-2.0) from a text description — no real
person's recording is involved — then saved as a 24 kHz reference clip with its
exact transcript. Run from the repo root with the project venv:

    .venv/bin/python tools/make_preset_voices.py [slug ...]

Afterwards verify every clip with tools/verify_preset_voices.py before committing.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thundertalk.core import audio_io  # noqa: E402
from thundertalk.core.tts_backends import voxcpm2 as vx  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "assets" / "voices"


def main(only: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    backend = vx.VoxCPM2Backend(cache_dir=Path(tempfile.mkdtemp()), use_shipped=False)
    backend.load()
    meta_path = OUT / "voices.json"
    items = {it["slug"]: it for it in (json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else [])}
    for d in vx._DESIGNS:
        if only and d.slug not in only:
            continue
        audio48, text = backend._make_ref(d)
        a24 = audio_io.resample(audio48, vx.SR, 24000)
        audio_io.write_wav(str(OUT / f"{d.slug}.wav"), a24, 24000)
        items[d.slug] = {"slug": d.slug, "name": d.name, "language": d.language, "gender": d.gender,
                         "description": d.instruct, "blurb_en": d.blurb_en, "blurb_zh": d.blurb_zh,
                         "file": f"{d.slug}.wav", "text": text, "seed": d.seed,
                         "duration_s": round(len(a24) / 24000, 2)}
        print(f"{d.slug}: {len(a24) / 24000:.1f}s", flush=True)
    order = [d.slug for d in vx._DESIGNS]
    meta_path.write_text(json.dumps([items[s] for s in order if s in items], ensure_ascii=False, indent=1) + "\n",
                         "utf-8")
    backend.unload()


if __name__ == "__main__":
    main(sys.argv[1:])
