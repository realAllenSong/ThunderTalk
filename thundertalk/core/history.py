"""Transcription history stored in ~/.thundertalk/history.json."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_PATH = Path.home() / ".thundertalk" / "history.json"
_MAX_ENTRIES = 1000


@dataclass
class HistoryEntry:
    text: str
    timestamp: float
    duration_secs: float
    inference_ms: int
    model: str
    # Optional translation (Review-mode result). Empty string when no
    # translation was produced for this entry.
    translation: str = ""
    translation_lang: str = ""


class HistoryStore:
    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []
        self.load()

    def load(self) -> None:
        if _PATH.exists():
            try:
                with open(_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._entries = [HistoryEntry(**e) for e in raw]
            except (json.JSONDecodeError, OSError, TypeError):
                self._entries = []

    def save(self) -> None:
        """Atomic write: a crash mid-save cannot corrupt the existing file."""
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(e) for e in self._entries[-_MAX_ENTRIES:]]
        tmp = _PATH.with_suffix(_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _PATH)

    def add(
        self,
        text: str,
        duration_secs: float,
        inference_ms: int,
        model: str,
        translation: str = "",
        translation_lang: str = "",
    ) -> None:
        self._entries.append(
            HistoryEntry(
                text=text,
                timestamp=time.time(),
                duration_secs=duration_secs,
                inference_ms=inference_ms,
                model=model,
                translation=translation,
                translation_lang=translation_lang,
            )
        )
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]
        self.save()

    def update_translation(
        self,
        original_text: str,
        translation: str,
        translation_lang: str,
    ) -> None:
        """Backfill the translation onto the most recent matching entry.
        Used because Review-mode T2TT completes AFTER the original entry
        was already added to history."""
        for entry in reversed(self._entries):
            if entry.text == original_text and not entry.translation:
                entry.translation = translation
                entry.translation_lang = translation_lang
                self.save()
                return

    def clear(self) -> None:
        self._entries.clear()
        self.save()

    @property
    def entries(self) -> list[HistoryEntry]:
        return list(reversed(self._entries))

    @property
    def total_duration_secs(self) -> float:
        return sum(e.duration_secs for e in self._entries)

    @property
    def total_characters(self) -> int:
        return sum(len(e.text) for e in self._entries)

    @property
    def session_count(self) -> int:
        return len(self._entries)
