"""ZipVoice distill int8: CPU cloning with an exact reference transcript."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from thundertalk.core import audio_io
from thundertalk.core.tts import ClonePrompt, TtsModelMissing
from thundertalk.core.tts_backends.base import (
    BackendInfo,
    BackendVoice,
    Download,
    TtsBackend,
)
from thundertalk.core.tts_backends.presets import load_presets

MODEL_FOLDER = "sherpa-onnx-zipvoice-distill-int8-zh-en-emilia"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL_FOLDER}.tar.bz2"
VOCODER = "vocos_24khz.onnx"
VOCODER_URL = (
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/{VOCODER}"
)
SR = 24000


class ZipVoiceBackend(TtsBackend):
    info = BackendInfo(
        id="zipvoice",
        name="ZipVoice",
        params="123M",
        language_codes=("zh", "en"),
        blurb_en="CPU voice cloning in Chinese and English. Requires reference audio and its exact transcript. 109 MB model + 54 MB vocoder.",
        blurb_zh="CPU 中英文声音克隆。需要参考音频及其准确文本。模型 109 MB + 声码器 54 MB。",
        languages=("chinese", "english"),
        supports_presets=True,
        supports_clone=True,
        needs_gpu=False,
        downloads=(
            Download("tar", MODEL_URL, MODEL_FOLDER, 109),
            Download("file", VOCODER_URL, VOCODER, 54),
        ),
    )
    sample_rate = SR

    def __init__(
        self, model_dir: Path | None = None, vocoder: Path | None = None, num_threads=4
    ):
        self._model_dir, self._vocoder = model_dir, vocoder
        self._num_threads = num_threads
        self._tts = None
        self._lock = threading.Lock()

    @property
    def model_dir(self):
        from thundertalk.core.models import get_models_dir

        return (
            Path(self._model_dir)
            if self._model_dir is not None
            else get_models_dir() / MODEL_FOLDER
        )

    @property
    def vocoder(self):
        from thundertalk.core.models import get_models_dir

        return (
            Path(self._vocoder)
            if self._vocoder is not None
            else get_models_dir() / VOCODER
        )

    def is_ready(self):
        return (
            self.vocoder.is_file()
            and all(
                (self.model_dir / f).is_file()
                for f in (
                    "encoder.int8.onnx",
                    "decoder.int8.onnx",
                    "tokens.txt",
                    "lexicon.txt",
                )
            )
            and (self.model_dir / "espeak-ng-data").is_dir()
        )

    def voices(self):
        # Synthetic app-owned clips; provenance/license: docs/model-metadata.md.
        return [
            BackendVoice(
                f"zipvoice:{p.slug}",
                p.name,
                p.language,
                p.gender,
                p.blurb_en,
                p.blurb_zh,
            )
            for p in load_presets()
            if p.language in ("chinese", "english")
        ]

    def load(self):
        with self._lock:
            if self._tts is not None:
                return
            if not self.is_ready():
                raise TtsModelMissing(MODEL_URL)
            import sherpa_onnx as so

            d = self.model_dir
            self._tts = so.OfflineTts(
                so.OfflineTtsConfig(
                    model=so.OfflineTtsModelConfig(
                        zipvoice=so.OfflineTtsZipvoiceModelConfig(
                            tokens=str(d / "tokens.txt"),
                            encoder=str(d / "encoder.int8.onnx"),
                            decoder=str(d / "decoder.int8.onnx"),
                            vocoder=str(self.vocoder),
                            data_dir=str(d / "espeak-ng-data"),
                            lexicon=str(d / "lexicon.txt"),
                        ),
                        num_threads=self._num_threads,
                        provider="cpu",
                    ),
                    max_num_sentences=1,
                )
            )

    def unload(self):
        with self._lock:
            self._tts = None

    @staticmethod
    def reference(voice):
        if isinstance(voice, ClonePrompt):
            audio, text = voice.audio, voice.text
        else:
            preset = next(
                (p for p in load_presets() if f"zipvoice:{p.slug}" == voice), None
            )
            if preset is None:
                raise ValueError(f"Unknown ZipVoice voice: {voice}")
            audio, sr = audio_io.read_wav(str(preset.wav))
            if sr != SR:
                audio = audio_io.resample(audio, sr, SR)
            text = preset.text
        if not text.strip() or len(audio) == 0:
            raise ValueError(
                "ZipVoice requires reference audio and its exact transcript"
            )
        if not np.isfinite(audio).all():
            raise ValueError("Invalid ZipVoice reference audio")
        return np.asarray(audio, dtype=np.float32).reshape(-1), text.strip()

    def generate(self, text, voice, language, *, seed=0, speed=1.0, context=None):
        audio, transcript = self.reference(voice)
        if language not in self.info.languages:
            raise ValueError(f"ZipVoice does not support {language}")
        if not text.strip():
            return np.zeros(0, np.float32)
        self.load()
        with self._lock:
            if self._tts is None:
                raise RuntimeError("ZipVoice was unloaded during generation")
            out = self._tts.generate(
                text,
                prompt_text=transcript,
                prompt_samples=audio,
                sample_rate=SR,
                speed=float(speed),
                num_steps=4,
            )
        if out.sample_rate != SR:
            raise RuntimeError(f"Unexpected ZipVoice sample rate: {out.sample_rate}")
        if context is not None:
            context["native_speed"] = True
        return np.asarray(out.samples, dtype=np.float32)
