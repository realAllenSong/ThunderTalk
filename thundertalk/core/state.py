"""Shared, observable application state.

The pipeline (app.py) is the only writer; the UI (home hero, sidebar status,
tray, onboarding) subscribes. Keeping this in one small QObject means every
surface tells the user the same truth about "can I dictate right now?".
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal

# Model lifecycle
MODEL_NONE = "none"          # nothing selected / nothing downloaded
MODEL_LOADING = "loading"    # weights being loaded into memory
MODEL_READY = "ready"        # can transcribe
MODEL_ERROR = "error"        # last load failed

# Recording lifecycle
REC_IDLE = "idle"
REC_RECORDING = "recording"
REC_TRANSCRIBING = "transcribing"


def model_display_name(model_id: Optional[str]) -> str:
    """Friendly name for a model id ('Qwen3-ASR-0.6B · MLX fp16')."""
    if not model_id:
        return ""
    from thundertalk.core.models import BUILTIN_MODELS
    info = next((m for m in BUILTIN_MODELS if m.id == model_id), None)
    if info is None:
        return model_id
    return f"{info.name} · {info.variant}"


class AppState(QObject):
    model_changed = Signal()
    recording_changed = Signal(str)
    permissions_changed = Signal()
    hotkey_changed = Signal(str)
    level_changed = Signal(float)     # live mic RMS 0..1 while recording

    def __init__(self, hotkey: str = "cmd_r") -> None:
        super().__init__()
        self._model_status = MODEL_NONE
        self._model_id = ""
        self._model_error = ""
        self._recording = REC_IDLE
        self._hotkey = hotkey
        self._mic = "authorized"
        self._accessibility = True

    # ── model ──
    @property
    def model_status(self) -> str:
        return self._model_status

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_name(self) -> str:
        return model_display_name(self._model_id)

    @property
    def model_error(self) -> str:
        return self._model_error

    def set_model_loading(self, model_id: str) -> None:
        self._model_status, self._model_id, self._model_error = MODEL_LOADING, model_id, ""
        self.model_changed.emit()

    def set_model_ready(self, model_id: str) -> None:
        self._model_status, self._model_id, self._model_error = MODEL_READY, model_id, ""
        self.model_changed.emit()

    def set_model_error(self, model_id: str, message: str) -> None:
        self._model_status, self._model_id, self._model_error = MODEL_ERROR, model_id, message
        self.model_changed.emit()

    def set_model_none(self) -> None:
        self._model_status, self._model_id, self._model_error = MODEL_NONE, "", ""
        self.model_changed.emit()

    # ── recording ──
    @property
    def recording(self) -> str:
        return self._recording

    def set_recording(self, state: str) -> None:
        if state != self._recording:
            self._recording = state
            self.recording_changed.emit(state)

    def push_level(self, rms: float) -> None:
        self.level_changed.emit(rms)

    # ── hotkey ──
    @property
    def hotkey(self) -> str:
        return self._hotkey

    def set_hotkey(self, combo: str) -> None:
        if combo != self._hotkey:
            self._hotkey = combo
            self.hotkey_changed.emit(combo)

    # ── permissions ──
    @property
    def mic_status(self) -> str:
        return self._mic

    @property
    def accessibility_ok(self) -> bool:
        return self._accessibility

    @property
    def permissions_ok(self) -> bool:
        return self._mic == "authorized" and self._accessibility

    def refresh_permissions(self) -> None:
        """Re-read the OS permission state; emits only when it changed."""
        from thundertalk.core.platform_utils import check_accessibility, check_microphone
        mic, acc = check_microphone(), check_accessibility()
        if mic != self._mic or acc != self._accessibility:
            self._mic, self._accessibility = mic, acc
            self.permissions_changed.emit()
