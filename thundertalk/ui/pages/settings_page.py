"""Settings — hotkey, audio, transcription, general. One scrolling page.

Every row goes through FormCard, so a live language switch re-labels the
whole page (it used to leave most rows stale until restart).
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from thundertalk.core.i18n import bus as i18n_bus, set_language, t
from thundertalk.ui import theme
from thundertalk.ui.keys import split_combo
from thundertalk.ui.widgets import FormCard, IconButton, KeyCaps, PageHeader, SectionLabel, column_scroll

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings


# ── Hotkey capture ──────────────────────────────────────────────────────

_MAC_NATIVE_VK: dict[int, str] = {
    0x37: "cmd_l", 0x36: "cmd_r",
    0x3A: "alt_l", 0x3D: "alt_r",
    0x38: "shift_l", 0x3C: "shift_r",
    0x3B: "ctrl_l", 0x3E: "ctrl_r",
}

_MODIFIER_NAMES = {
    "cmd", "cmd_l", "cmd_r", "alt", "alt_l", "alt_r",
    "ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r",
}

# Keys that are safe to bind on their own (they don't type a character).
_STANDALONE_OK = {
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "caps_lock", "home", "end", "page_up", "page_down",
}


def _is_modifier(key_name: str) -> bool:
    return key_name.lower().strip() in _MODIFIER_NAMES


def combo_is_safe(combo: str) -> bool:
    """A global hotkey must not fire while typing: it needs a modifier, or be
    a modifier / function key by itself. A bare letter would start a
    recording on every keystroke."""
    parts = [p.lower() for p in split_combo(combo)]
    if not parts:
        return False
    if any(_is_modifier(p) for p in parts):
        return True
    return len(parts) == 1 and parts[0] in _STANDALONE_OK


def _qt_key_to_name(ev: QKeyEvent) -> str:
    from PySide6.QtCore import Qt as QtKey

    if platform.system() == "Darwin":
        native_vk = ev.nativeVirtualKey()
        if native_vk in _MAC_NATIVE_VK:
            return _MAC_NATIVE_VK[native_vk]

    special: dict[int, str] = {
        QtKey.Key.Key_F1: "f1", QtKey.Key.Key_F2: "f2", QtKey.Key.Key_F3: "f3",
        QtKey.Key.Key_F4: "f4", QtKey.Key.Key_F5: "f5", QtKey.Key.Key_F6: "f6",
        QtKey.Key.Key_F7: "f7", QtKey.Key.Key_F8: "f8", QtKey.Key.Key_F9: "f9",
        QtKey.Key.Key_F10: "f10", QtKey.Key.Key_F11: "f11", QtKey.Key.Key_F12: "f12",
        QtKey.Key.Key_Space: "space",
        QtKey.Key.Key_Escape: "esc",
        QtKey.Key.Key_CapsLock: "caps_lock",
        QtKey.Key.Key_Tab: "tab",
        QtKey.Key.Key_Backspace: "backspace",
        QtKey.Key.Key_Delete: "delete",
        QtKey.Key.Key_Home: "home",
        QtKey.Key.Key_End: "end",
        QtKey.Key.Key_PageUp: "page_up",
        QtKey.Key.Key_PageDown: "page_down",
        QtKey.Key.Key_Right: "right",
        QtKey.Key.Key_Left: "left",
        QtKey.Key.Key_Up: "up",
        QtKey.Key.Key_Down: "down",
        QtKey.Key.Key_Shift: "shift_l",
        QtKey.Key.Key_Control: "cmd_l",
        QtKey.Key.Key_Meta: "ctrl_l",
        QtKey.Key.Key_Alt: "alt_l",
    }
    k = ev.key()
    if k in special:
        return special[k]
    text = ev.text()
    if text and text.isprintable() and len(text) == 1:
        return text.lower()
    return ""


class HotkeyCapture(QWidget):
    """Click, then press a combo. Shows the current hotkey as key caps and
    live-echoes held modifiers while capturing. Esc cancels; unsafe combos
    (a bare letter) are rejected with an explanation."""

    key_captured = Signal(str)
    capture_started = Signal()
    capture_ended = Signal()

    def __init__(self, current: str) -> None:
        super().__init__()
        self._capturing = False
        self._combo_str = current
        self._held_modifiers: list[str] = []
        self._modifier_timer_id: int | None = None
        self._error = ""
        self._hover = False
        self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        ly = QVBoxLayout(self)
        ly.setContentsMargins(0, 16, 0, 12)
        ly.setSpacing(8)
        ly.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._caps = KeyCaps(current, height=40, accent=False)
        ly.addWidget(self._caps, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._hint = QLabel(t("settings.hotkey.click_to_change"))
        self._hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")
        ly.addWidget(self._hint)
        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self._clear_error)

    # ── state ──
    def combo(self) -> str:
        return self._combo_str

    def retranslate(self) -> None:
        self._sync_labels()

    def _sync_labels(self) -> None:
        if self._error:
            self._hint.setText(self._error)
            self._hint.setStyleSheet(f"color: {theme.WARNING}; font-size: 12px; background: transparent;")
        elif self._capturing:
            self._hint.setText(t("settings.hotkey.press_keys") + "   ·   Esc")
            self._hint.setStyleSheet(f"color: {theme.ACCENT_ORANGE}; font-size: 12px; font-weight: 600; background: transparent;")
        else:
            self._hint.setText(t("settings.hotkey.click_to_change"))
            self._hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; background: transparent;")

    def _clear_error(self) -> None:
        self._error = ""
        self._sync_labels()

    def _show_caps(self) -> None:
        if self._capturing:
            if self._held_modifiers:
                self._caps.set_combo("+".join(self._held_modifiers))
                self._caps.show()
            else:
                self._caps.hide()
        else:
            self._caps.set_combo(self._combo_str)
            self._caps.show()

    # ── interaction ──
    def mousePressEvent(self, ev) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._error = ""
        self._held_modifiers.clear()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.grabKeyboard()
        self.capture_started.emit()
        self._show_caps()
        self._sync_labels()
        self.update()

    def _stop_capture(self) -> None:
        self._capturing = False
        self._held_modifiers.clear()
        if self._modifier_timer_id is not None:
            self.killTimer(self._modifier_timer_id)
            self._modifier_timer_id = None
        self.releaseKeyboard()
        self._show_caps()
        self._sync_labels()
        self.capture_ended.emit()
        self.update()

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        if not self._capturing:
            return super().keyPressEvent(ev)
        if ev.key() == Qt.Key.Key_Escape:
            self._stop_capture()               # Esc cancels — it used to bind Esc as the hotkey
            return

        key_name = _qt_key_to_name(ev)
        if not key_name:
            return
        if self._modifier_timer_id is not None:
            self.killTimer(self._modifier_timer_id)
            self._modifier_timer_id = None

        if _is_modifier(key_name):
            if key_name not in self._held_modifiers:
                self._held_modifiers.append(key_name)
            self._show_caps()
            self._modifier_timer_id = self.startTimer(1500)
            self.update()
            return
        self._finalize("+".join(self._held_modifiers + [key_name]))

    def timerEvent(self, ev) -> None:
        if ev.timerId() == self._modifier_timer_id:
            self.killTimer(self._modifier_timer_id)
            self._modifier_timer_id = None
            if self._held_modifiers:
                self._finalize("+".join(self._held_modifiers))

    def _finalize(self, combo: str) -> None:
        if not combo_is_safe(combo):
            self._error = t("settings.hotkey.needs_modifier")
            self._error_timer.start(4000)
            self._held_modifiers.clear()
            self._stop_capture()
            return
        self._combo_str = combo
        self._stop_capture()
        self.key_captured.emit(combo)

    def focusOutEvent(self, ev) -> None:
        if self._capturing:
            self._stop_capture()
        super().focusOutEvent(ev)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        if self._capturing:
            p.fillPath(path, QColor(217, 72, 15, 12))
            pen = QPen(QColor(theme.ACCENT_ORANGE), 1.4)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
        else:
            p.fillPath(path, QColor(31, 30, 27, 10 if self._hover else 5))
            p.setPen(QPen(theme._BORDER_STRONG_C if self._hover else theme._BORDER_DEFAULT_C, 1))
        p.drawPath(path)
        p.end()


# ── SettingsPage ────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    hotkey_changed = Signal(str)
    settings_changed = Signal()
    capture_started = Signal()
    capture_ended = Signal()
    run_setup_requested = Signal()

    def __init__(self, settings: "Settings") -> None:
        super().__init__()
        self._settings = settings
        self._cards: list[FormCard] = []
        self._section_labels: list[SectionLabel] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll, self._col = column_scroll(spacing=14)
        root.addWidget(scroll)

        self._header = PageHeader(t("settings.title"), t("settings.subtitle"))
        self._col.addWidget(self._header)
        self._col.addSpacing(6)

        self._build_hotkey()
        self._build_audio()
        self._build_transcription()
        self._build_general()
        self._col.addStretch()

        i18n_bus.language_changed.connect(self.retranslate)

        # Live mic dropdown: react to hot-plug, BT pairing, and BT profile
        # transitions surfaced by DeviceWatcher.
        from thundertalk.core.device_watcher import get_watcher
        get_watcher().devices_changed.connect(self._apply_device_names)

    # ── helpers ──
    def _section(self, icon: str, key: str) -> None:
        self._col.addSpacing(8)
        lbl = SectionLabel(icon, key)
        self._section_labels.append(lbl)
        self._col.addWidget(lbl)

    def _card(self, title_key: str | None = None, badge: QWidget | None = None) -> FormCard:
        card = FormCard(title_key, badge)
        self._cards.append(card)
        self._col.addWidget(card)
        return card

    @staticmethod
    def _combo(width: int) -> QComboBox:
        c = QComboBox()
        c.setFixedWidth(width)
        c.setFixedHeight(38)
        theme.style_combo(c)
        return c

    # ── Hotkey ──
    def _build_hotkey(self) -> None:
        self._section("keyboard", "settings.tab_hotkey")
        card = self._card("settings.section.activation_hotkey")
        self._hotkey_capture = HotkeyCapture(self._settings.hotkey)
        self._hotkey_capture.key_captured.connect(self._on_hotkey_changed)
        self._hotkey_capture.capture_started.connect(self.capture_started.emit)
        self._hotkey_capture.capture_ended.connect(self.capture_ended.emit)
        card.add_widget(self._hotkey_capture)

        self._mode_combo = self._combo(170)
        self._mode_combo.addItem(t("settings.mode.toggle_click"))
        # "Hold to record" stays hidden until it is stable; force toggle.
        self._settings.set("press_mode", "toggle")
        self._mode_combo.currentIndexChanged.connect(lambda i: self._set_mode("toggle"))
        card.add_row("settings.section.activation_mode", "settings.section.activation_mode_desc",
                     self._mode_combo, sep=True)

    def _on_hotkey_changed(self, key_name: str) -> None:
        self._settings.set("hotkey", key_name)
        self.hotkey_changed.emit(key_name)

    def _set_mode(self, mode: str) -> None:
        self._settings.set("press_mode", mode)
        self.settings_changed.emit()

    # ── Audio ──
    def _build_audio(self) -> None:
        self._section("mic", "settings.tab_audio")
        card = self._card("settings.section.input_device")

        mic_ctl = QWidget()
        mic_ctl.setStyleSheet("background: transparent;")
        h = QHBoxLayout(mic_ctl)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self._mic_combo = self._combo(250)
        self._mic_combo.addItem(t("settings.mic.auto"))
        self._refresh_mic_list()
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        h.addWidget(self._mic_combo)
        rescan = IconButton("refresh", 34, t("settings.mic.rescan"))
        rescan.clicked.connect(lambda: self._refresh_mic_list(force_rescan=True))
        h.addWidget(rescan)
        card.add_row("settings.mic.label", "settings.mic.desc", mic_ctl)

        card2 = self._card("settings.section.recording")
        self._mute_toggle = theme.ToggleSwitch(self._settings.get("mute_speakers"))
        self._mute_toggle.toggled_signal.connect(lambda v: self._settings.set("mute_speakers", v))
        card2.add_row("settings.mute.label", "settings.mute.desc", self._mute_toggle)

    def _refresh_mic_list(self, *, force_rescan: bool = False) -> None:
        """Rebuild the mic dropdown from AudioRecorder's executor-backed
        device list. ``force_rescan=True`` triggers a Pa_Terminate+Initialize
        cycle on the audio worker thread (with watchdog)."""
        from thundertalk.core.audio import AudioRecorder
        names = (AudioRecorder.refresh_devices() if force_rescan
                 else AudioRecorder.list_devices())
        self._apply_device_names(names)

    def _apply_device_names(self, names: list[str]) -> None:
        """Repopulate the mic combo from a pre-fetched list (must NOT call
        back into AudioRecorder — the watcher already paid that cost)."""
        self._mic_combo.blockSignals(True)
        while self._mic_combo.count() > 1:
            self._mic_combo.removeItem(1)
        for name in names:
            self._mic_combo.addItem(name)
        current = self._settings.microphone
        if current != "auto":
            idx = self._mic_combo.findText(current)
            if idx >= 0:
                self._mic_combo.setCurrentIndex(idx)
        self._mic_combo.blockSignals(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_mic_list()

    def _on_mic_changed(self, idx: int) -> None:
        self._settings.set("microphone", "auto" if idx == 0 else self._mic_combo.currentText())

    # ── Transcription ──
    def _build_transcription(self) -> None:
        self._section("waveform", "settings.tab_transcription")

        card = self._card("settings.section.language")
        self._lang_combo = self._combo(190)
        self._lang_codes = ["auto", "en", "zh", "ja", "ko", "es", "fr", "de", "ar", "hi", "it", "pt", "ru", "nl", "tr"]
        for code in self._lang_codes:
            self._lang_combo.addItem(t(f"settings.recog.{code}"), code)
        cur = self._settings.transcription_language
        if cur in self._lang_codes:
            self._lang_combo.setCurrentIndex(self._lang_codes.index(cur))
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        card.add_row("settings.recog.label", "settings.recog.desc", self._lang_combo)

        card2 = self._card("settings.section.output")
        ct = theme.ToggleSwitch(self._settings.get("save_to_clipboard"))
        ct.toggled_signal.connect(lambda v: self._settings.set("save_to_clipboard", v))
        card2.add_row("settings.clipboard.label", "settings.clipboard.desc", ct)
        self._live_toggle = theme.ToggleSwitch(self._settings.get("live_preview"))
        self._live_toggle.toggled_signal.connect(lambda v: self._settings.set("live_preview", v))
        card2.add_row("settings.live_preview.label", "settings.live_preview.desc",
                      self._live_toggle, sep=True)

        card3 = self._card("settings.llm_rewrite.label", theme.badge(t("common.experimental"), "orange"))
        rw_toggle = theme.ToggleSwitch(self._settings.get("llm_rewrite_enabled"))
        rw_toggle.toggled_signal.connect(lambda v: self._settings.set("llm_rewrite_enabled", v))
        card3.add_row("settings.llm_rewrite.label", "settings.llm_rewrite.desc", rw_toggle)

        self._rewrite_model_edit = QLineEdit()
        self._rewrite_model_edit.setFixedWidth(300)
        self._rewrite_model_edit.setFixedHeight(38)
        self._rewrite_model_edit.setPlaceholderText("mlx-community/Qwen3-8B-4bit")
        self._rewrite_model_edit.setText(
            self._settings.get("llm_rewrite_model") or "mlx-community/Qwen3-8B-4bit")
        self._rewrite_model_edit.setStyleSheet(theme.INPUT_QSS)
        self._rewrite_model_edit.editingFinished.connect(
            lambda: self._settings.set("llm_rewrite_model", self._rewrite_model_edit.text().strip()))
        card3.add_row("settings.llm_rewrite.model_label", "settings.llm_rewrite.model_hint",
                      self._rewrite_model_edit, sep=True)

    def _on_lang_changed(self, idx: int) -> None:
        code = self._lang_combo.itemData(idx)
        if code:
            self._settings.set("transcription_language", code)
            self.settings_changed.emit()

    # ── General ──
    def _build_general(self) -> None:
        self._section("sliders", "settings.tab_general")

        card = self._card("settings.section.appearance")
        self._ui_lang_combo = self._combo(170)
        self._ui_lang_combo.addItem("English", "en")
        self._ui_lang_combo.addItem("中文", "zh")
        cur_lang = self._settings.get("language") or "en"
        self._ui_lang_combo.setCurrentIndex(1 if cur_lang == "zh" else 0)
        self._ui_lang_combo.currentIndexChanged.connect(self._on_ui_lang_changed)
        card.add_row("settings.language", "settings.language_desc", self._ui_lang_combo)

        card2 = self._card("settings.section.startup")
        t1 = theme.ToggleSwitch(self._settings.get("launch_at_startup"))

        def _on_launch_toggled(v: bool) -> None:
            self._settings.set("launch_at_startup", v)
            from thundertalk.core import autostart
            ok, err = autostart.set_enabled(v)
            if not ok:
                print(f"[Settings] autostart.set_enabled({v}) failed: {err}")

        t1.toggled_signal.connect(_on_launch_toggled)
        card2.add_row("settings.startup.launch.label", "settings.startup.launch.desc", t1)
        t2 = theme.ToggleSwitch(self._settings.get("silent_launch"))
        t2.toggled_signal.connect(lambda v: self._settings.set("silent_launch", v))
        card2.add_row("settings.startup.silent.label", "settings.startup.silent.desc", t2, sep=True)

        perf = self._card("settings.section.performance")
        self._mem_combo = self._combo(230)
        self._mem_combo.addItem(t("settings.memory.high"), "high")
        self._mem_combo.addItem(t("settings.memory.low"), "low")
        cur_mem = self._settings.memory_mode
        for i in range(self._mem_combo.count()):
            if self._mem_combo.itemData(i) == cur_mem:
                self._mem_combo.setCurrentIndex(i)
                break
        self._mem_combo.currentIndexChanged.connect(self._on_memory_mode_changed)
        perf.add_row("settings.memory.label", "settings.memory.desc", self._mem_combo)
        self._mem_restart_hint = QLabel(t("settings.memory.restart_hint"))
        self._mem_restart_hint.setStyleSheet(
            f"color: {theme.WARNING}; font-size: 12px; background: transparent;")
        self._mem_restart_hint.setWordWrap(True)
        self._mem_restart_hint.hide()
        perf.add_widget(self._mem_restart_hint)

        logs = self._card("settings.section.logs")
        lt = theme.ToggleSwitch(self._settings.get("log_enabled"))
        lt.toggled_signal.connect(lambda v: self._settings.set("log_enabled", v))
        logs.add_row("settings.logs.enable.label", "settings.logs.enable.desc", lt)
        self._open_btn = theme.make_button(t("settings.logs.open"), "secondary", 34, font_px=12)
        self._open_btn.setMinimumWidth(120)
        self._open_btn.clicked.connect(self._open_log_dir)
        logs.add_row("settings.logs.dir", "", self._open_btn, sep=True)

        setup = self._card("onb.rerun")
        self._setup_btn = theme.make_button(t("onb.rerun"), "secondary", 34, font_px=12)
        self._setup_btn.setMinimumWidth(170)
        self._setup_btn.clicked.connect(self.run_setup_requested)
        setup.add_row("onb.rerun", "onb.rerun.desc", self._setup_btn)

    def _on_ui_lang_changed(self, idx: int) -> None:
        code = self._ui_lang_combo.itemData(idx)
        if code:
            self._settings.set("language", code)
            set_language(code)

    def retranslate(self) -> None:
        self._header.set_title(t("settings.title"))
        self._header.set_subtitle(t("settings.subtitle"))
        for lbl in self._section_labels:
            lbl.retranslate()
        for card in self._cards:
            card.retranslate()
        self._hotkey_capture.retranslate()
        # Dropdown entries that carry translated text
        self._mode_combo.setItemText(0, t("settings.mode.toggle_click"))
        for i, code in enumerate(self._lang_codes):
            self._lang_combo.setItemText(i, t(f"settings.recog.{code}"))
        self._mem_combo.setItemText(0, t("settings.memory.high"))
        self._mem_combo.setItemText(1, t("settings.memory.low"))
        self._mem_restart_hint.setText(t("settings.memory.restart_hint"))
        self._open_btn.setText(t("settings.logs.open"))
        self._setup_btn.setText(t("onb.rerun"))
        self._mic_combo.setItemText(0, t("settings.mic.auto"))

    def _on_memory_mode_changed(self, idx: int) -> None:
        mode = self._mem_combo.itemData(idx)
        if mode not in ("high", "low") or mode == self._settings.memory_mode:
            return
        self._settings.set("memory_mode", mode)
        # ASR is already loaded with the previous setting; the new value
        # only takes effect on the next model load.
        self._mem_restart_hint.show()

    def _open_log_dir(self) -> None:
        log_dir = Path.home() / ".thundertalk"
        log_dir.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            subprocess.run(["open", str(log_dir)], check=False)
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", str(log_dir)], check=False)
        elif platform.system() == "Windows":
            subprocess.run(["explorer", str(log_dir)], check=False)
