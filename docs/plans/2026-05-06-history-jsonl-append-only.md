# History append-only (JSONL) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move transcription history from a rewritten JSON array (`history.json`) to a true append-only JSON Lines file (`history.jsonl`), so once an entry is on disk it cannot be wiped by a parse error, schema drift, mid-write crash, or any future bug short of an explicit user-confirmed clear.

**Architecture:** One JSONL file in `~/.thundertalk/`. Every record is a single line of JSON with a `kind` discriminator. Two kinds: `entry` (a transcription) and `translate` (an event that backfills a translation onto an earlier entry by `id`). Writes are `open("a")` + `fsync` and never overwrite existing bytes; the `save()` API is deleted entirely. `load()` is line-resilient — a corrupt line is logged to `history.skipped-{ts}.jsonl` and the rest of the file is still recovered. `clear()` (only reachable behind the existing two-step UI confirm) renames the live file to `history.archived-{ts}.jsonl` and starts a fresh empty one. A one-shot migration on first v1.1.14 launch converts any existing `history.json` (JSON array) to `history.jsonl` and renames the source to `history.json.migrated-{ts}` so the original bytes are preserved.

**Tech Stack:** Python 3.12 stdlib only (`json`, `os`, `secrets`, `pathlib`); no new deps. Existing `HistoryStore` API used by `app.py`, `home_page.py`, `main_window.py` stays compatible.

**Out of scope:** UI changes (the Clear button keeps its existing two-step confirm dialog); cloud sync; encryption at rest; settings.json (different concern). The orphan `~/.thundertalk/thundertalk.db` from v0.x is left untouched — it's already not loaded; we don't risk touching it.

---

## Task 0: Pre-flight

**Step 1: Confirm clean working tree on `main`**

Run: `git status --porcelain`
Expected: empty (last commit is v1.1.13).

**Step 2: Capture the user's current history file before any code change**

Run:
```bash
cp -p ~/.thundertalk/history.json ~/.thundertalk/history.json.preplan-$(date +%s)
ls -la ~/.thundertalk/
```
Expected: a `.preplan-*` copy alongside the live file. Cheap belt-and-braces — if the migration code in Task 4 has a bug we don't detect, the user's bytes are still here.

---

## Task 1: Add `id` field to `HistoryEntry` + ID generator

**Files:**
- Modify: `thundertalk/core/history.py` (the dataclass + a small helper)
- Test: `tests/test_history.py` (new file — none exists today)

**Step 1: Write the failing tests**

