"""Persistence and indexed search never load unrelated transcripts."""

import json

import pytest

from thundertalk.core.transcribe import Segment, Transcript
from thundertalk.core.transcript_history import TranscriptHistory


def test_history_metadata_search_and_pagination(tmp_path, monkeypatch):
    store = TranscriptHistory(tmp_path)
    for i in range(115):
        store.save(Transcript([Segment(0, 2, f"discussion {i}")], 2, "ASR", title=f"Meeting {i}",
                              model_id="test-model", notes="A note"), f"/recordings/{i}.wav")
    rows = store.list()
    assert len(rows) == 50 and len(store.list(offset=100)) == 15
    entry = rows[0]
    meta = json.loads((tmp_path / entry['id'] / 'metadata.json').read_text())
    assert meta['model'] == 'test-model' and meta['notes'] == 'A note' and meta['date']
    monkeypatch.setattr(store, "load", lambda *a: pytest.fail("Search eagerly loaded transcript"))
    assert len(store.list("discussion 114")) == 1
    assert len(store.list("Meeting 114")) == 1
    assert store.list("%") == []
    store.delete(entry['id'])
    assert not (tmp_path / entry['id']).exists()
    with pytest.raises(ValueError):
        store.delete("../models")


def test_newer_fields_and_missing_folder_do_not_break_history(tmp_path):
    store = TranscriptHistory(tmp_path)
    entry_id = store.save(Transcript([Segment(0, 1, "Hi")], 1, "ASR"), "/a.wav")
    path = tmp_path / entry_id / "transcript.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["future_field"] = 1
    data["segments"][0]["future_seg"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.load(entry_id)[0].to_text() == "Hi"
    import shutil
    shutil.rmtree(tmp_path / entry_id)
    store.delete(entry_id)
    assert store.list() == []


def test_batch_history_failure_keeps_the_item(qapp, tmp_path, monkeypatch):
    from thundertalk.core import transcribe as tr
    from thundertalk.ui.studio.workers import BatchItem, BatchWorker
    monkeypatch.setattr(tr, "transcribe_file", lambda *a, **k: Transcript([Segment(0, 1, "Hi")], 1, "ASR"))
    class Broken:
        def save(self, *a):
            raise OSError("disk full")
    w = BatchWorker([BatchItem(str(tmp_path / "a.wav"))], None, False, ["txt"], str(tmp_path), history=Broken())
    assert w.work() == 1 and (tmp_path / "a.txt").is_file()


def test_round_trip_preserves_all_transcript_fields(tmp_path):
    store = TranscriptHistory(tmp_path)
    tr = Transcript([Segment(.1, 2, "Hello", "S01")], 2, "MOSS", .5, True,
                    {"S01": "Host"}, "Meeting", "https://example.com/v", 10, "Notes")
    entry_id = store.save(tr, tr.source_url)
    restored, meta = TranscriptHistory(tmp_path).load(entry_id)
    assert restored == tr and restored.is_partial
    assert meta['source'] == tr.source_url
    store.rename(entry_id, "New title")
    assert store.load(entry_id)[0].title == "New title"
    assert store.load(entry_id)[1]['date'] == meta['date']
