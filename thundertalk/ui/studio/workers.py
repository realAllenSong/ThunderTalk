"""Background jobs for the Studio. Each worker is a QThread with one result
signal; errors arrive as a stable code or a plain message the page can map to
a sentence in the user's language."""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np
from PySide6.QtCore import QThread, Signal

from thundertalk.core import audio_io, transcribe, tts, tts_verify
from thundertalk.core.models import DownloadCancelled, download_repo


def error_code(exc: BaseException) -> str:
    """Map an exception to a short code the UI translates (or the raw text)."""
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
        except (transcribe.TranscribeCancelled, tts.TtsCancelled, DownloadCancelled):
            self.cancelled.emit()
            return
        except BaseException as exc:      # noqa: BLE001 - surfaced to the UI
            import traceback
            traceback.print_exc()
            self.error.emit(error_code(exc))
            return
        self.done.emit(result)


class TranscribeWorker(_Worker):
    def __init__(self, path: str, engine, speakers: bool) -> None:
        super().__init__()
        self._path, self._engine, self._speakers = path, engine, speakers

    def work(self):
        return transcribe.transcribe_file(
            self._path, self._engine, speakers=self._speakers,
            progress=lambda p, m: self.progress.emit(p, m), cancel=self._cancel)


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