```python
# tests/test_history.py
"""Tests for HistoryStore — append-only invariant + format details."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from thundertalk.core import history as hist_mod
from thundertalk.core.history import HistoryEntry, HistoryStore


@pytest.fixture
def tmp_history(tmp_path, monkeypatch):
    """Redirect history storage into tmp_path so tests don't touch ~/.thundertalk."""
    monkeypatch.setattr(hist_mod, "_DIR", tmp_path)
    monkeypatch.setattr(hist_mod, "_JSONL_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(hist_mod, "_LEGACY_PATH", tmp_path / "history.json")
    return tmp_path


def test_history_entry_has_id_field() -> None:
    e = HistoryEntry(
        id="e123.abcd",
        text="hi",
        timestamp=1.0,
        duration_secs=0.5,
        inference_ms=100,
        model="m",
    )
    assert e.id == "e123.abcd"


def test_generate_id_is_unique_and_sortable() -> None:
    ids = {hist_mod._generate_id() for _ in range(1000)}
    assert len(ids) == 1000  # no collisions
    # ids start with "e" + integer timestamp → lexicographic ~ chronological
    assert all(s.startswith("e") for s in ids)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: ImportError on `_DIR` / `_JSONL_PATH` / `_LEGACY_PATH` / `_generate_id` (none exist yet) AND TypeError on `HistoryEntry(id=...)` (no such field).

**Step 3: Add the field and helper**

Edit `thundertalk/core/history.py`:

- Replace the module-level constants
  ```python
  _PATH = Path.home() / ".thundertalk" / "history.json"
  _MAX_ENTRIES = 1000
  ```
  with
  ```python
  _DIR = Path.home() / ".thundertalk"
  _JSONL_PATH = _DIR / "history.jsonl"
  _LEGACY_PATH = _DIR / "history.json"
  _MAX_ENTRIES = 1000
  ```
- Add at module top:
  ```python
  import secrets
  ```
- Add helper near the top:
  ```python
  def _generate_id() -> str:
      """Stable, sortable, collision-free per-entry id.
      Format: ``e{unix_int}.{8 hex chars}`` — sorts ~chronologically and
      includes 32 bits of randomness for the rare same-second case."""
      return f"e{int(time.time())}.{secrets.token_hex(4)}"
  ```
- Add `id: str` as the first field of `HistoryEntry`:
  ```python
  @dataclass
  class HistoryEntry:
      id: str
      text: str
      timestamp: float
      duration_secs: float
      inference_ms: int
      model: str
      translation: str = ""
      translation_lang: str = ""
  ```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: 2 passed.

---

## Task 2: JSONL append-only `add()` + `_append_record()` + line-resilient `load()`

**Files:**
- Modify: `thundertalk/core/history.py` (rewrite `HistoryStore.load`, `add`, delete `save`)
- Test: `tests/test_history.py` (extend)

This task replaces the "rewrite the whole file" mutation pattern with append-only semantics.

**Step 1: Write the failing tests**

Append to `tests/test_history.py`:

```python
def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_add_appends_one_line_per_call(tmp_history) -> None:
    s = HistoryStore()
    s.add(text="hello", duration_secs=1.0, inference_ms=100, model="m")
    s.add(text="world", duration_secs=1.5, inference_ms=120, model="m")

    lines = _read_jsonl(hist_mod._JSONL_PATH)
    assert len(lines) == 2
    assert all(rec["kind"] == "entry" for rec in lines)
    assert [rec["text"] for rec in lines] == ["hello", "world"]
    assert lines[0]["id"] != lines[1]["id"]


def test_add_then_load_in_new_store_recovers_entries(tmp_history) -> None:
    s1 = HistoryStore()
    s1.add(text="a", duration_secs=1.0, inference_ms=100, model="m")
    s1.add(text="b", duration_secs=1.0, inference_ms=100, model="m")
    del s1

    s2 = HistoryStore()
    assert [e.text for e in s2._entries] == ["a", "b"]


def test_load_skips_corrupt_lines_and_keeps_good_ones(tmp_history) -> None:
    """A single bad line must not poison the rest. The bad line goes to a
    sidecar file so the user can recover manually."""
    good = json.dumps({
        "v": 1, "kind": "entry", "id": "e1.aaaa",
        "text": "good", "timestamp": 1.0, "duration_secs": 1.0,
        "inference_ms": 100, "model": "m",
        "translation": "", "translation_lang": "",
    })
    hist_mod._JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist_mod._JSONL_PATH.write_text(
        good + "\n"
        + "this is not json at all\n"
        + json.dumps({"v": 1, "kind": "entry"}) + "\n"  # missing required fields
        + good.replace('"e1.aaaa"', '"e2.bbbb"').replace('"good"', '"after"') + "\n"
    )

    s = HistoryStore()
    assert [e.text for e in s._entries] == ["good", "after"]

    skipped_files = list(hist_mod._DIR.glob("history.skipped-*.jsonl"))
    assert len(skipped_files) == 1
    skipped_lines = skipped_files[0].read_text().splitlines()
    assert "this is not json at all" in skipped_lines
    assert any('"kind": "entry"' in s and '"text"' not in s for s in skipped_lines)


def test_save_method_does_not_exist(tmp_history) -> None:
    """The whole-file rewrite API must be gone — that's the bug class
    we're closing. Any caller still trying to use it will fail loudly
    in a single test run instead of silently wiping production data."""
    s = HistoryStore()
    assert not hasattr(s, "save")


def test_existing_jsonl_is_never_truncated_by_add(tmp_history) -> None:
    """The append invariant — the file size after add() is strictly
    larger than before, byte-for-byte. No rewrite, no shrink."""
    s = HistoryStore()
    s.add(text="x", duration_secs=1.0, inference_ms=100, model="m")
    size_before = hist_mod._JSONL_PATH.stat().st_size

    s.add(text="y", duration_secs=1.0, inference_ms=100, model="m")
    size_after = hist_mod._JSONL_PATH.stat().st_size

    assert size_after > size_before


