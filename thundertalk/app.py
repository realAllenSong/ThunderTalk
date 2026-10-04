"""Application orchestrator — wires hotkey → recording → ASR → paste."""

from __future__ import annotations

import sys
import time
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication


from thundertalk.core.asr import AsrEngine
from thundertalk.core.audio import AudioRecorder
from thundertalk.core.device_watcher import get_watcher
from thundertalk.core.history import HistoryStore
from thundertalk.core.hotkey import HotkeyListener
from thundertalk.core.gpu_lock import GPU_LOCK
from thundertalk.core.live_preview import LivePreview, preview_wanted
from thundertalk.core.priority import DICTATION
from thundertalk.core.settings import Settings
from thundertalk.core.text_merge import merge_preview_terms
from thundertalk.core.recordings import save_recording
from thundertalk.core.i18n import t
from thundertalk.core import state as st
from thundertalk.core.state import AppState
from thundertalk.core.auto_learn import on_text_pasted as notify_auto_learn
from thundertalk.core.auto_learn import set_callback as set_auto_learn_callback
from thundertalk.core.platform_utils import (
    activate_app, request_accessibility,
    request_microphone,
)
from thundertalk.core.system_audio import (
    mute_system_audio, recover_system_audio, shutdown_system_audio, stop_recording_and_restore,
    audio_diagnostic,
)
from thundertalk.core.text_output import paste_text, save_frontmost_app
from thundertalk.ui.main_window import MainWindow
from thundertalk.ui.overlay import VoiceOverlay
from thundertalk.ui.tray import TrayIcon

import numpy as np


_BENIGN_QT_NOISE = (
    "propagateSizeHints",                 # Cocoa plugin, harmless
    "Populating font family aliases",     # one-off font DB warm-up
)


def _qt_message_filter(mode, context, message) -> None:
    """Drop known-benign Qt chatter but let real warnings through.

    This used to hide every "Could not parse stylesheet" message, which is
    how twelve broken stylesheets shipped unnoticed. tests/test_qss_valid.py
    now guards against regressions, so those warnings stay visible."""
    if any(n in message for n in _BENIGN_QT_NOISE):
        return
    if sys.stderr is not None:          # None in the windowed (console=False) bundle
        sys.stderr.write(message + "\n")


def _save_recent_recording(recording, final_text: str, pasted_text: str, enabled: bool):
    """Save a per-take snapshot off the UI thread, including failed recognition."""
    if not recording or not recording["keep"] or not enabled:
        return None
    import threading

    metadata = dict(model=recording["model"], language=recording["language"],
                    final_text=final_text, preview_text=recording["preview"],
                    pasted_text=pasted_text,
                    loop_detected=bool(recording["preview_stats"].get("loops", 0)),
                    hotwords=list(recording["hotwords"]))
    samples = recording["samples"]

    def _save():
        try:
            save_recording(samples, **metadata)
        except Exception as exc:
            print(f"[Recordings] could not save dictation: {exc}")
    worker = threading.Thread(target=_save, daemon=True, name="dictation-save")
    worker.start()
    return worker


class AsrWorker(QThread):
    """Runs ASR inference off the main thread."""

    done = Signal(str, int, float, str, float)  # text, inference_ms, duration_secs, backend, rtf
    error = Signal(str)
    waiting = Signal()      # the GPU is busy with a Studio job; the clip is kept and decoded after

    def __init__(self, engine: AsrEngine, samples: np.ndarray, wait_before=None, lock=None) -> None:
        super().__init__()
        self._engine = engine
        self._samples = samples
        self._wait_before = wait_before
        self._lock = lock

    def run(self) -> None:
        try:
            if self._wait_before is not None:
                self._wait_before()     # let a live-preview decode finish first
            lock = self._lock if getattr(self._engine, "uses_gpu", False) else None
            if lock is not None and not lock.acquire(blocking=False):
                # Studio jobs pause for dictation between units of work, so
                # this is at most one span (or MOSS's start-up pass).
                self.waiting.emit()
                lock.acquire()
            try:
                result = self._engine.recognize(self._samples)
            finally:
                if lock is not None:
                    lock.release()
            self.done.emit(result.text, result.inference_ms, result.duration_secs,
                           result.backend, result.rtf)
        except Exception as e:
            self.error.emit(str(e))


class TranslationWorker(QThread):
    """Runs SeamlessM4T translation off the main thread.

    Emits the same `done` signature as AsrWorker so the existing
    _on_asr_done handler can consume either result type without
    changes. The 'backend' field is reused to carry an identifier
    like 'seamless-torch:eng' for logging.
    """

    done = Signal(str, int, float, str, float)  # text, ms, dur, backend, rtf
    error = Signal(str)

    def __init__(self, engine, samples: np.ndarray, tgt_lang: str) -> None:
        super().__init__()
        self._engine = engine
        self._samples = samples
        self._tgt_lang = tgt_lang

    def run(self) -> None:
        try:
            result = self._engine.translate(self._samples, self._tgt_lang)
            rtf = (result.inference_ms / 1000.0) / result.duration_secs \
                if result.duration_secs > 0 else 0.0
            backend = f"seamless-torch:{result.tgt_lang}"
            self.done.emit(
                result.text, result.inference_ms, result.duration_secs,
                backend, rtf,
            )
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))


class TextTranslateWorker(QThread):
    """Runs SeamlessM4T T2TT (text→text) off the main thread.

    Used by Review mode: ASR produces the original-language text, then
    this worker translates it through the same loaded SeamlessM4T model
    using the T2TT path (no audio re-pass, much faster than S2TT).
    """

    done = Signal(str, str, str)  # original_text, translated_text, tgt_lang
    error = Signal(str)

    def __init__(
        self,
        engine,
        original_text: str,
        src_lang: str,
        tgt_lang: str,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._original_text = original_text
        self._src_lang = src_lang
        self._tgt_lang = tgt_lang

    def run(self) -> None:
        try:
            result = self._engine.translate_text(
                self._original_text,
                src_lang=self._src_lang,
                tgt_lang=self._tgt_lang,
            )
            self.done.emit(self._original_text, result.text, result.tgt_lang)
        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))


