"""Real cached-latent repaint check on our original generated take; GPU lock required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import numpy as np
import soundfile as sf

from thundertalk.core import music
from thundertalk.core.music_metrics import PeakFootprint
from thundertalk.core.music_process import MusicProcess
from thundertalk.core.music_worker import MusicRequest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=Path('artifacts/music-spike/e2e/turbo-planner/take-2.wav'))
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    source = a.source.resolve()
    if not source.is_relative_to((Path.cwd() / 'artifacts/music-spike').resolve()):
        raise ValueError('Only our original generated spike WAVs are allowed')
    meta = json.loads(source.with_suffix('.json').read_text())
    if not meta.get('repaint_source_latents_file'):
        raise ValueError('Missing saved source latents')
    m = next(m for m in music.MUSIC_MODELS if m.folder == meta['model'])
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    a.output.mkdir(parents=True, exist_ok=True)
    req = MusicRequest(project_root=str(Path.home() / 'Library/Caches/ThunderTalk-bench/music-models'),
                       model_folder=m.folder, job_dir=str(a.output.resolve()),
                       caption=meta['caption'], lyrics=meta['lyrics'], shown_lyrics=meta.get('shown_lyrics', ''),
                       duration=meta['duration'], vocal_language=meta['vocal_language'],
                       instrumental=meta['instrumental'], steps=m.steps, planner=False, seeds=[meta['seed']],
                       task_type='repaint', src_audio=str(source), repainting_start=3, repainting_end=6,
                       estimate_s=music.estimate_take_s(m, meta['duration'], False))
    process = MusicProcess()
    start = time.monotonic()
    with PeakFootprint(process) as peak:
        outcome = process.run(req, lambda event: print(event[:3], flush=True), threading.Event(),
                              ram_need_mb=music.ram_need_mb(m, False))
    x, sr = sf.read(source, always_2d=True)
    y, yr = sf.read(a.output / 'take-1.wav', always_2d=True)
    same_shape = x.shape == y.shape and sr == yr
    outside_max = inside_max = None
    if same_shape:
        left, right = int(3 * sr), int(6 * sr)
        outside_max = float(max(np.max(np.abs(x[:left] - y[:left])), np.max(np.abs(x[right:] - y[right:]))))
        inside_max = float(np.max(np.abs(x[left:right] - y[left:right])))
    result = {'outcome': outcome, 'wall_s': time.monotonic() - start,
              'peak_worker_bytes': peak.peak, 'child_exited': process.pid is None,
              'source_unchanged': before == hashlib.sha256(source.read_bytes()).hexdigest(),
              'same_shape_and_sample_rate': same_shape, 'finite': bool(np.isfinite(y).all()),
              'outside_max_abs_change': outside_max, 'inside_max_abs_change': inside_max}
    (a.output / 'repaint-check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if (outcome == 'done' and result['child_exited'] and result['source_unchanged']
                 and same_shape and result['finite'] and outside_max <= 1 / 32768 and inside_max > 0) else 1


if __name__ == '__main__':
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    raise SystemExit(main())