def test_max_entries_only_caps_in_memory_view(tmp_history) -> None:
    """In-memory cap protects UI/stat performance without ever
    truncating disk. Disk grows monotonically."""
    s = HistoryStore()
    for i in range(hist_mod._MAX_ENTRIES + 5):
        s.add(text=f"e{i}", duration_secs=0.1, inference_ms=10, model="m")

    # In-memory list is capped (existing v1.1.13 behaviour kept)
    assert len(s._entries) == hist_mod._MAX_ENTRIES
    # ...but every single entry is on disk.
    assert len(_read_jsonl(hist_mod._JSONL_PATH)) == hist_mod._MAX_ENTRIES + 5
```

**Step 2: Run tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: 5–6 failures (existing `save` still present, no `_append_record`, no skipped sidecar, etc.).

**Step 3: Implement the new `HistoryStore`**

Replace the body of `class HistoryStore` (everything from `def __init__` to the end of the class) with:

```python
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
        # In-memory view cap; disk is never trimmed.
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

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

        We open in append mode, write one line, flush + fsync, then close.
        On macOS APFS this is durable across power loss for the bytes that
        returned from fsync, which is what we need for the append invariant.
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
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

    def update_translation(
        self,
        original_text: str,
        translation: str,
        translation_lang: str,
    ) -> None:
        """Backfill a translation onto the most recent matching entry.

        Persisted as a separate ``translate`` event line — the entry line
        itself is never rewritten."""
        target: Optional[HistoryEntry] = None
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
```

Also delete the now-unused module docstring reference to `history.json` and replace the top docstring:

```python
"""Append-only transcription history at ~/.thundertalk/history.jsonl."""
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: 8 passed (2 from Task 1 + 6 new).

**Step 5: Run full suite, no regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: previous 142 tests still passing (136 prior + 6 new); new total 142.

---

## Task 3: Translation event + clear archiving

**Files:**
- Test only: `tests/test_history.py` (extend — code already in place from Task 2)

**Step 1: Write the failing tests** (will pass on Task 2 code; goal is locking in behaviour)

```python
def test_update_translation_appends_event_does_not_rewrite_entry(tmp_history) -> None:
    s = HistoryStore()
    s.add(text="bonjour", duration_secs=1.0, inference_ms=100, model="m")
    size_after_entry = hist_mod._JSONL_PATH.stat().st_size

    s.update_translation("bonjour", "hello", "eng")
    size_after_event = hist_mod._JSONL_PATH.stat().st_size

    assert size_after_event > size_after_entry  # event was appended

    lines = _read_jsonl(hist_mod._JSONL_PATH)
    assert len(lines) == 2
    assert lines[0]["kind"] == "entry"
    assert lines[0].get("translation") == ""  # original line UNCHANGED
    assert lines[1] == {
        "v": 1, "kind": "translate", "id": lines[0]["id"],
        "translation": "hello", "translation_lang": "eng",
    }


def test_translation_event_replays_on_load(tmp_history) -> None:
    s1 = HistoryStore()
    s1.add(text="bonjour", duration_secs=1.0, inference_ms=100, model="m")
    s1.update_translation("bonjour", "hello", "eng")
    del s1

    s2 = HistoryStore()
    assert s2._entries[0].translation == "hello"
    assert s2._entries[0].translation_lang == "eng"


def test_translation_event_for_unknown_id_is_skipped_not_fatal(tmp_history) -> None:
    """A stale or hand-edited translate event must not break load()."""
    hist_mod._JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist_mod._JSONL_PATH.write_text(
        json.dumps({"v": 1, "kind": "translate", "id": "ghost",
                    "translation": "x", "translation_lang": "eng"}) + "\n"
    )
    s = HistoryStore()
    assert s._entries == []
    skipped = list(hist_mod._DIR.glob("history.skipped-*.jsonl"))
    assert len(skipped) == 1


def test_clear_archives_then_next_add_starts_fresh_file(tmp_history) -> None:
    s = HistoryStore()
    s.add(text="before-clear", duration_secs=1.0, inference_ms=100, model="m")
    s.clear()

    archives = list(hist_mod._DIR.glob("history.archived-*.jsonl"))
    assert len(archives) == 1
    assert "before-clear" in archives[0].read_text()

    # The live file may not exist yet (or be empty); add() recreates.
    s.add(text="after-clear", duration_secs=1.0, inference_ms=100, model="m")
    lines = _read_jsonl(hist_mod._JSONL_PATH)
    assert [rec["text"] for rec in lines] == ["after-clear"]
```