class LlmRewriteWorker(QThread):
    """Bounded provider work, followed by a guarded paste off the Qt thread."""

    done = Signal()
    proofread_ready = Signal(str, str, bool)
    proofread_failed = Signal(object)

    def __init__(self, provider, text, model, ticket, timeout, keep_clipboard,
                 hotwords=(), reference_text=None, effort=None):
        super().__init__()
        import threading
        self.cancel = threading.Event()
        self.provider, self.text, self.model = provider, text, model
        self.ticket, self.timeout, self.keep_clipboard = ticket, timeout, keep_clipboard
        self.hotwords, self.reference_text = list(hotwords or []), reference_text
        self.effort = effort

    def run(self):
        from thundertalk.core.ai_cleanup import cleanup
        from thundertalk.core.text_output import apply_if_unchanged
        try:
            result = cleanup(self.provider, self.text, self.model, timeout=self.timeout,
                             cancel=self.cancel, reference_text=self.reference_text,
                             hotwords=self.hotwords, effort=self.effort)
            if not self.cancel.is_set():
                applied = result == self.text or apply_if_unchanged(
                    self.ticket, result, self.keep_clipboard, self.cancel)
                self.proofread_ready.emit(self.text, result, bool(applied))
        except Exception as exc:
            if not self.cancel.is_set():
                self.proofread_failed.emit(exc)
        finally:
            self.done.emit()


class ModelLoadWorker(QThread):
    """Loads an ASR model off the main thread."""

    loaded = Signal(str)      # model_id — emitted from run() on success
    error = Signal(str, str)    # model_id, error_message

    def __init__(
        self,
        engine: AsrEngine,
        model_id: str,
        path: str,
        family: str,
        backend: str,
        memory_mode: str = "high",
    ) -> None:
        super().__init__()
        self._engine = engine
        self._model_id = model_id
        self._path = path
        self._family = family
        self._backend = backend
        self._memory_mode = memory_mode

    def run(self) -> None:
        try:
            print(f"[ModelLoad] Loading {self._model_id} ({self._backend}) memory={self._memory_mode}...")
            self._engine.load_model(
                self._path, self._family, self._backend,
                memory_mode=self._memory_mode,
            )
            print("[ModelLoad] load_model done")
            self.loaded.emit(self._model_id)
        except Exception as e:
            print(f"[ModelLoad] ERROR: {e}")
            self.error.emit(self._model_id, str(e))


class TranslatorLoadWorker(QThread):
    """Loads a TranslationEngine model off the main thread.

    Mirrors ModelLoadWorker's signal shape so the UI loading-state plumbing
    (set_loading / show_load_error) can reuse the same handlers, but the
    underlying engine and load_model() signature are different
    (single-arg path, no family/backend).
    """

    loaded = Signal(str)
    error = Signal(str, str)

    def __init__(self, engine, model_id: str, path: str) -> None:
        super().__init__()
        self._engine = engine
        self._model_id = model_id
        self._path = path

    def run(self) -> None:
        try:
            print(f"[ModelLoad] Loading {self._model_id} (seamless-torch translator)...")
            self._engine.load_model(self._path)
            print("[ModelLoad] translator load_model done")
            self.loaded.emit(self._model_id)
        except Exception as e:
            print(f"[ModelLoad] ERROR: {e}")
            traceback.print_exc()
            self.error.emit(self._model_id, str(e))


