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


class ModelLoadWorker(QThread):
    """Loads an ASR model off the main thread."""

    finished = Signal(str)      # model_id
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
            self.finished.emit(self._model_id)
        except Exception as e:
            print(f"[ModelLoad] ERROR: {e}")
            self.error.emit(self._model_id, str(e))


class Pipeline(QObject):
    """Bridges hotkey events (from a background thread) into Qt signals."""

    toggle_signal = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        mic = settings.microphone
        self.recorder = AudioRecorder()
        self.asr = AsrEngine()
        self._recording = False
        self._worker: AsrWorker | None = None
        self._load_worker: ModelLoadWorker | None = None
        self._mic_device = None if mic == "auto" else mic

    def toggle(self) -> None:
        self.toggle_signal.emit()


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
    def _start_model_load(model_id: str, path: str, family: str, backend: str) -> None:
        """Start loading a model in a background thread."""
        if pipe._load_worker and pipe._load_worker.isRunning():
            window.show_load_error("Another model is already loading, please wait.")
            return

        window.models_page.set_loading(model_id, True)
        worker = ModelLoadWorker(pipe.asr, model_id, path, family, backend)

        def _on_load_finished(mid: str) -> None:
            pipe._load_worker = None
            window.set_active_model(mid)
            tray.set_model_status(mid)
            settings.set("active_model_id", mid)
            window.models_page.set_loading(mid, False)

        def _on_load_error(mid: str, msg: str) -> None:
            pipe._load_worker = None
            window.show_load_error(f"Failed to load {mid}: {msg}")
            window.models_page.set_loading(mid, False)
            traceback.print_exc()

        worker.finished.connect(_on_load_finished)
        worker.error.connect(_on_load_error)
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
        # Don't block with wait() — the signal firing already means run() finished.
        # Just clear the reference so it can be garbage-collected.
        pipe._worker = None
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
        else:
            overlay.show_error("No speech detected")

    def _on_asr_error(msg: str) -> None:
        print("[Toggle] _on_asr_error called")
        pipe._worker = None
        print(f"[ASR] Error: {msg}")
        overlay.show_error(msg[:40])

    @Slot()
    def on_toggle() -> None:
        print(f"[Toggle] on_toggle called, _recording={pipe._recording}")
        if pipe._recording:
            # ---- STOP recording ----
            t_stop = time.perf_counter()
            print("[Toggle] Stopping recording...")
            overlay.show_transcribing()
            samples = pipe.recorder.stop()
            stop_ms = int((time.perf_counter() - t_stop) * 1000)
            if settings.get("mute_speakers"):
                print(f"[Toggle] Restoring system audio ({stop_ms}ms to stop recorder)")
                threading.Thread(target=_unmute_bg, daemon=True).start()
            pipe._recording = False

            if samples is None or len(samples) < 800:
                print("[Toggle] Too short (audio already restored on stop)")
                overlay.show_error("Too short")
                return

            if not pipe.asr.is_loaded:
                print("[Toggle] No model (audio already restored on stop)")
                overlay.show_error("No model loaded")
                return

            print(f"[Toggle] Starting ASR on {len(samples)} samples")
            worker = AsrWorker(pipe.asr, samples)
            worker.done.connect(_on_asr_done)
            worker.error.connect(_on_asr_error)
            pipe._worker = worker
            worker.start()
        else:
            # ---- START recording ----
            if not pipe.asr.is_loaded:
                overlay.show_error("Load a model first")
                return
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