**Step 2: Run them**

Run: `.venv/bin/python -m pytest tests/test_history.py::test_update_translation_appends_event_does_not_rewrite_entry tests/test_history.py::test_translation_event_replays_on_load tests/test_history.py::test_translation_event_for_unknown_id_is_skipped_not_fatal tests/test_history.py::test_clear_archives_then_next_add_starts_fresh_file -v`
Expected: 4 passed.

---

## Task 4: One-time migration from legacy `history.json`

**Files:**
- Modify: `thundertalk/core/history.py` (add `_maybe_migrate_legacy()` and call it from `__init__` before `load()`)
- Test: `tests/test_history.py` (extend)

**Step 1: Write the failing test**

```python
def test_legacy_json_array_is_migrated_on_first_load(tmp_history) -> None:
    """v1.1.13 history.json (array form) should auto-convert into JSONL
    on first v1.1.14 launch, with the source preserved as a sidecar."""
    legacy = [
        {"text": "old1", "timestamp": 1.0, "duration_secs": 1.0,
         "inference_ms": 100, "model": "m"},
        {"text": "old2", "timestamp": 2.0, "duration_secs": 1.5,
         "inference_ms": 110, "model": "m",
         "translation": "alt", "translation_lang": "eng"},
    ]
    hist_mod._LEGACY_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist_mod._LEGACY_PATH.write_text(json.dumps(legacy))

    s = HistoryStore()

    # In-memory view recovered, including the translation
    assert [e.text for e in s._entries] == ["old1", "old2"]
    assert s._entries[1].translation == "alt"

    # New JSONL written
    assert hist_mod._JSONL_PATH.exists()
    lines = _read_jsonl(hist_mod._JSONL_PATH)
    assert [rec["text"] for rec in lines] == ["old1", "old2"]
    assert all("id" in rec for rec in lines)

    # Legacy file preserved (renamed, NOT deleted)
    assert not hist_mod._LEGACY_PATH.exists()
    survivors = list(hist_mod._DIR.glob("history.json.migrated-*"))
    assert len(survivors) == 1
    assert json.loads(survivors[0].read_text()) == legacy


def test_migration_skips_when_jsonl_already_exists(tmp_history) -> None:
    """Migration must run AT MOST ONCE — if a JSONL is already present
    we never touch the legacy file (it might be the user's manual backup)."""
    hist_mod._JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist_mod._JSONL_PATH.write_text(
        json.dumps({"v": 1, "kind": "entry", "id": "e1.aaaa",
                    "text": "fresh", "timestamp": 1.0,
                    "duration_secs": 1.0, "inference_ms": 100,
                    "model": "m", "translation": "",
                    "translation_lang": ""}) + "\n"
    )
    hist_mod._LEGACY_PATH.write_text("[]")

    s = HistoryStore()
    assert [e.text for e in s._entries] == ["fresh"]
    # Legacy file still there, untouched
    assert hist_mod._LEGACY_PATH.exists()
    assert hist_mod._LEGACY_PATH.read_text() == "[]"


def test_migration_with_corrupt_legacy_json_preserves_source(tmp_history) -> None:
    """A broken legacy file must NOT cause data loss — preserve it
    under a clearly named sidecar, start fresh JSONL empty."""
    hist_mod._LEGACY_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist_mod._LEGACY_PATH.write_text("{not json at all")

    s = HistoryStore()
    assert s._entries == []

    # Legacy still preserved (renamed) — never silently deleted.
    assert not hist_mod._LEGACY_PATH.exists()
    survivors = list(hist_mod._DIR.glob("history.json.migrated-*"))
    assert len(survivors) == 1
    assert survivors[0].read_text() == "{not json at all"
```

