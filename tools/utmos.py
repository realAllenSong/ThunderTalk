"""UTMOS22 naturalness score (predicted MOS, 1–5) on the CPU — a proxy used to
rank voice candidates; the final choice is by ear.

    .venv/bin/python tools/utmos.py FILE_OR_DIR ...
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

_M = None


def score(path: str) -> float:
    global _M
    import torch
    from thundertalk.core import audio_io
    if _M is None:
        torch.set_num_threads(4)
        _M = torch.hub.load("tarepan/SpeechMOS:v1.2.0", "utmos22_strong", trust_repo=True)
    x = audio_io.decode_audio(str(path), 16000).astype(np.float32)
    with torch.no_grad():
        return float(_M(torch.from_numpy(x)[None], 16000))


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        p = Path(arg)
        files = sorted(f for f in p.rglob("*") if f.suffix in (".wav", ".flac", ".m4a", ".mp3")) if p.is_dir() else [p]
        for f in files:
            print(f"{score(f):.2f}  {f}", flush=True)
