"""Cloud text-to-speech (OpenAI, Gemini, ElevenLabs, any OpenAI-compatible
endpoint) behind the normal backend contract. See core/cloud.py.

Differences from the local backends: nothing to download (ready = a key is
set), warm-up is free (it would otherwise bill a throwaway word), takes are
not retried (``stochastic = False``), and any voice name the provider accepts
works (``open_voices``), not only the listed ones."""

from __future__ import annotations

from typing import Optional

import numpy as np

from thundertalk.core import cloud
from thundertalk.core.tts_backends.base import BackendInfo, BackendVoice, TtsBackend


class CloudBackend(TtsBackend):
    stochastic = False
    open_voices = True
    is_cloud = True
    sample_rate = cloud.TTS_RATE

    def __init__(self, pid: str) -> None:
        p = cloud.PROVIDERS[pid]
        self.pid = pid
        self.info = BackendInfo(
            id=pid, name=p.name,
            blurb_en=f"Cloud · sends text to {p.name}",
            blurb_zh=f"云端 · 文本会发送给 {p.name}",
            languages=("chinese", "english", "multi"),
            supports_presets=True, supports_clone=False, needs_gpu=False, speed="cloud",
        )

    def is_ready(self) -> bool:
        return cloud.is_configured(self.pid)

    def voices(self) -> list[BackendVoice]:
        try:
            rows = cloud.voices(self.pid)
        except cloud.CloudError as exc:
            print(f"[TTS-Cloud] {self.pid} voices unavailable: {exc}")
            rows = []
        return [BackendVoice(id=r["id"], name=r["name"], language=r["language"], gender=r["gender"],
                             blurb_en=f"{self.info.name} (cloud)", blurb_zh=f"{self.info.name}（云端）")
                for r in rows]

    def load(self) -> None:
        if not self.is_ready():
            raise cloud.CloudError("cloud_no_key", f"No API key for {self.info.name}. Add one on the Models page.")

    def warm_up(self, voice, language: str) -> None:
        return None

    def generate(self, text: str, voice, language: str, *, seed: int = 0,
                 speed: float = 1.0, context: Optional[dict] = None) -> np.ndarray:
        if not isinstance(voice, str):
            raise cloud.CloudError("cloud_unsupported", f"{self.info.name} cannot use a cloned voice.")
        pid, name = cloud.split_id(voice)
        if pid != self.pid:
            raise ValueError(f"Voice {voice} does not belong to {self.pid}")
        return cloud.synthesize(self.pid, text, name)
