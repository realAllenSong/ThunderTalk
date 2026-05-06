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
    assert any('"kind": "entry"' in line and '"text"' not in line for line in skipped_lines)


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
