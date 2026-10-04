"""SeamlessM4T in a disposable process: torch's native heaps survive unload.

The parent owns GPU_LOCK for every RPC, so this worker shares the app's GPU
admission rules. CPU dictation remains independent. Spawn (never fork) and
freeze_support in __main__ also support the packaged macOS app.
"""
from __future__ import annotations

from functools import wraps
import multiprocessing
import os
import threading

from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.memory_policy import managed


def _serve(connection):
    from thundertalk.core.translate import TranslationEngine
    engine = TranslationEngine()
    try:
        while True:
            method, args, kwargs = connection.recv()
            try:
                result = getattr(engine, method)(*args, **kwargs)
                connection.send((True, result))
            except Exception as exc:
                connection.send((False, f"{type(exc).__name__}: {exc}"))
            finally:
                # A direct-translation request may contain minutes of audio.
                # Keep only weights between jobs, never the last request.
                del args, kwargs
    except (EOFError, BrokenPipeError):
        pass
    finally:
        engine.unload()
        connection.close()


def locked(fn):
    @wraps(fn)
    def call(self, *args, **kwargs):
        with GPU_LOCK, self._lock:
            return fn(self, *args, **kwargs)
    return call


class TranslationProcess:
    def __init__(self):
        self._source = None
        self._loaded = False
        self._process = None
        self._connection = None
        self._lock = threading.RLock()

    @property
    def pid(self):
        process = self._process
        try:
            return process.pid if process and process.is_alive() else None
        except ValueError:
            return None  # an idle sweep just closed this process handle

    @property
    def is_loaded(self):
        return self._loaded and self.pid is not None

    @property
    def can_translate(self):
        return self._source is not None

    @property
    def current_model(self):
        source = self._source
        return os.path.basename(source.rstrip("/")) if source and self.is_loaded else None

    @locked
    def prepare(self, source):
        if self._source != source:
            self.unload()
        self._source = source

    def _call(self, method, *args, **kwargs):
        if self.pid is None:
            self.unload(forget=False)
            ctx = multiprocessing.get_context("spawn")
            parent, child = ctx.Pipe()
            self._process = ctx.Process(target=_serve, args=(child,), name="translation", daemon=True)
            try:
                self._process.start()
            except Exception:
                parent.close()
                child.close()
                self._process.close()
                self._process = None
                raise
            child.close()
            self._connection = parent
        try:
            self._connection.send((method, args, kwargs))
            ok, result = self._connection.recv()
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.unload(forget=False)
            from thundertalk.core.i18n import t
            raise RuntimeError(t("models.translator.worker_stopped")) from exc
        if not ok:
            raise RuntimeError(result)
        return result

    @locked
    @managed(release="release_idle")
    def load_model(self, source):
        self.prepare(source)
        self._call("load_model", source)
        self._loaded = True

    def _ensure(self):
        if not self.can_translate:
            from thundertalk.core.i18n import t
            raise RuntimeError(t("overlay.no_translator"))
        if not self.is_loaded:
            self.load_model(self._source)

    @locked
    @managed(release="release_idle")
    def translate(self, samples, tgt_lang, sample_rate=16000):
        self._ensure()
        return self._call("translate", samples, tgt_lang, sample_rate)

    @locked
    @managed(release="release_idle")
    def translate_text(self, text, src_lang, tgt_lang):
        self._ensure()
        return self._call("translate_text", text, src_lang, tgt_lang)

    @locked
    def unload(self, *, forget=True):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            # The parent lease/lock guarantees no job is running here.
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=3)
            self._process.close()
            self._process = None
        self._loaded = False
        if forget:
            self._source = None

    def release_idle(self):
        self.unload(forget=False)
