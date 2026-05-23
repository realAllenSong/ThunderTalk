"""Append-only transcription history at ~/.thundertalk/history.jsonl."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_DIR = Path.home() / ".thundertalk"
_JSONL_PATH = _DIR / "history.jsonl"
_LEGACY_PATH = _DIR / "history.json"


def _generate_id() -> str:
    """Stable, sortable, collision-free per-entry id.
    Format: ``e{unix_int}.{8 hex chars}`` — sorts ~chronologically and
    includes 32 bits of randomness for the rare same-second case."""
    return f"e{int(time.time())}.{secrets.token_hex(4)}"


@dataclass
class HistoryEntry:
    id: str
    text: str
    timestamp: float
    duration_secs: float
    inference_ms: int
    model: str
    # Optional translation (Review-mode result). Empty string when no
    # translation was produced for this entry.
    translation: str = ""
    translation_lang: str = ""


def _maybe_migrate_legacy() -> None:
    """One-shot v1.1.13 → v1.1.14 conversion.

    If ``history.json`` (the old JSON-array file) exists and
    ``history.jsonl`` does NOT, read the array and write each entry as
    a JSONL line. Then rename the source file to
    ``history.json.migrated-{ts}`` so its bytes survive — we don't
    delete the user's history under any circumstance.

    If the source can't be parsed at all, we still rename it (so a fresh
    JSONL can be written by future ``add()`` calls without conflict) but
    we don't write an empty JSONL — first ``add()`` will create one.
    """
    if _JSONL_PATH.exists():
        return  # Already migrated, or this is a fresh JSONL install.
    if not _LEGACY_PATH.exists():
        return  # Fresh install with no prior history.

    parsed: "list | None" = None
    try:
        raw_text = _LEGACY_PATH.read_text(encoding="utf-8")
        loaded = json.loads(raw_text)
        if isinstance(loaded, list):
            parsed = loaded
    except (OSError, json.JSONDecodeError):
        pass

    if parsed is not None:
        _DIR.mkdir(parents=True, exist_ok=True)
        with open(_JSONL_PATH, "a", encoding="utf-8") as f:
            for old in parsed:
                if not isinstance(old, dict) or "text" not in old:
                    continue  # skip junk entries silently — source is preserved
                rec = {
                    "v": 1,
                    "kind": "entry",
                    "id": _generate_id(),
                    "text": old.get("text", ""),
                    "timestamp": float(old.get("timestamp", 0.0)),
                    "duration_secs": float(old.get("duration_secs", 0.0)),
                    "inference_ms": int(old.get("inference_ms", 0)),
                    "model": old.get("model", ""),
                    "translation": old.get("translation", ""),
                    "translation_lang": old.get("translation_lang", ""),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    archived = _DIR / f"history.json.migrated-{int(time.time())}"
    try:
        os.replace(_LEGACY_PATH, archived)
        print(
            f"[History] migrated legacy {_LEGACY_PATH.name} → {_JSONL_PATH.name}; "
            f"source preserved as {archived.name}."
        )
    except OSError as exc:
        print(
            f"[History] migration: source rename failed: {exc!r}; "
            "legacy file left in place."
        )


class HistoryStore:
    """Append-only transcription log.

    Invariants:
      1. Once an entry is fsync'd to ``history.jsonl`` it is never
         overwritten or removed, except by an explicit user-confirmed
         ``clear()`` which archives the file rather than deleting it.
      2. A corrupt or unparseable line never invalidates the rest of
         the file; bad lines are copied to ``history.skipped-{ts}.jsonl``
         and ignored on load.
      3. There is no ``save()`` API. The bug class "in-memory state
         leaks to disk and shrinks the file" cannot exist by design.
    """

    def __init__(self) -> None:
        self._entries: list[HistoryEntry] = []
        _maybe_migrate_legacy()
        self.load()

    def load(self) -> None:
        """Stream-parse ``history.jsonl``. Skipped lines go to a sidecar."""
        self._entries = []
        if not _JSONL_PATH.exists():
            return

        entries_by_id: dict[str, HistoryEntry] = {}
        order: list[str] = []
        skipped: list[str] = []

        with open(_JSONL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if not stripped.strip():
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    skipped.append(stripped)
                    continue

                kind = rec.get("kind")
                if kind == "entry":
                    try:
                        e = HistoryEntry(
                            id=rec["id"],
                            text=rec["text"],
                            timestamp=rec["timestamp"],
                            duration_secs=rec["duration_secs"],
                            inference_ms=rec["inference_ms"],
                            model=rec["model"],
                            translation=rec.get("translation", ""),
                            translation_lang=rec.get("translation_lang", ""),
                        )
                    except (KeyError, TypeError):
                        skipped.append(stripped)
                        continue
                    if e.id not in entries_by_id:
                        entries_by_id[e.id] = e
                        order.append(e.id)
                    else:
                        # Duplicate id (shouldn't happen, but: keep first,
                        # treat second as skipped so the bytes survive).
                        skipped.append(stripped)
                elif kind == "translate":
                    eid = rec.get("id")
                    if eid and eid in entries_by_id:
                        entries_by_id[eid].translation = rec.get("translation", "")
                        entries_by_id[eid].translation_lang = rec.get("translation_lang", "")
                    else:
                        skipped.append(stripped)
                else:
                    skipped.append(stripped)

        self._entries = [entries_by_id[i] for i in order]

        if skipped:
            sidecar = _DIR / f"history.skipped-{int(time.time())}.jsonl"
            try:
                with open(sidecar, "w", encoding="utf-8") as f:
                    f.write("\n".join(skipped) + "\n")
                print(
                    f"[History] {len(skipped)} unparseable line(s) in "
                    f"{_JSONL_PATH.name}; preserved at {sidecar.name}."
                )
            except OSError as exc:
                print(f"[History] could not write skipped sidecar: {exc}")

    def _append_record(self, rec: dict) -> None:
        """Atomic single-line append. Caller is responsible for the schema.

        Open in append mode, write one line, flush + fsync, close. On macOS
        APFS this is durable across power loss for the bytes that returned
        from fsync — exactly what the append invariant requires.
        """
        _DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(_JSONL_PATH, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def add(
        self,
        text: str,
        duration_secs: float,
        inference_ms: int,
        model: str,
        translation: str = "",
        translation_lang: str = "",
    ) -> None:
        entry = HistoryEntry(
            id=_generate_id(),
            text=text,
            timestamp=time.time(),
            duration_secs=duration_secs,
            inference_ms=inference_ms,
            model=model,
            translation=translation,
            translation_lang=translation_lang,
        )
        self._append_record({
            "v": 1,
            "kind": "entry",
            "id": entry.id,
            "text": entry.text,
            "timestamp": entry.timestamp,
            "duration_secs": entry.duration_secs,
            "inference_ms": entry.inference_ms,
            "model": entry.model,
            "translation": entry.translation,
            "translation_lang": entry.translation_lang,
        })
        self._entries.append(entry)

    def update_translation(
        self,
        original_text: str,
        translation: str,
        translation_lang: str,
    ) -> None:
        """Backfill a translation onto the most recent matching entry.

        Persisted as a separate ``translate`` event line — the entry line
        itself is never rewritten."""
        target: "HistoryEntry | None" = None
        for entry in reversed(self._entries):
            if entry.text == original_text and not entry.translation:
                target = entry
                break
        if target is None:
            return
        target.translation = translation
        target.translation_lang = translation_lang
        self._append_record({
            "v": 1,
            "kind": "translate",
            "id": target.id,
            "translation": translation,
            "translation_lang": translation_lang,
        })

    def clear(self) -> None:
        """User-confirmed clear. Archive the current file rather than
        delete it; the next ``add()`` opens a fresh ``history.jsonl``.

        Caller (UI) MUST have shown a destructive-action confirm dialog
        before reaching this method. The store does not double-prompt."""
        self._entries.clear()
        if _JSONL_PATH.exists():
            archive = _DIR / f"history.archived-{int(time.time())}.jsonl"
            try:
                os.replace(_JSONL_PATH, archive)
                print(f"[History] cleared; previous file archived as {archive.name}")
            except OSError as exc:
                # If we can't rename, do NOT fall through to a destructive
                # alternative — leave the file in place so the user keeps
                # their data, even though the in-memory view is empty.
                print(f"[History] clear: archive rename failed: {exc!r}; "
                      "live file left untouched.")

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
