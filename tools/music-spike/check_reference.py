"""Check the objectively intelligible Phase-0 reference with the calibrated threshold."""
import json
import sys
from pathlib import Path

from run import LYRICS

from thundertalk.core import music_check
from thundertalk.core.asr import AsrEngine
from thundertalk.core.models import get_model_path


def main():
    wav = Path('artifacts/music-spike/turbo-5.6.2-mlx-vocal-vae192-mxcache1-weights/gate.wav')
    engine = AsrEngine()
    engine.load_model(get_model_path('qwen3-asr-06b-int8'), 'Qwen3-ASR', 'onnx')
    try:
        engine.set_language('en')
        result = music_check.check_take(wav, lambda x: engine.recognize(x, 16000).text,
                                       {'lyrics': LYRICS, 'vocal_language': 'en'})
        print(json.dumps({'summary': result.summary, 'checks': [c.__dict__ for c in result.checks]}, indent=2))
    finally:
        engine.unload()
    return 0


if __name__ == '__main__':
    sys.exit(main())
