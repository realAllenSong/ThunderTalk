"""Lyric read-back check for gate takes, with a spoken-lyrics control.

For each WAV: the raw Qwen3-ASR read-back, then the read-back of the same take with macOS `say`
reading the gate lyrics mixed in at the same RMS as the music. If the control reads back but the
raw take does not, the take has no intelligible vocal; if neither does, the checker is at fault.
Run through lock.py gpu.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np

WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
BENCH = Path(os.environ.get('TT_BENCH') or Path.home() / 'Library/Caches/ThunderTalk-bench')

from run import LYRICS  # noqa: E402
from thundertalk.core import audio_io  # noqa: E402
from thundertalk.core.asr import AsrEngine  # noqa: E402

SR = 16000


def spoken_lyrics():
    text = ' '.join(line for line in LYRICS.splitlines() if line and not line.startswith('['))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'lyrics.aiff'
        subprocess.run(['say', '-o', str(path), text], check=True)
        return audio_io.decode_audio(str(path), SR)


def read_back(asr, samples):
    return ' '.join(asr.recognize(samples[i:i + 15 * SR], SR).text.strip()
                    for i in range(0, len(samples), 13 * SR)).strip()


def word_recall(text):
    words = {w.strip('.,!?').lower() for w in LYRICS.split() if not w.startswith('[')}
    heard = {w.strip('.,!?').lower() for w in text.split()}
    return round(len(words & heard) / len(words), 2)


def main(paths):
    asr = AsrEngine()
    asr.load_model(str(BENCH / 'music-models/lyric-asr'), 'Qwen3-ASR', 'onnx')
    asr.set_language('en')
    speech = spoken_lyrics()
    results = {}
    try:
        for path in paths:
            music = audio_io.decode_audio(path, SR)
            voice = np.zeros_like(music)
            voice[:min(len(speech), len(music))] = speech[:len(music)]
            music_rms = float(np.sqrt(np.mean(music ** 2))) or 1e-9
            voice_rms = float(np.sqrt(np.mean(voice[voice != 0] ** 2))) or 1e-9
            mixed = music + voice * (music_rms / voice_rms)
            mixed /= max(1.0, float(np.abs(mixed).max()))
            raw = read_back(asr, music)
            control = read_back(asr, mixed.astype(np.float32))
            results[path] = {'raw': raw, 'raw_recall': word_recall(raw),
                             'control': control, 'control_recall': word_recall(control)}
            print(json.dumps({path: results[path]}), flush=True)
    finally:
        asr.unload()
    return results


if __name__ == '__main__':
    main(sys.argv[1:])
