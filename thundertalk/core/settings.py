"""JSON-backed persistent settings at ~/.thundertalk/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "hotkey": "cmd_r",
    "press_mode": "toggle",
    "microphone": "auto",
    "mute_speakers": True,
    "language": "en",
    "launch_at_startup": False,
    "silent_launch": True,
    "transcription_language": "auto",
    "save_to_clipboard": True,
    "hotwords": [],
    "active_model_id": "",
    "translation_target": "off",
    "translation_mode": "direct",  # "direct" (S2TT) or "review" (Pipeline + popup)
    "log_enabled": True,
    # ASR memory profile.
    #   "high" (default) — max_total_len=4096, max_new_tokens=2048, full
    #     thread count. Supports very long single utterances and decoder
    #     output. ~3.8 GB physical footprint on M3 Max.
    #   "low" — max_total_len=1024, max_new_tokens=256, capped to 4 threads.
    #     Sufficient for ~40s utterances and ~150-char outputs. ~1 GB
    #     physical footprint.
    # Takes effect on next ASR model load (typically next app launch).
    "memory_mode": "high",
    # Last running version, recorded each launch. When this differs
    # from the current __version__ we know the user just upgraded
    # (likely via the in-app updater) and surface a one-time hint
    # about macOS permissions resetting under ad-hoc code signing.
    "last_run_version": "",
}

_PATH = Path.home() / ".thundertalk" / "settings.json"


class Settings:
    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if _PATH.exists():
            try:
                with open(_PATH, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                for k, v in stored.items():
                    if k in DEFAULTS:
                        self._data[k] = v
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        """Atomic write: a crash mid-save cannot corrupt the existing file."""
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _PATH)

    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    @property
    def hotkey(self) -> str:
        return self._data["hotkey"]

    @property
    def press_mode(self) -> str:
        return self._data["press_mode"]

    @property
    def microphone(self) -> str:
        return self._data["microphone"]

    @property
    def hotwords(self) -> list[str]:
        return self._data.get("hotwords", [])

    @property
    def active_model_id(self) -> str:
        return self._data.get("active_model_id", "")

    @property
    def transcription_language(self) -> str:
        return self._data.get("transcription_language", "auto")

    @property
    def translation_target(self) -> str:
        return self._data.get("translation_target", "off")

    @property
    def memory_mode(self) -> str:
        """'high' (default) — bigger ONNX KV cache + full thread count.
        'low' — slimmer KV cache + 4 threads, ~3 GB less RAM."""
        m = self._data.get("memory_mode", "high")
        return m if m in ("high", "low") else "high"

    @property
    def translation_mode(self) -> str:
        """'direct' — S2TT, paste translated text directly.
        'review' — Pipeline (Qwen3-ASR + SeamlessM4T T2TT), popup for confirm.
        Only meaningful when translation_target != 'off'.
        """
        mode = self._data.get("translation_mode", "direct")
        return mode if mode in ("direct", "review") else "direct"

