"""One real Compose job through thundertalk.core.music_process (no UI).

Run with the venv-music python under lock.py gpu. Writes takes to
artifacts/music-spike/e2e/<tag>/ and prints every event plus a JSON summary.
Usage: e2e.py [--duration 20] [--takes 2] [--planner] [--dit mlx|mps] [--tag name]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import LYRICS, footprint  # noqa: E402

from thundertalk.core import music  # noqa: E402
from thundertalk.core.music_process import MusicError, MusicProcess  # noqa: E402
from thundertalk.core.music_worker import MusicRequest, take_seeds  # noqa: E402

MODELS = Path.home() / "Library/Caches/ThunderTalk-bench/music-models"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=20)
    ap.add_argument("--takes", type=int, default=2)
    ap.add_argument("--planner", action="store_true")
    ap.add_argument("--dit", default="mlx")
    ap.add_argument("--tag", default="turbo")
    args = ap.parse_args()
    job = ROOT / "artifacts/music-spike/e2e" / args.tag
    job.mkdir(parents=True, exist_ok=True)
    request = MusicRequest(
        project_root=str(MODELS), model_folder="acestep-v15-turbo", job_dir=str(job),
        caption="Warm folk pop, acoustic guitar, soft piano, gentle brushed drums, clear vocal, calm sunrise mood.",
        lyrics=LYRICS, vocal_language="en", duration=args.duration, steps=8,
        seeds=take_seeds(1234, args.takes), planner=args.planner, dit_backend=args.dit)
    events = []
    start = time.monotonic()

    def on_event(event):
        events.append(event)
        if event[0] not in ("step", "tokens") or event[1] == event[2]:
            print(f"{time.monotonic() - start:7.1f}s {event[:3]}", flush=True)

    process = MusicProcess()
    outcome, code = "error", ""
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.5):
            pid = process.pid
            if pid:
                peak[0] = max(peak[0], footprint(pid))

    threading.Thread(target=sample, daemon=True).start()
    try:
        outcome = process.run(request, on_event, threading.Event(),
                              ram_need_mb=music.ram_need_mb(music.TURBO, args.planner))
    except MusicError as exc:
        code = exc.code
        print("MusicError", exc.code, exc.detail[:2000], flush=True)
    stop.set()
    takes = [e for e in events if e[0] == "take"]
    summary = {
        "outcome": outcome, "error": code, "wall_s": round(time.monotonic() - start, 1),
        "takes": [{"wav": e[2], "seed": e[3]["seed"], "wall_s": e[3]["wall_seconds"],
                   "duration": e[3]["duration"], "latents": e[3]["repaint_source_latents_file"]} for e in takes],
        "kinds": sorted({e[0] for e in events}),
        "child_peak_footprint_gb": round(peak[0] / 2**30, 2),
        "child_alive_after": process.pid is not None,
        "files": sorted(os.listdir(job)),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if outcome == "done" and len(takes) == args.takes else 1


if __name__ == "__main__":
    raise SystemExit(main())
