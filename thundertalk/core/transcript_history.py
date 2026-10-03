"""Studio transcripts on disk, with a lightweight paginated search index."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from thundertalk.core.transcribe import Segment, Transcript


class TranscriptHistory:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.home() / ".thundertalk" / "transcripts"
        self.root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, title TEXT, "
                       "source TEXT, date TEXT, duration REAL, model TEXT, text TEXT)")
            db.execute("CREATE INDEX IF NOT EXISTS entries_date ON entries(date DESC, id DESC)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.root / "index.sqlite3")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _entry_dir(self, entry_id: str) -> Path:
        # IDs never come from a user-supplied title or source path.
        if len(entry_id) != 32 or any(c not in "0123456789abcdef" for c in entry_id):
            raise ValueError("Invalid transcript ID")
        return self.root / entry_id

    def save(self, tr: Transcript, source: str = "") -> str:
        entry_id = tr.history_id or uuid.uuid4().hex
        folder = self._entry_dir(entry_id)
        folder.mkdir(exist_ok=True)
        meta_path = folder / "metadata.json"
        old = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        source = source or old.get("source", "") or tr.source_url
        title = tr.title or old.get("title") or (Path(source).stem if source else "Transcript")
        date = old.get("date") or datetime.now(timezone.utc).isoformat()
        tr.history_id, tr.title = entry_id, title
        meta = dict(id=entry_id, title=title, source=source, source_url=tr.source_url,
                    duration=tr.duration, model=tr.model_id or tr.engine, date=date,
                    speaker_names=tr.speaker_names, notes=tr.notes)
        for path, data in ((folder / "transcript.json", asdict(tr)), (meta_path, meta)):
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (entry_id, title, source, date, tr.duration, tr.model_id or tr.engine, tr.to_text()))
        return entry_id

    def list(self, query: str = "", offset: int = 0, limit: int = 50) -> list[dict]:
        # Escape LIKE metacharacters: search is literal, including '%' and '_'.
        query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        select = "SELECT id, title, source, date, duration, model FROM entries "
        params = []
        if query:
            select += "WHERE title LIKE ? ESCAPE '\\' OR text LIKE ? ESCAPE '\\' "
            params.extend((f"%{query}%", f"%{query}%"))
        with self._db() as db:
            rows = db.execute(select + "ORDER BY date DESC, id DESC LIMIT ? OFFSET ?",
                              (*params, limit, offset)).fetchall()
        return [dict(row) for row in rows]

    def load(self, entry_id: str) -> tuple[Transcript, dict]:
        folder = self._entry_dir(entry_id)
        data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
        # Ignore fields written by a newer version.
        known = {f.name for f in fields(Transcript)}
        data = {k: v for k, v in data.items() if k in known}
        seg_known = {f.name for f in fields(Segment)}
        data["segments"] = [Segment(**{k: v for k, v in s.items() if k in seg_known}) for s in data["segments"]]
        return Transcript(**data), json.loads((folder / "metadata.json").read_text(encoding="utf-8"))

    def rename(self, entry_id: str, title: str) -> None:
        if title.strip():
            tr, meta = self.load(entry_id)
            tr.title = title.strip()
            self.save(tr, meta["source"])

    def delete(self, entry_id: str) -> None:
        folder = self._entry_dir(entry_id)
        if folder.exists():
            shutil.rmtree(folder)
        with self._db() as db:
            db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