**Step 2: Run them and watch them fail**

Run: `.venv/bin/python -m pytest tests/test_history.py -v -k "migrat"`
Expected: 3 failures — `_maybe_migrate_legacy` doesn't exist yet.

**Step 3: Implement `_maybe_migrate_legacy()`**

Add to `history.py`, then call it from `__init__` before `load()`:

```python
def _maybe_migrate_legacy() -> None:
    """One-shot v1.1.13 -> v1.1.14 conversion.

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

    raw_text = ""
    parsed: Optional[list] = None
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
        print(f"[History] migrated legacy {_LEGACY_PATH.name} → {_JSONL_PATH.name}; "
              f"source preserved as {archived.name}.")
    except OSError as exc:
        print(f"[History] migration: source rename failed: {exc!r}; "
              "legacy file left in place.")
```

In `HistoryStore.__init__` change:

```python
def __init__(self) -> None:
    self._entries: list[HistoryEntry] = []
    _maybe_migrate_legacy()
    self.load()
```

**Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_history.py -v`
Expected: 11 passed.

**Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 145 passed (was 142 + 3 migration tests).

---

## Task 5: Manual integration smoke

**Step 1: Verify the running app still talks to history correctly**

A unit test can't easily prove the wiring still works end-to-end (Qt event loop, etc.). Run the app from source against a temp `~/.thundertalk` to confirm:

```bash
.venv/bin/python - <<'PY'
"""Manual smoke: import the lifecycle as app.py does, add fake entries,
re-import in a fresh process, confirm round-trip."""
import os, tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp(prefix="hist_smoke_"))
os.environ["HOME"] = str(tmp)  # redirect ~/.thundertalk into tmp
from thundertalk.core.history import HistoryStore
s = HistoryStore()
s.add(text="hello", duration_secs=1.0, inference_ms=100, model="m")
s.add(text="bonjour", duration_secs=1.2, inference_ms=110, model="m")
s.update_translation("bonjour", "hello-fr", "fra")
print("entries:", [(e.text, e.translation) for e in s._entries])

# Verify: file exists and is append-only
import json, pathlib
p = tmp / ".thundertalk" / "history.jsonl"
print("on-disk lines:")
for ln in p.read_text().splitlines():
    print("  ", ln)
PY
```

Expected output: 2 entries listed, 3 on-disk lines (2 entries + 1 translate event), translations replayed correctly.

---

## Task 6: Bump version, build, release

Per the project's existing release flow (mirrored from v1.1.12 / v1.1.13):

**Step 1: Bump version**

Edit:
- `thundertalk/__init__.py`: `__version__ = "1.1.13"` → `"1.1.14"`
- `pyproject.toml`: `version = "1.1.13"` → `"1.1.14"`

**Step 2: Refresh uv.lock**

Run: `uv lock`
Expected: `Updated thundertalk v1.1.13 -> v1.1.14`.

**Step 3: Build**

Run: `.venv/bin/python build_macos.py 2>&1 | tail -10` (in background — ~2 min)
Expected: ends with `✅ Build + sign complete: dist/ThunderTalk.app`.

**Step 4: Zip**

Run:
```bash
cd dist && rm -f ThunderTalk-v1.1.14-macOS.zip \
  && ditto -c -k --keepParent ThunderTalk.app ThunderTalk-v1.1.14-macOS.zip \
  && ls -lh ThunderTalk-v1.1.14-macOS.zip
```

**Step 5: Confirm changes**

Run: `git status --porcelain`
Expected:
```
 M pyproject.toml
 M thundertalk/__init__.py
 M thundertalk/core/history.py
 M uv.lock
