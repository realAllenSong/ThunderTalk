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


START_S, END_S = 3, 6
# The VAE re-decodes the whole latent, so the edges of the window blend for up to
# ~1 s; outside that guard the take must be unchanged, inside it must be new.
GUARD_S = 1.0
OUTSIDE_MIN_SNR_DB = 40.0
INSIDE_MAX_SNR_DB = 10.0


def _snr_db(reference, other):
    noise = float(np.sqrt(np.mean((reference - other) ** 2)))
    signal = float(np.sqrt(np.mean(reference ** 2)))
    return 20 * np.log10(max(signal, 1e-12) / max(noise, 1e-12))


def compare(x, y, sr):
    left, right = int(START_S * sr), int(END_S * sr)
    guard = int(GUARD_S * sr)
    outside_x = np.concatenate([x[:left - guard], x[right + guard:]])
    outside_y = np.concatenate([y[:left - guard], y[right + guard:]])
    return {'outside_snr_db': _snr_db(outside_x, outside_y),
            'inside_snr_db': _snr_db(x[left:right], y[left:right]),
            'outside_max_abs_change': float(np.max(np.abs(outside_x - outside_y))),
            'inside_max_abs_change': float(np.max(np.abs(x[left:right] - y[left:right]))),
            'guard_s': GUARD_S}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=Path('artifacts/music-spike/e2e/turbo-planner/take-2.wav'))
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--compare-only', action='store_true', help='re-score an existing repaint take (no model)')
    a = ap.parse_args()
    source = a.source.resolve()
    if not source.is_relative_to((Path.cwd() / 'artifacts/music-spike').resolve()):
        raise ValueError('Only our original generated spike WAVs are allowed')
    if a.compare_only:
        result = json.loads((a.output / 'repaint-check.json').read_text())
        return _score(source, a.output, result)
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
                       task_type='repaint', src_audio=str(source), repainting_start=START_S, repainting_end=END_S,
                       estimate_s=music.estimate_take_s(m, meta['duration'], False))
    process = MusicProcess()
    start = time.monotonic()
    with PeakFootprint(process) as peak:
        outcome = process.run(req, lambda event: print(event[:3], flush=True), threading.Event(),
                              ram_need_mb=music.ram_need_mb(m, False))
    result = {'outcome': outcome, 'wall_s': time.monotonic() - start,
              'peak_worker_bytes': peak.peak, 'child_exited': process.pid is None,
              'source_unchanged': before == hashlib.sha256(source.read_bytes()).hexdigest()}
    return _score(source, a.output, result)


def _score(source, output, result):
    x, sr = sf.read(source, always_2d=True)
    y, yr = sf.read(output / 'take-1.wav', always_2d=True)
    same_shape = x.shape == y.shape and sr == yr
    result.update({'same_shape_and_sample_rate': same_shape, 'finite': bool(np.isfinite(y).all())})
    if same_shape:
        result.update(compare(x, y, sr))
    (output / 'repaint-check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if (result['outcome'] == 'done' and result['child_exited'] and result['source_unchanged']
                 and same_shape and result['finite'] and result['outside_snr_db'] >= OUTSIDE_MIN_SNR_DB
                 and result['inside_snr_db'] <= INSIDE_MAX_SNR_DB) else 1


if __name__ == '__main__':
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    raise SystemExit(main())
