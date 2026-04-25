"""Application orchestrator — wires hotkey → recording → ASR → paste."""

from __future__ import annotations

import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication


def _suppress_style_warnings(mode, context, message) -> None:
    if "Could not parse stylesheet" not in message:
        sys.stderr.write(message + "\n")

from thundertalk.core.asr import AsrEngine
from thundertalk.core.audio import AudioRecorder
from thundertalk.core.history import HistoryStore
from thundertalk.core.hotkey import HotkeyListener
from thundertalk.core.settings import Settings
from thundertalk.core.auto_learn import on_text_pasted as notify_auto_learn
from thundertalk.core.auto_learn import set_callback as set_auto_learn_callback
from thundertalk.core.platform_utils import (
    set_accessory_app, activate_app, deactivate_app,
    check_accessibility, request_accessibility,
    check_microphone, request_microphone,
    open_accessibility_settings, open_microphone_settings,
)
from thundertalk.core.system_audio import mute_system_audio, unmute_system_audio, force_unmute, ensure_audio_restored
from thundertalk.core.text_output import paste_text, save_frontmost_app
from thundertalk.ui.main_window import MainWindow
from thundertalk.ui.overlay import VoiceOverlay
from thundertalk.ui.tray import TrayIcon

import numpy as np


class AsrWorker(QThread):
    """Runs ASR inference off the main thread."""

    done = Signal(str, int, float, str, float)  # text, inference_ms, duration_secs, backend, rtf
    error = Signal(str)

    def __init__(self, engine: AsrEngine, samples: np.ndarray) -> None:
        super().__init__()
        self._engine = engine
        self._samples = samples

    def run(self) -> None:
        try:
            result = self._engine.recognize(self._samples)
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