?? docs/plans/2026-05-06-history-jsonl-append-only.md
?? tests/test_history.py
```

**Step 6: Single release commit (project convention — no Co-Authored-By)**

Title: `v1.1.14: append-only history (JSONL) — entries can never be wiped by a parse error or schema drift`

Body should cover:
- The recurring history-loss complaint, traced to: load() can set `_entries = []` on any TypeError/JSONDecodeError → next `save()` writes 0 entries over N entries on disk.
- The v1.0-era `.broken` rename safeguard didn't help: rename can fail and is silently swallowed; a single corrupt entry in the JSON array invalidates the whole list-comprehension parse.
- Architectural fix: switch on-disk format to JSON Lines, append per record, never rewrite. Two record kinds (`entry` and `translate`) preserve translation backfill semantics without mutating earlier lines. Bad lines are sidelined to `history.skipped-*.jsonl`. The `save()` API is deleted entirely, so the bug class "in-memory state shrinks → disk shrinks" is structurally impossible.
- Migration: existing v1.1.13 `history.json` is auto-converted to `history.jsonl` on first launch; the source is renamed to `history.json.migrated-{ts}` (never deleted).
- Test coverage: append/load round-trip, corrupt-line resilience, no-truncate invariant, translation event replay, archive-then-fresh on clear, three migration paths (happy / already-migrated / corrupt source).

**Step 7: Tag and push**

```bash
git tag v1.1.14
git push origin main
git push origin v1.1.14
```

**Step 8: GitHub release**

```bash
gh release create v1.1.14 dist/ThunderTalk-v1.1.14-macOS.zip \
  --title "ThunderTalk v1.1.14" \
  --notes "<release-notes>"
```

Release notes (markdown), user-facing language:

> ## Fix: transcription history can no longer be wiped by app bugs
>
> A few users have reported their history disappearing across app updates
> or restarts. The cause was a fragile read-the-whole-file / write-the-whole-file
> design: any single corrupt entry, schema drift, or transient parse error
> reset history to empty in memory, and the next new transcription overwrote
> the on-disk file with just that one line.
>
> v1.1.14 switches transcription history to a true append-only log
> (`~/.thundertalk/history.jsonl`). New entries are appended one line at a
> time and the file is never rewritten. A corrupt line is set aside in
> `history.skipped-{timestamp}.jsonl` and the rest of the file still loads
> fine. Clearing history (when you press Clear and confirm) renames the
> file to `history.archived-{timestamp}.jsonl` rather than deleting it.
>
> Existing v1.1.13 history is auto-migrated on first launch; the original
> file is preserved as `history.json.migrated-{timestamp}`.
>
> Audit & recovery:
> - Your active history: `~/.thundertalk/history.jsonl`
> - Pre-v1.1.14 backup (auto): `~/.thundertalk/history.json.migrated-*`
> - Anything that failed to parse: `~/.thundertalk/history.skipped-*.jsonl`

**Step 9: Verify release is live**

Run: `gh release view v1.1.14 --json tagName,name,assets,url`
Expected: `ThunderTalk-v1.1.14-macOS.zip` listed, `state=uploaded`.

---

## Verification matrix

| Check | Command | Expected |
|---|---|---|
| New history tests | `.venv/bin/python -m pytest tests/test_history.py -v` | 11 passed |
| Full suite | `.venv/bin/python -m pytest -q` | 145 passed, 1 skipped |
| Manual round-trip smoke | (Task 5 inline script) | 2 entries, 3 disk lines |
| Migration on real `~/.thundertalk` | Launch v1.1.14 once, then `ls ~/.thundertalk/` | `history.jsonl` exists, `history.json.migrated-*` exists |
| Release asset | `gh release view v1.1.14 --json assets` | one zip uploaded |
| Local auto-update | Launch app, await updater popup | offers v1.1.14 |

---

## What this plan deliberately does NOT do

- **Does not delete the orphan `~/.thundertalk/thundertalk.db`** (v0.x SQLite leftover). It's not loaded; touching it is unrelated risk.
- **Does not add UI for managing skipped/archived sidecars.** Those files are user-facing breadcrumbs for recovery; surfacing them in Settings is a separate UX feature.
- **Does not introduce a "history backup" feature** (e.g., scheduled export to iCloud). Belongs in a future release; outside the bug-fix scope.
- **Does not change the in-memory `_MAX_ENTRIES = 1000` cap.** The on-disk file grows unbounded but stays cheap to append; the cap protects UI/stat performance only.
- **Does not change license / repo visibility.** That's a product decision, tracked separately from this technical fix.