class Pipeline(QObject):
    """Bridges hotkey events (from a background thread) into Qt signals."""

    toggle_signal = Signal()
    audio_ready = Signal(object)
    capture_ready = Signal(object)
    review_ready = Signal(str, str, str)    # original, translated, tgt_lang
    review_started = Signal(str, str)       # original, tgt_lang (popup loads now)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        mic = settings.microphone
        self.recorder = AudioRecorder()
        self.asr = AsrEngine()
        self.translator = None  # lazy-instantiated TranslationEngine
        self._recording = False
        self._starting = False
        self._stopping = False
        self._ducking_session = None
        self._stopping_session = None
        # All in-flight QThread workers are kept alive in this list. Each
        # worker's `.finished` signal removes it. Single-reference patterns
        # (e.g. `self._worker = worker`) are unsafe because reassigning to a
        # new worker drops the old QThread's Python wrapper while its C++
        # thread may still be running, causing "QThread: Destroyed while
        # thread is still running" SIGABRT.
        self._workers: list = []
        self._load_worker: ModelLoadWorker | TranslatorLoadWorker | None = None
        # Re-entrancy guard for translator auto-load. Two rapid signal
        # emissions (e.g. user clicks "Direct" → both translation_mode_changed
        # and translation_target_changed fire) would otherwise spawn two
        # parallel load threads, each calling load_model() which does
        # unload+load — producing nondeterministic state and a stuck spinner.
        self._translator_loading: bool = False
        self._mic_device = None if mic == "auto" else mic

    def toggle(self) -> None:
        self.toggle_signal.emit()

    def stop_capture(self):
        """Quit owns the token even while a delayed microphone tail is pending."""
        session = self._ducking_session or self._stopping_session
        self._ducking_session = self._stopping_session = None
        self._starting = self._recording = False
        return stop_recording_and_restore(self.recorder, session)

    def get_translator(self):
        """Return the TranslationEngine, creating it lazily on first call.

        We do not import translate.py at module top because it transitively
        triggers torch/transformers imports the moment its lazy load_model()
        is called. The class itself is light, so it's OK to construct here
        — the heavy imports happen inside load_model().
        """
        if self.translator is None:
            from thundertalk.core.translate import TranslationEngine
            self.translator = TranslationEngine()
        return self.translator


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        from thundertalk import selftest
        sys.exit(selftest.run(sys.argv[2:]))
    qInstallMessageHandler(_qt_message_filter)
    app = QApplication(sys.argv)
    from thundertalk.ui import theme as _theme
    _theme.force_light(app)          # the palette is light-only
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ThunderTalk")
    recover_system_audio()
    import atexit
    atexit.register(shutdown_system_audio)

    from thundertalk.ui.tray import app_icon
    app.setWindowIcon(app_icon())

    settings = Settings()
    history = HistoryStore()
    pipe = Pipeline(settings)
    def _shutdown_recording():
        session = pipe._ducking_session or pipe._stopping_session
        if session is not None:
            session.microphone_transition("close")
        from thundertalk.ui.studio.clone_dialog import CloneDialog
        for widget in app.topLevelWidgets():
            if isinstance(widget, CloneDialog):
                widget.reject()  # close Studio input before publishing restores
        try:
            pipe.stop_capture()
        except Exception as exc:
            audio_diagnostic("quit_capture_failed", error=type(exc).__name__)
        finally:
            shutdown_system_audio()
    app.aboutToQuit.connect(_shutdown_recording)
    overlay = VoiceOverlay()
    overlay.set_hotkey(settings.hotkey)
    from thundertalk.ui.review_overlay import ReviewOverlay
    review_overlay = ReviewOverlay()
    state = AppState(settings.hotkey)
    window = MainWindow(settings, history, state)
    tray = TrayIcon(state)

    # --- Startup permission state (macOS) ----------------------------------
    # Onboarding walks first-run users through permissions; returning users
    # get the same system prompts as before, and anything still missing shows
    # up as a fix-it banner on Home (no modal dialog, no hard-coded language).
    def _check_permissions() -> None:
        state.refresh_permissions()
        if not settings.get("onboarding_done"):
            return
        if state.mic_status == "not_determined":
            request_microphone()
        if not state.accessibility_ok:
            request_accessibility()

    QTimer.singleShot(800, _check_permissions)

    # --- Hotwords + Language → ASR engine ---
    pipe.asr.set_hotwords(settings.hotwords)
    pipe.asr.set_language(settings.transcription_language)
    pipe.asr.set_speaker_labels(settings.get("moss_speaker_labels"))
    window.models_page.speaker_labels_toggled.connect(pipe.asr.set_speaker_labels)

    # --- Studio: the dictation model also serves file transcription, and reads
    # cloning references / generated speech back to catch mistakes ---
    window.studio_page.set_engine(pipe.asr)
    app.aboutToQuit.connect(window.studio_page.shutdown)

    # --- Live preview: words so far, shown under the indicator while recording.
    # The pasted text still comes from the full-clip recognition after stop.
    live = LivePreview(pipe.recorder.snapshot,
                       lambda s: pipe.asr.recognize(s, preview=True).text)
    live.text_changed.connect(lambda text: overlay.set_preview_text(text))

    # --- Model loading helpers -----------------------------------------
    def _clear_load_worker() -> None:
        # Runs on QThread's built-in finished signal, AFTER run() has fully
        # exited — so dropping the last Python ref here is safe.
        pipe._load_worker = None

    def _start_translator_load(model_id: str, path: str) -> None:
        """Load SeamlessM4T into the TranslationEngine on a background thread.

        Translation models are NOT set as active_model_id (that's ASR-specific
        and would crash _restore_model on next launch with 'Unknown model
        family'). They simply become the loaded translator engine.
        """
        translator = pipe.get_translator()

        # Idempotency: if already loaded with this model, just update UI.
        if translator.is_loaded and translator.current_model == model_id:
            print(f"[ModelLoad] Translator already loaded: {model_id}")
            window.models_page.set_loading(model_id, False)
            window.models_page.set_translator_active(model_id)
            return

        worker = TranslatorLoadWorker(translator, model_id, path)

        def _on_translator_loaded(mid: str) -> None:
            print(f"[ModelLoad] Translator ready: {mid}")
            window.models_page.set_loading(mid, False)
            window.models_page.set_translator_active(mid)

        def _on_translator_error(mid: str, msg: str) -> None:
            window.show_load_error(f"Failed to load {mid}: {msg}")
            window.models_page.set_loading(mid, False)

        worker.loaded.connect(_on_translator_loaded)
        worker.error.connect(_on_translator_error)
        worker.finished.connect(_clear_load_worker)
        pipe._load_worker = worker
        worker.start()

    def _start_model_load(model_id: str, path: str, family: str, backend: str) -> None:
        """Start loading a model in a background thread."""
        if pipe._load_worker and pipe._load_worker.isRunning():
            window.show_load_error("Another model is already loading, please wait.")
            return

        window.models_page.set_loading(model_id, True)
        if backend != "seamless-torch":
            state.set_model_loading(model_id)

        # Translation models load into TranslationEngine, not AsrEngine.
        # AsrEngine.load_model() does not understand the SeamlessM4T-v2 family
        # and would raise ValueError("Unknown model family").
        if backend == "seamless-torch":
            _start_translator_load(model_id, path)
            return

        worker = ModelLoadWorker(
            pipe.asr, model_id, path, family, backend,
            memory_mode=settings.memory_mode,
        )

        def _on_load_finished(mid: str) -> None:
            window.set_active_model(mid)
            tray.set_model_status(mid)
            settings.set("active_model_id", mid)
            window.models_page.set_loading(mid, False)
            state.set_model_ready(mid)
            window.show_toast(t("toast.model_ready").format(name=state.model_name), "success")

        def _on_load_error(mid: str, msg: str) -> None:
            window.show_load_error(f"Failed to load {mid}: {msg}")
            window.models_page.set_loading(mid, False)
            state.set_model_error(mid, msg[:160])
            window.show_toast(t("toast.model_failed"), "error")
            traceback.print_exc()

        worker.loaded.connect(_on_load_finished)
        worker.error.connect(_on_load_error)
        worker.finished.connect(_clear_load_worker)
        pipe._load_worker = worker
        worker.start()

    # --- Restore last active model (background thread) --------------------
    # Loading weights used to run synchronously on the UI thread right after
    # the window appeared, freezing it for seconds (and for the length of a
    # download if the weights were missing). Now it goes through the same
    # worker as a manual Activate, so the UI shows "Loading model…".
    def _restore_model() -> None:
        last_model = settings.active_model_id
        if not last_model:
            return
        from thundertalk.core.models import get_model_path, is_downloaded, BUILTIN_MODELS
        if not is_downloaded(last_model):
            return
        path = get_model_path(last_model)
        info = next((m for m in BUILTIN_MODELS if m.id == last_model), None)
        if not (path and info):
            return
        # Translation engine models go through _maybe_load_translator instead.
        if info.backend == "seamless-torch":
            return
        print(f"[Startup] Loading model in background: {last_model}")
        _start_model_load(last_model, path, info.family, info.backend)

    QTimer.singleShot(500, _restore_model)

    from thundertalk.core import text_output
    proofread = window.proofread_page
    QTimer.singleShot(1000, proofread.refresh)
    text_output.activity.set_hotkey(settings.hotkey)
    text_output.activity.start()
    pipe._last_paste = None
    pipe._cleanup_worker = None

    def _cancel_cleanup():
        if pipe._cleanup_worker is not None:
            pipe._cleanup_worker.cancel.set()
        pipe._cleanup_worker = None

    def _shutdown_cleanup():
        _cancel_cleanup()
        for worker in list(pipe._workers):
            if isinstance(worker, LlmRewriteWorker):
                worker.cancel.set()
                worker.wait()
        proofread.shutdown()
        text_output.activity.stop()

    app.aboutToQuit.connect(_shutdown_cleanup)
    proofread.toggle.toggled_signal.connect(lambda _value: _cancel_cleanup())

    # --- Model loading from UI -----------------------------------------
    def on_load_model(model_id: str, path: str, family: str, backend: str) -> None:
        _start_model_load(model_id, path, family, backend)

    window.load_model_signal.connect(on_load_model)

    # --- Auto-learn hotwords -------------------------------------------
    def _on_auto_learned_word(word: str) -> None:
        print(f"[AutoLearn] New hotword: {word}")
        QTimer.singleShot(0, lambda: window.hotwords_page.add_hotword_external(word))
        QTimer.singleShot(0, lambda: window.show_toast(
            t("toast.hotword_learned").format(w=word), "success"))

    set_auto_learn_callback(_on_auto_learned_word)

    def _paste_and_learn(text: str) -> None:
        keep_clipboard = not settings.get("save_to_clipboard")
        paste_text(text, keep_clipboard=keep_clipboard)
        notify_auto_learn(text)

    # --- Voice pipeline ------------------------------------------------
    # DICTATION is raised when recording starts and lowered exactly once per
    # recording: here (after the paste is dispatched), in _on_asr_error, or on
    # an early exit in on_toggle. Studio jobs pause while it is raised.
    def _on_asr_done(text: str, ms: int, dur: float, backend: str, rtf: float,
                     recording=None) -> None:
        try:
            _handle_asr_done(text, ms, dur, backend, rtf, recording)
        finally:
            DICTATION.end()

    def _handle_asr_done(text: str, ms: int, dur: float, backend: str, rtf: float,
                         recording=None) -> None:
        # Audio is restored when recording stops (before ASR), not here.
        t_start = time.perf_counter()
        state.set_recording(st.REC_IDLE)
        print("[Toggle] _on_asr_done called")
        # Note: do NOT clear pipe._worker here — the QThread's run() hasn't
        # fully unwound yet when this handler fires. Clearing now can drop the
        # last Python ref and trigger dealloc of a still-running QThread, which
        # Qt aborts on with SIGABRT. _clear_asr_worker() handles it from the
        # built-in finished signal, after run() has returned.
        print(f'[ASR] Result: "{text}" ({ms}ms, backend={backend}, RTF={rtf:.3f})')
        raw_text = text
        reference = recording["preview"] if recording else ""
        if not backend.startswith("seamless-torch"):
            text = merge_preview_terms(text, reference)
            if text != raw_text:
                print(f"[DictationMerge] {raw_text!r} → {text!r} (preview={reference!r})")

        def _remember(pasted):
            _save_recent_recording(recording, raw_text, pasted,
                                   settings.get("keep_recent_recordings"))

        if text:
            overlay.hide_overlay()
            # Paste FIRST — lowest latency path to the user's target app.
            ticket = text_output.paste_dictation(text, not settings.get("save_to_clipboard"))
            if pipe._last_paste is not None:
                pipe._last_paste.invalidate()
            pipe._last_paste = ticket
            _remember(text)
            notify_auto_learn(text)
            paste_dispatch_ms = int((time.perf_counter() - t_start) * 1000)
            print(f"[Toggle] Post-ASR dispatch took {paste_dispatch_ms}ms")
            # AI proofreading in the background; replaces the paste only if untouched.
            if (not backend.startswith("seamless-torch")
                    and settings.get("llm_rewrite_enabled")
                    and not (settings.translation_target != "off" and settings.translation_mode == "review")):
                _launch_rewrite(text, ticket, reference_text=reference or None)
            # Defer non-critical UI updates so they don't block paste
            history.add(
                text=text,
                duration_secs=dur,
                inference_ms=ms,
                model=pipe.asr.current_model or "unknown",
            )
            QTimer.singleShot(50, window.home_page.refresh)
            # Review mode: kick off T2TT translation in parallel; the popup
            # is shown when translated text comes back. Only triggers when
            # the result we just got was an ASR pass (not S2TT translator).
            is_asr_result = not backend.startswith("seamless-torch")
            tgt = settings.get("translation_target")
            mode = settings.get("translation_mode")
            translator = pipe.translator
            if (
                is_asr_result
                and tgt
                and tgt != "off"
                and mode == "review"
                and translator
                and translator.is_loaded
            ):
                from thundertalk.core.translate import detect_src_lang
                src_lang = detect_src_lang(text)
                print(f"[Review] T2TT {src_lang}→{tgt} on {len(text)} chars")
                # Defer the popup briefly so the original paste actually
                # lands in the user's app first. Without this, the popup
                # races ahead of the async paste and the user sees the
                # translation before their original is even visible.
                _t2t_text = text
                _t2t_tgt = tgt
                QTimer.singleShot(
                    300,
                    lambda: pipe.review_started.emit(_t2t_text, _t2t_tgt),
                )
                t2t_worker = TextTranslateWorker(translator, text, src_lang, tgt)
                t2t_worker.done.connect(_on_t2tt_done)
                t2t_worker.error.connect(_on_t2tt_error)
                _track_worker(t2t_worker)
                t2t_worker.start()
        else:
            _remember("")
            overlay.show_error(t("overlay.no_speech"))

    def _on_asr_error(msg: str, recording=None) -> None:
        DICTATION.end()
        print("[Toggle] _on_asr_error called")
        state.set_recording(st.REC_IDLE)
        print(f"[ASR] Error: {msg}")
        _save_recent_recording(recording, "", "", settings.get("keep_recent_recordings"))
        overlay.show_error(msg[:40])

    def _track_worker(w) -> None:
        """Keep `w` Python-side referenced until its QThread.finished fires.

        Replaces the older `pipe._worker = w` single-ref pattern, which broke
        when a second worker started before the first finished — assigning to
        `pipe._worker` dropped the running thread's wrapper.
        """
        pipe._workers.append(w)
        def _untrack() -> None:
            try:
                pipe._workers.remove(w)
            except ValueError:
                pass
            w.deleteLater()
        w.finished.connect(_untrack)

    def _on_t2tt_done(original: str, translated: str, tgt_lang: str) -> None:
        print(
            f'[Review] T2TT done: "{original[:30]}…" → "{translated[:30]}…" ({tgt_lang})'
        )
        # Backfill the translation onto the existing history entry so the
        # Home page can show / let the user copy it later.
        history.update_translation(original, translated, tgt_lang)
        QTimer.singleShot(50, window.home_page.refresh)
        pipe.review_ready.emit(original, translated, tgt_lang)

    def _on_t2tt_error(msg: str) -> None:
        print(f"[Review] T2TT error: {msg}")
        # Original text is already pasted; silently drop the translation.
        # No overlay needed — user has the original; the popup just doesn't appear.

    def _launch_rewrite(text, ticket, reference_text=None):
        provider = proofread.chosen_provider()
        if provider is None:
            return
        _cancel_cleanup()
        try:
            timeout = min(120.0, max(1.0, float(settings.get("cleanup_timeout"))))
        except (ValueError, TypeError):
            timeout = 60.0
        worker = LlmRewriteWorker(provider, text, proofread.chosen_model(provider),
                                  ticket, timeout, not settings.get("save_to_clipboard"),
                                  hotwords=settings.hotwords, reference_text=reference_text,
                                  effort=proofread.chosen_effort(provider) or None)
        pipe._cleanup_worker = worker
        overlay.show_cleanup(text)

        def _ready(original, corrected, applied):
            if pipe._cleanup_worker is worker and not worker.cancel.is_set() and not pipe._recording:
                if applied:
                    overlay.show_cleanup_diff(original, corrected)
                else:
                    overlay.show_result(t("cleanup.skipped"))

        def _failed(error):
            if pipe._cleanup_worker is worker and not worker.cancel.is_set() and not pipe._recording:
                from thundertalk.ui.pages.proofread_page import error_reason
                overlay.show_error(t("cleanup.error").format(reason=error_reason(error)))

        worker.proofread_ready.connect(_ready)
        worker.proofread_failed.connect(_failed)

        def _done():
            if pipe._cleanup_worker is worker:
                pipe._cleanup_worker = None
                if not pipe._recording and state.recording == st.REC_IDLE and overlay._state == overlay._CLEANUP:
                    overlay.hide_overlay()
        worker.done.connect(_done)
        _track_worker(worker)
        worker.start()

    # Grace period (ms) between stop-requested and stream-closed so the
    # audio callback can capture trailing speech that is still being spoken
    # as the user's fingers reach the hotkey. Without this, the OS driver /
    # PortAudio in-flight buffer (~50-150ms) plus any audio spoken during
    # keystroke reaction (~100-200ms) is lost, eating 1-2 trailing syllables.
    TAIL_GRACE_MS = 250

    @Slot()
    def on_toggle() -> None:
        audio_diagnostic("hotkey", recording=pipe._recording, starting=pipe._starting,
                         stopping=pipe._stopping)
        print(f"[Toggle] on_toggle called, _recording={pipe._recording}")
        if pipe._stopping:
            audio_diagnostic("hotkey_skipped", reason="microphone_tail")
            return  # the previous microphone stream still owns its tail
        if pipe._starting:
            audio_diagnostic("capture_cancelled", reason="hotkey_during_startup")
            pipe._starting = False
            session, pipe._ducking_session = pipe._ducking_session, None
            try:
                stop_recording_and_restore(pipe.recorder, session)
            except Exception as exc:
                audio_diagnostic("cancel_capture_failed", error=type(exc).__name__)
            DICTATION.end()
            state.set_recording(st.REC_IDLE)
            overlay.hide_overlay()
            return
        if pipe._recording:
            # ---- STOP recording ----
            t_stop = time.perf_counter()
            live.stop()             # no new preview decodes; final takes priority
            print(f"[Toggle] Stop requested, capturing {TAIL_GRACE_MS}ms tail...")
            overlay.show_transcribing()
            state.set_recording(st.REC_TRANSCRIBING)
            pipe._recording = False
            pipe._stopping = True
            session, pipe._ducking_session = pipe._ducking_session, None
            pipe._stopping_session = session

            def _finalize_stop() -> None:
                try:
                    samples = stop_recording_and_restore(pipe.recorder, session)
                except Exception as exc:
                    DICTATION.end()
                    state.set_recording(st.REC_IDLE)
                    overlay.show_error(t("overlay.mic_unavailable"))
                    print(f"[Toggle] recorder.stop failed: {exc}")
                    return
                finally:
                    pipe._stopping = False
                    pipe._stopping_session = None
                stop_ms = int((time.perf_counter() - t_stop) * 1000)
                print(f"[Toggle] Restoring system audio ({stop_ms}ms total)")

                if samples is None or len(samples) < 800:
                    print("[Toggle] Too short (audio already restored on stop)")
                    DICTATION.end()
                    state.set_recording(st.REC_IDLE)
                    overlay.show_error(t("overlay.too_short"))
                    return

                recording = {"samples": samples, "preview": live.last_clean_text(),
                             "preview_stats": live.stats,
                             "model": settings.active_model_id or pipe.asr.current_model or "unknown",
                             "language": settings.transcription_language,
                             "hotwords": list(settings.hotwords),
                             "keep": settings.get("keep_recent_recordings")}

                def _done(text, ms, dur, backend, rtf):
                    _on_asr_done(text, ms, dur, backend, rtf, recording=recording)

                def _error(msg):
                    _on_asr_error(msg, recording=recording)

                tgt = settings.get("translation_target")
                mode = settings.get("translation_mode") or "direct"

                # Direct mode: SeamlessM4T S2TT (audio → translated text directly)
                if tgt and tgt != "off" and mode == "direct":
                    translator = pipe.get_translator()
                    if not translator.is_loaded:
                        print("[Toggle] Direct translation but model not loaded")
                        DICTATION.end()
                        state.set_recording(st.REC_IDLE)
                        overlay.show_error(t("overlay.no_translator"))
                        return
                    print(f"[Toggle] Starting Direct translation → {tgt} on {len(samples)} samples")
                    worker = TranslationWorker(translator, samples, tgt)
                    worker.done.connect(_done)
                    worker.error.connect(_error)
                    _track_worker(worker)
                    worker.start()
                    return

                # Off mode OR Review mode: route through ASR first.
                # Review mode adds T2TT in _on_asr_done after the ASR result
                # is pasted, then shows the review popup.
                if not pipe.asr.is_loaded:
                    print("[Toggle] No ASR model (audio already restored on stop)")
                    DICTATION.end()
                    state.set_recording(st.REC_IDLE)
                    overlay.show_error(t("overlay.no_model"))
                    return

                if tgt and tgt != "off" and mode == "review":
                    print(f"[Toggle] Starting Review (ASR → T2TT → popup) on {len(samples)} samples")
                else:
                    print(f"[Toggle] Starting ASR on {len(samples)} samples")
                worker = AsrWorker(pipe.asr, samples,
                                   wait_before=live.wait_idle, lock=GPU_LOCK)
                worker.done.connect(_done)
                worker.error.connect(_error)
                worker.waiting.connect(overlay.show_waiting)
                _track_worker(worker)
                worker.start()

            QTimer.singleShot(TAIL_GRACE_MS, _finalize_stop)
        else:
            # ---- START recording ----
            if not pipe.asr.is_loaded:
                audio_diagnostic("hotkey_skipped", reason="model_unavailable",
                                 loading=state.model_status == st.MODEL_LOADING)
                overlay.show_error(
                    t("status.loading") if state.model_status == st.MODEL_LOADING
                    else t("overlay.load_model")
                )
                return
            # A denied microphone doesn't raise — PortAudio just yields silence,
            # which used to surface as a baffling "No speech detected".
            state.refresh_permissions()
            if state.mic_status in ("denied", "restricted"):
                audio_diagnostic("hotkey_skipped", reason="microphone_denied")
                overlay.show_error(t("overlay.mic_denied"))
                return
            # Dismiss any leftover Review popup from a previous round
            review_overlay.hide_review()
            _cancel_cleanup()
            save_frontmost_app()
            text_output.activity.set_hotkey(settings.hotkey)
            if not text_output.activity.available:
                text_output.activity.stop()
                text_output.activity.start()
            # Show overlay immediately so user gets instant visual feedback
            pipe._starting = True
            audio_diagnostic("recording_requested", ducking=bool(settings.get("mute_speakers")))
            DICTATION.begin()
            overlay.show_recording()
            app.processEvents()
            if not pipe._starting:
                return
            if settings.get("mute_speakers"):
                try:
                    session = mute_system_audio()
                except Exception as exc:
                    pipe._starting = False
                    DICTATION.end()
                    overlay.show_error(t("overlay.audio_unavailable"))
                    print(f"[Toggle] speaker mute startup failed: {exc}")
                    return
                pipe._ducking_session = session
                session.ready.add_done_callback(lambda _future: pipe.audio_ready.emit(session))
            else:
                _start_capture(None)

    def _start_capture(session) -> None:
        if not pipe._starting or session is not pipe._ducking_session:
            audio_diagnostic("capture_skipped", reason="stale_or_cancelled_ready")
            return  # cancelled startup or a completion from an older generation
        error_text = t("overlay.audio_unavailable")
        try:
            if session is not None:
                session.ready.result()  # already done; never blocks the Qt thread
                session.microphone_transition("open")
            error_text = t("overlay.mic_unavailable")
            mic = settings.microphone
            audio_diagnostic("microphone_open")
            pipe.recorder.start(device=None if mic == "auto" else mic)
            audio_diagnostic("microphone_opened")
            if not pipe.recorder.is_recording:
                raise RuntimeError("microphone startup timed out")
        except Exception as exc:
            try:
                stop_recording_and_restore(pipe.recorder, session)
            except Exception as stop_exc:
                audio_diagnostic("startup_cleanup_failed", error=type(stop_exc).__name__)
            pipe._ducking_session = None
            pipe._starting = False
            DICTATION.end()
            print(f"[Toggle] recording startup failed: {exc}")
            overlay.show_error(error_text)
            return
        if session is not None:
            checked = session.synchronize()
            checked.add_done_callback(lambda future: pipe.capture_ready.emit((session, future)))
        else:
            _finish_capture((None, None))

    def _finish_capture(payload) -> None:
        session, checked = payload
        if not pipe._starting or session is not pipe._ducking_session:
            audio_diagnostic("capture_skipped", reason="stale_or_cancelled_check")
            return
        try:
            if checked is not None:
                checked.result()  # delivered by the worker after graph verification
            pipe.recorder.discard_pending()
        except Exception as exc:
            pipe._starting = False
            pipe._ducking_session = None
            try:
                stop_recording_and_restore(pipe.recorder, session)
            except Exception as stop_exc:
                audio_diagnostic("startup_cleanup_failed", error=type(stop_exc).__name__)
            DICTATION.end()
            state.set_recording(st.REC_IDLE)
            overlay.show_error(t("overlay.audio_unavailable"))
            audio_diagnostic("capture_check_failed", error=type(exc).__name__)
            return
        if session is not None:
            session.diagnostic("capture_started")
        pipe._starting = False
        pipe._recording = True
        state.set_recording(st.REC_RECORDING)
        live.reset()
        if preview_wanted(settings):
            live.start()
        print("[Toggle] Recording started")

    pipe.audio_ready.connect(_start_capture, Qt.QueuedConnection)
    pipe.capture_ready.connect(_finish_capture, Qt.QueuedConnection)

    pipe.toggle_signal.connect(on_toggle, Qt.QueuedConnection)

    # --- Review overlay (translation confirm popup) -------------------
    # `review_started` fires the moment the original is pasted; popup
    # appears in loading state. `review_ready` fires when T2TT finishes
    # and fills in the translation.
    pipe.review_started.connect(review_overlay.show_review_loading)
    pipe.review_ready.connect(
        lambda _orig, translated, tgt: review_overlay.update_translation(
            translated, tgt
        )
    )

    def _on_replace_clicked(translated: str) -> None:
        from thundertalk.core.text_output import replace_pasted_text
        keep_clipboard = not settings.get("save_to_clipboard")
        replace_pasted_text(translated, keep_clipboard=keep_clipboard)

    review_overlay.replace_clicked.connect(_on_replace_clicked)

    def _on_review_lang_changed(original: str, new_lang: str) -> None:
        """User picked a different target language in the popup.
        Persist to settings and re-run T2TT immediately."""
        settings.set("translation_target", new_lang)
        translator = pipe.translator
        if translator is None or not translator.is_loaded:
            print("[Review] Lang change requested but translator not loaded")
            return
        from thundertalk.core.translate import detect_src_lang
        src_lang = detect_src_lang(original)
        print(f"[Review] Re-translate {src_lang}→{new_lang}")
        worker = TextTranslateWorker(translator, original, src_lang, new_lang)
        worker.done.connect(_on_t2tt_done)
        worker.error.connect(_on_t2tt_error)
        _track_worker(worker)
        worker.start()

    review_overlay.lang_change_requested.connect(_on_review_lang_changed)

    # Feed live mic level into the overlay waveform — only runs while
    # recording so idle CPU stays near zero.
    _level_timer = QTimer()
    _level_timer.setInterval(40)
    def _push_level() -> None:
        rms = pipe.recorder.current_rms
        overlay.set_audio_level(rms)
        state.push_level(rms)

    _level_timer.timeout.connect(_push_level)

    _orig_show_recording = overlay.show_recording
    def _show_recording_wrapped() -> None:
        _orig_show_recording()
        _level_timer.start()
    overlay.show_recording = _show_recording_wrapped

    _orig_show_transcribing = overlay.show_transcribing
    def _show_transcribing_wrapped() -> None:
        _level_timer.stop()
        _orig_show_transcribing()
    overlay.show_transcribing = _show_transcribing_wrapped

    _orig_hide = overlay.hide_overlay
    def _hide_wrapped() -> None:
        _level_timer.stop()
        _orig_hide()
    overlay.hide_overlay = _hide_wrapped

    # --- Audio device watcher -----------------------------------------
    # Listens for hot-plug, BT pairing, and A2DP↔HFP profile transitions
    # so the mic dropdown stays live and the saved input device is
    # rediscovered if it appears after launch (login-launch race).
    get_watcher().start(recorder=pipe.recorder)

    # --- Hotkey --------------------------------------------------------
    hotkey = HotkeyListener(on_toggle=pipe.toggle, key_name=settings.hotkey)
    hotkey.start()

    def _on_hotkey_setting_changed(key_name: str) -> None:
        hotkey.set_hotkey(key_name)
        state.set_hotkey(key_name)
        overlay.set_hotkey(key_name)

    window.settings_page.hotkey_changed.connect(_on_hotkey_setting_changed)

    # While the user is capturing a new hotkey, gate the global listener so
    # pressing the current hotkey doesn't trigger recording.
    window.settings_page.capture_started.connect(lambda: hotkey.set_enabled(False))
    window.settings_page.capture_ended.connect(lambda: hotkey.set_enabled(True))

    def _on_hotwords_changed(words: list[str]) -> None:
        pipe.asr.set_hotwords(words)
        if pipe.asr.needs_reload_for_hotwords and pipe.asr.is_loaded:
            mid = pipe.asr.current_model
            md = pipe.asr._model_dir
            mf = pipe.asr._model_family
            be = pipe.asr._active_backend
            if md and mf:
                _start_model_load(mid or "", md, mf, be)

    window.hotwords_page.hotwords_changed.connect(_on_hotwords_changed)

    # --- Language setting → ASR engine ---
    def _on_settings_changed() -> None:
        pipe.asr.set_language(settings.transcription_language)

    window.settings_page.settings_changed.connect(_on_settings_changed)

    # --- Translation engine (lazy load) --------------------------------
    def _maybe_load_translator() -> None:
        """Load SeamlessM4T into RAM if target language is set AND model is on disk.

        Updates the inline translator-status row inside the TranslationModeCard
        with one of: hidden / missing / loading / ready / error so the user
        can see what's happening without guessing from spinners.
        """
        tgt = settings.get("translation_target")
        if not tgt or tgt == "off":
            # Free SeamlessM4T if it was previously loaded. Without this
            # the translator engine sits in RAM (~4 GB MPS + ~5 GB CPU)
            # until app quit even though the user has explicitly turned
            # translation off.
            if pipe.translator is not None and pipe.translator.is_loaded:
                print("[Translate] Target=off — unloading SeamlessM4T")
                pipe.translator.unload()
                window.models_page.set_translator_active(None)
            window.models_page.set_translator_status("hidden")
            return

        from thundertalk.core.models import is_downloaded, get_model_path
        if not is_downloaded("seamless-m4t-v2-large"):
            print("[Translate] Target set but model not downloaded")
            window.models_page.set_translator_status("missing")
            return

        translator = pipe.get_translator()
        if translator.is_loaded:
            window.models_page.set_translator_status("ready")
            return
        if pipe._translator_loading:
            window.models_page.set_translator_status("loading")
            return

        model_path = get_model_path("seamless-m4t-v2-large")
        if not model_path:
            window.models_page.set_translator_status("missing")
            return

        pipe._translator_loading = True
        # Visible spinner on the SeamlessM4T card AND a status pill at the
        # top so users understand the ~10s torch+MPS load isn't a stuck UI.
        window.models_page.set_loading("seamless-m4t-v2-large", True)
        window.models_page.set_translator_status("loading")

        # Use a QThread (TranslatorLoadWorker) instead of threading.Thread.
        # The previous version called QTimer.singleShot(0, _on_done) from
        # inside the worker thread; QTimer.singleShot binds the timer to
        # the calling thread's event loop, and a raw threading.Thread has
        # none, so the completion callback never fired and the UI stayed
        # in "loading" forever even after the model was actually loaded.
        # Qt signals from a QThread auto-marshal to the receiver's
        # thread (main UI) without any timer dance.
        worker = TranslatorLoadWorker(
            translator, "seamless-m4t-v2-large", model_path
        )

        def _on_translator_loaded(mid: str) -> None:
            pipe._translator_loading = False
            window.models_page.set_loading(mid, False)
            window.models_page.set_translator_active(mid)
            window.models_page.set_translator_status("ready")

        def _on_translator_failed(mid: str, msg: str) -> None:
            pipe._translator_loading = False
            window.models_page.set_loading(mid, False)
            window.models_page.set_translator_status("error", msg[:80])

        worker.loaded.connect(_on_translator_loaded)
        worker.error.connect(_on_translator_failed)
        _track_worker(worker)
        worker.start()

    QTimer.singleShot(1500, _maybe_load_translator)
    # The Translation Mode card on the Models page is the canonical control
    # for translation_target / translation_mode. Either signal triggers a
    # translator-load check (loads SeamlessM4T into RAM if user just turned
    # translation on, no-ops otherwise).
    window.models_page.translation_target_changed.connect(
        lambda _code: _maybe_load_translator()
    )
    window.models_page.translation_mode_changed.connect(
        lambda _mode: _maybe_load_translator()
    )

    # User clicked the "Download" button on the translator-status row
    # (only visible when target ≠ off and model is missing on disk).
    def _on_download_translator_requested() -> None:
        from thundertalk.core.models import BUILTIN_MODELS, is_downloaded
        if is_downloaded("seamless-m4t-v2-large"):
            # Already on disk — kick the loader instead.
            _maybe_load_translator()
            return
        info = next(m for m in BUILTIN_MODELS if m.id == "seamless-m4t-v2-large")
        # Reuse the Models-page download path so we get progress + completion.
        window.models_page._on_download(info.id)
        window.models_page.set_translator_status(
            "loading", "Downloading translation model… this can take a while."
        )

    window.models_page.download_translator_requested.connect(
        _on_download_translator_requested
    )

    # When the SeamlessM4T download finishes, auto-load it into the
    # translator engine so the user doesn't need to know to click Activate.
    def _on_model_download_completed(model_id: str) -> None:
        if model_id == "seamless-m4t-v2-large":
            _maybe_load_translator()
            return
        from thundertalk.core.models import BUILTIN_MODELS
        info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
        if info is not None:
            window.show_toast(t("models.downloaded").format(name=info.name), "success")
        # First model on a fresh install: switch it on for the user instead of
        # leaving them to find the Activate button.
        if state.model_status in (st.MODEL_NONE, st.MODEL_ERROR):
            window.models_page.activate_model(model_id)

    window.models_page.model_download_completed.connect(
        _on_model_download_completed
    )
    window.models_page.download_failed.connect(
        lambda _mid, _msg: window.show_toast(t("toast.download_failed"), "error")
    )

    # --- Tray ----------------------------------------------------------
    def _show_settings_window() -> None:
        activate_app()
        window.show()
        window.raise_()
        window.activateWindow()

    tray.open_action.triggered.connect(_show_settings_window)
    tray.toggle_requested.connect(pipe.toggle)
    tray.quit_action.triggered.connect(app.quit)
    tray.show()

    window.show()
    window.raise_()

    # First run → guided setup. Anyone who already has a model (i.e. every
    # existing user) is marked done silently so upgrades don't nag.
    if not settings.get("onboarding_done"):
        if settings.active_model_id:
            settings.set("onboarding_done", True)
        else:
            QTimer.singleShot(450, window.show_onboarding)

    # Track the last running version in settings (used previously
    # to show a post-update permission hint dialog; the dialog was
    # too intrusive on every minor update so it's now silent).
    # Marker persists in case a less-intrusive surface is added
    # later — e.g. a one-line banner on the About page.
    def _record_run_version() -> None:
        import thundertalk
        settings.set("last_run_version", thundertalk.__version__)

    QTimer.singleShot(1500, _record_run_version)

    # Reconcile macOS Login Item state with the stored toggle. The
    # toggle was a no-op in v1.1.9 and earlier, so users who flipped
    # it on saw nothing register; this catches them up on first launch
    # of a build that includes thundertalk/core/autostart.py.
    def _sync_autostart() -> None:
        from thundertalk.core import autostart
        autostart.sync_with_setting(bool(settings.get("launch_at_startup")))

    QTimer.singleShot(2000, _sync_autostart)

    # Silent update probe shortly after launch. 4 s is long enough
    # that startup feels responsive but short enough that, by the
    # time the user lands on the About page, the action button has
    # already flipped to "Download Update" if a new release exists.
    # Probe only changes the About page state — never raises a
    # popup, never blocks.
    QTimer.singleShot(4_000, window.about_page.trigger_background_check)

    app.aboutToQuit.connect(hotkey.stop)

    sys.exit(app.exec())