class ModelLoadWorker(QThread):
    """Loads an ASR model off the main thread."""

    loaded = Signal(str)      # model_id — emitted from run() on success
    error = Signal(str, str)    # model_id, error_message

    def __init__(self, engine: AsrEngine, model_id: str, path: str, family: str, backend: str) -> None:
        super().__init__()
        self._engine = engine
        self._model_id = model_id
        self._path = path
        self._family = family
        self._backend = backend

    def run(self) -> None:
        try:
            print(f"[ModelLoad] Loading {self._model_id} ({self._backend})...")
            self._engine.load_model(self._path, self._family, self._backend)
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
    review_ready = Signal(str, str, str)    # original, translated, tgt_lang
    review_started = Signal(str, str)       # original, tgt_lang (popup loads now)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        mic = settings.microphone
        self.recorder = AudioRecorder()
        self.asr = AsrEngine()
        self.translator = None  # lazy-instantiated TranslationEngine
        self._recording = False
        self._worker: AsrWorker | TranslationWorker | TextTranslateWorker | None = None
        self._load_worker: ModelLoadWorker | TranslatorLoadWorker | None = None
        # Side workers (e.g. Review-popup language re-translate) kept alive
        # here so Python doesn't GC the QThread before run() completes.
        self._side_workers: list = []
        self._mic_device = None if mic == "auto" else mic

    def toggle(self) -> None:
        self.toggle_signal.emit()

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
    qInstallMessageHandler(_suppress_style_warnings)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("ThunderTalk")

    from thundertalk.ui.tray import app_icon
    app.setWindowIcon(app_icon())

    settings = Settings()
    history = HistoryStore()
    pipe = Pipeline(settings)
    overlay = VoiceOverlay()
    from thundertalk.ui.review_overlay import ReviewOverlay
    review_overlay = ReviewOverlay()
    window = MainWindow(settings, history)
    tray = TrayIcon()

    # --- Startup permission checks (macOS) --------------------------------
    def _check_permissions() -> None:
        from PySide6.QtWidgets import QMessageBox

        mic_status = check_microphone()
        if mic_status == "not_determined":
            request_microphone()
        elif mic_status == "denied":
            dlg = QMessageBox(window)
            dlg.setWindowTitle("需要麦克风权限")
            dlg.setText("ThunderTalk 需要麦克风权限来进行语音识别。\n请在系统设置中开启麦克风权限。")
            dlg.setIcon(QMessageBox.Icon.Warning)
            open_btn = dlg.addButton("打开系统设置", QMessageBox.ButtonRole.AcceptRole)
            dlg.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
            dlg.exec()
            if dlg.clickedButton() == open_btn:
                open_microphone_settings()

        if not check_accessibility():
            request_accessibility()

    QTimer.singleShot(800, _check_permissions)

    # --- Hotwords + Language → ASR engine ---
    pipe.asr.set_hotwords(settings.hotwords)
    pipe.asr.set_language(settings.transcription_language)

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

        # Translation models load into TranslationEngine, not AsrEngine.
        # AsrEngine.load_model() does not understand the SeamlessM4T-v2 family
        # and would raise ValueError("Unknown model family").
        if backend == "seamless-torch":
            _start_translator_load(model_id, path)
            return

        worker = ModelLoadWorker(pipe.asr, model_id, path, family, backend)

        def _on_load_finished(mid: str) -> None:
            window.set_active_model(mid)
            tray.set_model_status(mid)
            settings.set("active_model_id", mid)
            window.models_page.set_loading(mid, False)

        def _on_load_error(mid: str, msg: str) -> None:
            window.show_load_error(f"Failed to load {mid}: {msg}")
            window.models_page.set_loading(mid, False)
            traceback.print_exc()

        worker.loaded.connect(_on_load_finished)
        worker.error.connect(_on_load_error)
        worker.finished.connect(_clear_load_worker)
        pipe._load_worker = worker
        worker.start()

    # --- Restore last active model (sync on main thread via timer) --------
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
        # Skip translation engine models — they go through _maybe_load_translator,
        # not the ASR active-model restore path.
        if info.backend == "seamless-torch":
            return
        print(f"[Startup] Loading model sync: {last_model}")
        try:
            pipe.asr.load_model(path, info.family, info.backend)
            window.set_active_model(last_model)
            tray.set_model_status(last_model)
            settings.set("active_model_id", last_model)
            print("[Startup] Model loaded OK")
        except Exception as e:
            print(f"[Startup] Model load failed: {e}")

    QTimer.singleShot(500, _restore_model)

    # --- Model loading from UI -----------------------------------------
    def on_load_model(model_id: str, path: str, family: str, backend: str) -> None:
        _start_model_load(model_id, path, family, backend)

    window.load_model_signal.connect(on_load_model)

    # --- Auto-learn hotwords -------------------------------------------
    def _on_auto_learned_word(word: str) -> None:
        print(f"[AutoLearn] New hotword: {word}")
        QTimer.singleShot(0, lambda: window.hotwords_page.add_hotword_external(word))

    set_auto_learn_callback(_on_auto_learned_word)

    def _paste_and_learn(text: str) -> None:
        keep_clipboard = not settings.get("save_to_clipboard")
        paste_text(text, keep_clipboard=keep_clipboard)
        notify_auto_learn(text)

    def _unmute_bg() -> None:
        time.sleep(0.02)
        unmute_system_audio()

    # --- Voice pipeline ------------------------------------------------
    def _on_asr_done(text: str, ms: int, dur: float, backend: str, rtf: float) -> None:
        # Audio is restored when recording stops (before ASR), not here.
        t_start = time.perf_counter()
        print("[Toggle] _on_asr_done called")
        # Note: do NOT clear pipe._worker here — the QThread's run() hasn't
        # fully unwound yet when this handler fires. Clearing now can drop the
        # last Python ref and trigger dealloc of a still-running QThread, which
        # Qt aborts on with SIGABRT. _clear_asr_worker() handles it from the
        # built-in finished signal, after run() has returned.
        print(f'[ASR] Result: "{text}" ({ms}ms, backend={backend}, RTF={rtf:.3f})')
        if text:
            overlay.hide_overlay()
            # Paste FIRST — lowest latency path to the user's target app
            _paste_and_learn(text)
            paste_dispatch_ms = int((time.perf_counter() - t_start) * 1000)
            print(f"[Toggle] Post-ASR dispatch took {paste_dispatch_ms}ms")
            # Defer non-critical UI updates so they don't block paste
            history.add(
                text=text,
                duration_secs=dur,
                inference_ms=ms,
                model=pipe.asr.current_model or "unknown",
            )
            QTimer.singleShot(50, window.home_page.refresh)
            # Post-paste watchdog: macOS may silently re-mute audio when
            # _do_paste activates the previous app.  Check after 500ms.
            if settings.get("mute_speakers"):
                QTimer.singleShot(500, ensure_audio_restored)
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
                t2t_worker.finished.connect(_clear_asr_worker)
                pipe._worker = t2t_worker
                t2t_worker.start()
        else:
            overlay.show_error("No speech detected")

    def _on_asr_error(msg: str) -> None:
        print("[Toggle] _on_asr_error called")
        print(f"[ASR] Error: {msg}")
        overlay.show_error(msg[:40])

    def _clear_asr_worker() -> None:
        # Runs on QThread's built-in finished signal, after run() has fully
        # exited. Safe to drop the last Python reference here.
        pipe._worker = None

    def _on_t2tt_done(original: str, translated: str, tgt_lang: str) -> None:
        print(
            f'[Review] T2TT done: "{original[:30]}…" → "{translated[:30]}…" ({tgt_lang})'
        )
        pipe.review_ready.emit(original, translated, tgt_lang)

    def _on_t2tt_error(msg: str) -> None:
        print(f"[Review] T2TT error: {msg}")
        # Original text is already pasted; silently drop the translation.
        # No overlay needed — user has the original; the popup just doesn't appear.

    # Grace period (ms) between stop-requested and stream-closed so the
    # audio callback can capture trailing speech that is still being spoken
    # as the user's fingers reach the hotkey. Without this, the OS driver /
    # PortAudio in-flight buffer (~50-150ms) plus any audio spoken during
    # keystroke reaction (~100-200ms) is lost, eating 1-2 trailing syllables.
    TAIL_GRACE_MS = 250

    @Slot()
    def on_toggle() -> None:
        print(f"[Toggle] on_toggle called, _recording={pipe._recording}")
        if pipe._recording:
            # ---- STOP recording ----
            t_stop = time.perf_counter()
            print(f"[Toggle] Stop requested, capturing {TAIL_GRACE_MS}ms tail...")
            overlay.show_transcribing()
            pipe._recording = False  # prevent re-entry during grace window

            def _finalize_stop() -> None:
                samples = pipe.recorder.stop()
                stop_ms = int((time.perf_counter() - t_stop) * 1000)
                if settings.get("mute_speakers"):
                    print(f"[Toggle] Restoring system audio ({stop_ms}ms total)")
                    threading.Thread(target=_unmute_bg, daemon=True).start()

                if samples is None or len(samples) < 800:
                    print("[Toggle] Too short (audio already restored on stop)")
                    overlay.show_error("Too short")
                    return

                tgt = settings.get("translation_target")
                mode = settings.get("translation_mode") or "direct"

                # Direct mode: SeamlessM4T S2TT (audio → translated text directly)
                if tgt and tgt != "off" and mode == "direct":
                    translator = pipe.get_translator()
                    if not translator.is_loaded:
                        print(f"[Toggle] Direct translation but model not loaded")
                        overlay.show_error("Translation model not loaded")
                        return
                    print(f"[Toggle] Starting Direct translation → {tgt} on {len(samples)} samples")
                    worker = TranslationWorker(translator, samples, tgt)
                    worker.done.connect(_on_asr_done)
                    worker.error.connect(_on_asr_error)
                    worker.finished.connect(_clear_asr_worker)
                    pipe._worker = worker
                    worker.start()
                    return

                # Off mode OR Review mode: route through ASR first.
                # Review mode adds T2TT in _on_asr_done after the ASR result
                # is pasted, then shows the review popup.
                if not pipe.asr.is_loaded:
                    print("[Toggle] No ASR model (audio already restored on stop)")
                    overlay.show_error("No model loaded")
                    return

                if tgt and tgt != "off" and mode == "review":
                    print(f"[Toggle] Starting Review (ASR → T2TT → popup) on {len(samples)} samples")
                else:
                    print(f"[Toggle] Starting ASR on {len(samples)} samples")
                worker = AsrWorker(pipe.asr, samples)
                worker.done.connect(_on_asr_done)
                worker.error.connect(_on_asr_error)
                worker.finished.connect(_clear_asr_worker)
                pipe._worker = worker
                worker.start()

            QTimer.singleShot(TAIL_GRACE_MS, _finalize_stop)
        else:
            # ---- START recording ----
            if not pipe.asr.is_loaded:
                overlay.show_error("Load a model first")
                return
            # Dismiss any leftover Review popup from a previous round
            review_overlay.hide_review()
            save_frontmost_app()
            # Show overlay immediately so user gets instant visual feedback
            overlay.show_recording()
            app.processEvents()
            mute_on = settings.get("mute_speakers")
            print(f"[Toggle] Starting recording, mute_speakers={mute_on}")
            # Start mic BEFORE muting: muting Bluetooth speakers can trigger
            # a profile switch (A2DP→HFP) that changes the default input device.
            mic = settings.microphone
            pipe.recorder.start(device=None if mic == "auto" else mic)
            if mute_on:
                threading.Thread(target=mute_system_audio, daemon=True).start()
            pipe._recording = True
            print("[Toggle] Recording started")

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
            print(f"[Review] Lang change requested but translator not loaded")
            return
        from thundertalk.core.translate import detect_src_lang
        src_lang = detect_src_lang(original)
        print(f"[Review] Re-translate {src_lang}→{new_lang}")
        worker = TextTranslateWorker(translator, original, src_lang, new_lang)
        worker.done.connect(_on_t2tt_done)
        worker.error.connect(_on_t2tt_error)
        # Strong-reference the worker until run() finishes; otherwise
        # Python GCs the QThread mid-execution and Qt aborts with
        # 'QThread: Destroyed while thread is still running'.
        pipe._side_workers.append(worker)

        def _cleanup_side_worker() -> None:
            try:
                pipe._side_workers.remove(worker)
            except ValueError:
                pass
            worker.deleteLater()

        worker.finished.connect(_cleanup_side_worker)
        worker.start()

    review_overlay.lang_change_requested.connect(_on_review_lang_changed)

    # Feed live mic level into the overlay waveform — only runs while
    # recording so idle CPU stays near zero.
    _level_timer = QTimer()
    _level_timer.setInterval(40)
    _level_timer.timeout.connect(lambda: overlay.set_audio_level(pipe.recorder.current_rms))

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

    # --- Hotkey --------------------------------------------------------
    hotkey = HotkeyListener(on_toggle=pipe.toggle, key_name=settings.hotkey)
    hotkey.start()

    def _on_hotkey_setting_changed(key_name: str) -> None:
        hotkey.set_hotkey(key_name)

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

        Skips if target == 'off' or model missing. Runs the heavy load on a
        background thread so app startup / settings changes never block the UI.
        """
        tgt = settings.get("translation_target")
        if not tgt or tgt == "off":
            return
        from thundertalk.core.models import is_downloaded, get_model_path
        if not is_downloaded("seamless-m4t-v2-large"):
            print("[Translate] Target set but model not downloaded; skipping load")
            return
        translator = pipe.get_translator()
        if translator.is_loaded:
            return

        model_path = get_model_path("seamless-m4t-v2-large")
        if not model_path:
            return

        # Visible spinner on the SeamlessM4T card so users understand
        # the ~10s torch+MPS load isn't a stuck UI.
        window.models_page.set_loading("seamless-m4t-v2-large", True)

        def _on_done() -> None:
            window.models_page.set_loading("seamless-m4t-v2-large", False)
            window.models_page.set_translator_active("seamless-m4t-v2-large")

        def _on_fail() -> None:
            window.models_page.set_loading("seamless-m4t-v2-large", False)

        def _load() -> None:
            try:
                translator.load_model(model_path)
                QTimer.singleShot(0, _on_done)
            except Exception:
                traceback.print_exc()
                QTimer.singleShot(0, _on_fail)

        threading.Thread(target=_load, daemon=True).start()

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

    # --- Tray ----------------------------------------------------------
    def _show_settings_window() -> None:
        activate_app()
        window.show()
        window.raise_()
        window.activateWindow()

    tray.open_action.triggered.connect(_show_settings_window)
    tray.quit_action.triggered.connect(app.quit)
    tray.show()

    window.show()
    window.raise_()

    import atexit
    atexit.register(force_unmute)

    app.aboutToQuit.connect(lambda: (hotkey.stop(), force_unmute()))

    sys.exit(app.exec())
