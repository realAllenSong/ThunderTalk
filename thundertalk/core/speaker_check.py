"""Does a generated take still sound like the voice it was asked for?

Polished speech generates several takes of each sentence and keeps one. A
cloning model now and then drifts: a take reads every word right yet sounds
like someone else, or like the same person with a cold. A speaker-verification
model, one that is not part of any of the speech engines, compares each take
with the voice's reference recording, and a take that has drifted is passed
over (Kaikou's second check, after reading the text back).

The model is CAM++ (3D-Speaker, Apache-2.0), trained on Chinese and English
speech, 28 MB, run on the CPU through sherpa-onnx. It is fetched the first
time polished speech is made and checked against its SHA-256.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
       "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx")
SHA256 = "53cab20ba8f2eb2f6e2d89ecc18e88ef3222169b61b4419cbc13666744a4574b"
FILE = "campplus_sv_zh_en_16k.onnx"
SIZE_MB = 28

_lock = threading.Lock()
_extractor = None


def model_path() -> Path:
    from thundertalk.core.models import get_models_dir
    return get_models_dir() / "speaker" / FILE


def available() -> bool:
    return model_path().is_file()


def ensure(progress: Optional[Callable[[int, str], None]] = None,
           cancel: Optional[threading.Event] = None) -> bool:
    """Fetch the model if it is not here yet. False when it could not be had
    (offline, a bad download): the caller goes on without the check."""
    if available():
        return True
    from thundertalk.core import models
    dest = model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        models._curl_download(URL, tmp, progress or (lambda *_: None), cancel, 0, 99)
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        if h.hexdigest() != SHA256:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(dest)
        return True
    except Exception:
        tmp.unlink(missing_ok=True)
        return False


def _get():
    global _extractor
    with _lock:
        if _extractor is None:
            import sherpa_onnx
            _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(model_path()), num_threads=2))
        return _extractor


def embed(audio: np.ndarray, sr: int) -> np.ndarray:
    """A unit-length voiceprint of ``audio`` (mono float32 at ``sr``)."""
    from thundertalk.core import audio_io
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    x16 = audio_io.resample(x, sr, 16000) if sr != 16000 else x
    ex = _get()
    s = ex.create_stream()
    s.accept_waveform(16000, x16)
    s.input_finished()
    v = np.asarray(ex.compute(s), dtype=np.float32)
    return v / (float(np.linalg.norm(v)) + 1e-9)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))
