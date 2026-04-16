"""JSON-backed persistent settings at ~/.thundertalk/settings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "hotkey": "cmd_r",
    "press_mode": "toggle",
    "microphone": "auto",
    "mute_speakers": True,
    "theme": "dark",
    "language": "en",
    "launch_at_startup": False,
    "silent_launch": True,
    "show_in_dock": False,
    "transcription_language": "auto",
    "save_to_clipboard": True,
    "hotwords": [],
    "active_model_id": "",
    "log_enabled": True,
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
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

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

