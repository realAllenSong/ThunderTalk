"""Qwen read-back of our original benchmark vocals; run through lock.py gpu."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

from thundertalk.core import music_check
from thundertalk.core.asr import AsrEngine


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('takes', type=Path, nargs='+')
    ap.add_argument('--asr', type=Path, default=Path.home() / 'Library/Caches/ThunderTalk-bench/music-models/lyric-asr')
    a = ap.parse_args()
    root = (Path.cwd() / 'artifacts/music-spike').resolve()
    paths = [p.resolve() for p in a.takes]
    if any(not p.is_relative_to(root) for p in paths):
        raise ValueError('Only our original generated spike WAVs are allowed')
    engine = AsrEngine()
    engine.load_model(str(a.asr), 'Qwen3-ASR', 'onnx')
    try:
        for path in paths:
            meta = json.loads(path.with_suffix('.json').read_text())
            engine.set_language(meta['vocal_language'])
            start = time.monotonic()
            result = music_check.check_take(path, lambda x: engine.recognize(x, 16000).text, meta)
            if result is None:
                raise ValueError(f'Not a vocal take: {path}')
            out = dict(asdict(result), summary=result.summary, seconds=time.monotonic() - start)
            path.with_suffix('.lyric-check.json').write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({'take': str(path), 'summary': result.summary, 'seconds': out['seconds'],
                              'checks': out['checks']}, ensure_ascii=False), flush=True)
    finally:
        engine.unload()
    return 0


if __name__ == '__main__':
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    raise SystemExit(main())
