"""Background jobs for the Studio. Each worker is a QThread with one result
signal; errors arrive as a stable code or a plain message the page can map to
a sentence in the user's language."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from thundertalk.core import audio_io, burn, links, transcribe, tts, tts_verify
from thundertalk.core.models import DownloadCancelled, download_repo


def error_code(exc: BaseException) -> str:
    """Map an exception to a short code the UI translates (or the raw text)."""
    if isinstance(exc, links.LinkError):
        return f"link:{exc.code}:{exc.detail}"
    if isinstance(exc, burn.BurnError):
        return f"burn:{exc.code}:{exc.detail}"
    if isinstance(exc, audio_io.AudioDecodeError):
        return f"decode:{exc}"
    if isinstance(exc, tts.TtsModelMissing):
        return "tts_missing"
    if isinstance(exc, RuntimeError) and str(exc) in ("no_model", "no_speech"):
        return str(exc)
    if isinstance(exc, MemoryError):
        return "memory"
    return f"other:{exc}"


def friendly_error(code: str) -> str:
    """A sentence in the user's language for an ``error_code``."""
    from thundertalk.core.i18n import t
    if code in ("no_speech", "no_model", "tts_missing", "memory"):
        return t(f"studio.err.{code}")
    for kind in ("link", "burn"):
        if code.startswith(kind + ":"):
            sub, _, detail = code[len(kind) + 1:].partition(":")
            if sub == "other" or sub == "failed":
                return t(f"studio.{kind}.err.{sub}").format(msg=detail or "?")
            return t(f"studio.{kind}.err.{sub}")
    if code.startswith("decode:"):
        return t("studio.err.decode").format(msg=code[7:])
    return t("studio.err.other").format(msg=code[6:] if code.startswith("other:") else code)


class _Worker(QThread):
    progress = Signal(int, str)          # percent (-1 = unknown), message key/text
    done = Signal(object)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel

    def cancel(self) -> None:
        self._cancel.set()

    def work(self):                      # pragma: no cover - overridden
        raise NotImplementedError

    def run(self) -> None:
        try:
            result = self.work()
        except (transcribe.TranscribeCancelled, tts.TtsCancelled, DownloadCancelled,
                links.LinkCancelled, burn.BurnCancelled):
            self.cancelled.emit()
            return
        except BaseException as exc:      # noqa: BLE001 - surfaced to the UI
            import traceback
            traceback.print_exc()
            self.error.emit(error_code(exc))
            return
        self.done.emit(result)


class TranscribeWorker(_Worker):
    """One file, or one web link (``url``: downloaded first, then transcribed)."""

    titled = Signal(str)                  # the link's title, once known

    def __init__(self, path: str, engine, speakers: bool, url: str = "") -> None:
        super().__init__()
        self._path, self._engine, self._speakers, self._url = path, engine, speakers, url

    def work(self):
        prog = lambda p, m: self.progress.emit(p, m)      # noqa: E731
        if self._url:
            return transcribe.transcribe_link(self._url, self._engine, speakers=self._speakers,
                                              progress=prog, cancel=self._cancel, on_title=self.titled.emit)
        return transcribe.transcribe_file(self._path, self._engine, speakers=self._speakers,
                                          progress=prog, cancel=self._cancel)


@dataclass
class BatchItem:
    source: str                           # file path or URL
    is_url: bool = False


class BatchWorker(_Worker):
    """Transcribe a queue strictly one item at a time and save each result.

    ``out_dir`` empty = next to each source file (links go to ``link_dir``).
    Items can be appended while it runs; ``cancel_item`` skips one, ``cancel``
    stops the current item and everything after it."""

    item_started = Signal(int)
    item_progress = Signal(int, int, str)          # index, percent, message
    item_titled = Signal(int, str)
    item_done = Signal(int, object, object)        # index, Transcript, saved paths
    item_failed = Signal(int, str)                 # index, error code
    item_cancelled = Signal(int)

    def __init__(self, items: list[BatchItem], engine, speakers: bool, formats: list[str],
                 out_dir: str = "", link_dir: str = "") -> None:
        super().__init__()
        self._items = list(items)
        self._lock = threading.Lock()
        self._engine, self._speakers, self._formats = engine, speakers, list(formats)
        self._out_dir = out_dir
        self._link_dir = link_dir or str(Path.home() / "Downloads")
        self._skip: set[int] = set()
        self._current = -1
        self._item_cancel = threading.Event()

    def add(self, item: BatchItem) -> int:
        with self._lock:
            self._items.append(item)
            return len(self._items) - 1

    def cancel_item(self, index: int) -> None:
        if index == self._current:
            self._item_cancel.set()
        else:
            self._skip.add(index)

    def cancel(self) -> None:
        super().cancel()
        self._item_cancel.set()

    def _next(self, i: int) -> Optional[BatchItem]:
        with self._lock:
            return self._items[i] if i < len(self._items) else None

    def work(self):
        i, ok = 0, 0
        while (item := self._next(i)) is not None:
            if self._cancel.is_set() or i in self._skip:
                self.item_cancelled.emit(i)
                i += 1
                continue
            self._current, self._item_cancel = i, threading.Event()
            if self._cancel.is_set():
                self._item_cancel.set()
            self.item_started.emit(i)
            try:
                tr = self._one(i, item)
                stem = (links.safe_name(tr.title) if item.is_url else Path(item.source).stem)
                folder = self._out_dir or (self._link_dir if item.is_url else str(Path(item.source).parent))
                paths = transcribe.save_outputs(tr, folder, stem, self._formats)
            except (transcribe.TranscribeCancelled, links.LinkCancelled):
                self.item_cancelled.emit(i)
            except Exception as exc:      # noqa: BLE001 - one bad item must not stop the queue
                self.item_failed.emit(i, error_code(exc))
            else:
                ok += 1
                self.item_done.emit(i, tr, paths)
            i += 1
        self._current = -1
        return ok

    def _one(self, i: int, item: BatchItem) -> transcribe.Transcript:
        prog = lambda p, m: self.item_progress.emit(i, p, m)      # noqa: E731
        if item.is_url:
            return transcribe.transcribe_link(item.source, self._engine, speakers=self._speakers,
                                              progress=prog, cancel=self._item_cancel,
                                              on_title=lambda s: self.item_titled.emit(i, s))
        return transcribe.transcribe_file(item.source, self._engine, speakers=self._speakers,
                                          progress=prog, cancel=self._item_cancel)


class BurnWorker(_Worker):
    def __init__(self, src: str, tr: transcribe.Transcript, out: str, soft: bool) -> None:
        super().__init__()
        self._a = (src, tr, out, soft)

    def work(self):
        src, tr, out, soft = self._a
        return burn.burn(src, tr, out, soft=soft, progress=lambda p, m: self.progress.emit(p, m),
                         cancel=self._cancel)


class SynthWorker(_Worker):
    step = Signal(int, int)              # finished pieces, total pieces

    def __init__(self, text: str, voice, language: Optional[str], speed: float,
                 asr_engine=None, clone_backend: Optional[str] = None) -> None:
        super().__init__()
        self._a = (text, voice, language, speed, clone_backend)
        self._asr = asr_engine            # reads each piece back to catch skipped/garbled text

    def work(self):
        from thundertalk.core import speech
        text, voice, language, speed, clone_backend = self._a
        last = [-1]

        def prog(i: int, n: int, _seg: str) -> None:
            if i != last[0]:
                last[0] = i
                self.step.emit(i, n)

        return speech.get_engine().synthesize(text, voice, language=language, speed=speed,
                                              progress=prog, cancel=self._cancel,
                                              verifier=tts_verify.make_verifier(self._asr),
                                              clone_backend=clone_backend)


class BackendDownloadWorker(_Worker):
    """Fetch everything a speech backend needs (HF repos and/or archives)."""

    def __init__(self, info) -> None:
        super().__init__()
        self._info = info

    def work(self):
        from thundertalk.core import speech
        speech.download_backend(self._info, lambda p, m: self.progress.emit(p, m), self._cancel)
        return self._info.id


class RepoDownloadWorker(_Worker):
    def __init__(self, repo: str, ready=None) -> None:
        super().__init__()
        self._repo, self._ready = repo, ready

    def work(self):
        download_repo(self._repo, self._ready, lambda p, m: self.progress.emit(p, m), self._cancel)
        return self._repo


class RefTranscribeWorker(_Worker):
    """Transcribe a cloning reference with the dictation model so the user only
    has to confirm the words instead of typing them."""

    def __init__(self, audio_16k: np.ndarray, engine) -> None:
        super().__init__()
        self._x, self._engine = audio_16k, engine

    def work(self):
        from thundertalk.core.gpu_lock import GPU_LOCK
        if self._engine is None or not getattr(self._engine, "is_loaded", False):
            raise RuntimeError("no_model")
        with GPU_LOCK:
            r = self._engine.recognize(self._x, 16000)
        return (r.text or "").strip()
