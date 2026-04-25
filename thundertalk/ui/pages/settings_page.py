"""Settings page — segment-control tabs: Hotkey, Audio, Transcription, System.

Layout and cards match 闪电说 style:
- Rounded segment pill tab bar at top center
- Card sections with bold in-card titles
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

import sounddevice as sd

from thundertalk.core.i18n import bus as i18n_bus, set_language, t
from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings


# Curated SeamlessM4T v2 target languages (ISO-639-3).
# Korean (kor) intentionally excluded — model produces malformed output
# for that target (see spike notes in docs/plans/2026-04-24-translation-seamlessm4t.md).
TRANSLATION_TARGETS: list[tuple[str, str]] = [
    ("off", "off"),  # display text fetched via i18n at runtime
    ("eng", "English"),
    ("cmn", "中文 (Chinese)"),
    ("jpn", "日本語 (Japanese)"),
    ("spa", "Español (Spanish)"),
    ("fra", "Français (French)"),
    ("deu", "Deutsch (German)"),
    ("por", "Português (Portuguese)"),
    ("rus", "Русский (Russian)"),
    ("ita", "Italiano (Italian)"),
    ("arb", "العربية (Arabic)"),
    ("hin", "हिन्दी (Hindi)"),
]


# ── Hotkey Capture Widget ───────────────────────────────────────────────

class HotkeyCapture(QWidget):
    """Click the capsule, press a key combo to set hotkey.

    Displays current hotkey as key-cap badges.
    While capturing: shows held modifiers live.
    """

    key_captured = Signal(str)
    capture_started = Signal()
    capture_ended = Signal()

    def __init__(self, current: str) -> None:
        super().__init__()
        self._capturing = False
        self._combo_str = current
        self._display = _display_combo(current)
        self._saved_display = self._display
        self._held_modifiers: list[str] = []
        self._modifier_timer_id: int | None = None
        self.setFixedHeight(80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, ev) -> None:
        self._capturing = True
        self._held_modifiers.clear()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.grabKeyboard()
        self.capture_started.emit()
        self.update()

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        if not self._capturing:
            return super().keyPressEvent(ev)

        key_name = _qt_key_to_name(ev)
        if not key_name:
            return

        if self._modifier_timer_id is not None:
            self.killTimer(self._modifier_timer_id)
            self._modifier_timer_id = None

        if _is_modifier(key_name):
            if key_name not in self._held_modifiers:
                self._held_modifiers.append(key_name)
            self._display = _display_combo("+".join(self._held_modifiers) + "+…")
            self._modifier_timer_id = self.startTimer(1500)
            self.update()
            return

        parts = self._held_modifiers + [key_name]
        combo = "+".join(parts)
        self._finalize(combo)

    def keyReleaseEvent(self, ev: QKeyEvent) -> None:
        if not self._capturing:
            return super().keyReleaseEvent(ev)

    def timerEvent(self, ev) -> None:
        if ev.timerId() == self._modifier_timer_id:
            self.killTimer(self._modifier_timer_id)
            self._modifier_timer_id = None
            if self._held_modifiers:
                combo = "+".join(self._held_modifiers)
                self._finalize(combo)

    def _finalize(self, combo: str) -> None:
        self._capturing = False
        self.releaseKeyboard()
        self._combo_str = combo
        self._display = _display_combo(combo)
        self._saved_display = self._display
        self._held_modifiers.clear()
        self.key_captured.emit(combo)
        self.capture_ended.emit()
        self.update()

    def focusOutEvent(self, ev) -> None:
        if self._capturing:
            self._capturing = False
            self._display = self._saved_display
            self._held_modifiers.clear()
            if self._modifier_timer_id is not None:
                self.killTimer(self._modifier_timer_id)
                self._modifier_timer_id = None
            self.releaseKeyboard()
            self.capture_ended.emit()
            self.update()
        super().focusOutEvent(ev)

    def paintEvent(self, ev) -> None:
        from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen
        from PySide6.QtCore import QRectF

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0, 0, self.width(), self.height())
        bg = QPainterPath()
        bg.addRoundedRect(rect, 14, 14)

        if self._capturing:
            p.fillPath(bg, QColor(theme.BG_ELEVATED))
            p.setPen(QPen(QColor(theme.ACCENT_BLUE), 1.5))
            p.drawPath(bg)
            p.setFont(theme.font(14))
            p.setPen(QColor(theme.ACCENT_BLUE))
            label = self._display if self._held_modifiers else "Press keys…"
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        else:
            p.fillPath(bg, QColor(theme.BG_ELEVATED))
            p.setPen(QPen(QColor(theme.BORDER_SUBTLE), 1))
            p.drawPath(bg)

            # Draw key-cap badges
            parts = [pt.strip() for pt in self._combo_str.split("+") if pt.strip()]
            displayed = [_display_single(pt) for pt in parts]

            total_width = 0
            cap_widths = []
            for d in displayed:
                w = max(38, len(d) * 11 + 20)
                cap_widths.append(w)
                total_width += w
            total_width += (len(displayed) - 1) * 8

            start_x = (self.width() - total_width) / 2
            cap_h = 34
            cap_y = (self.height() - cap_h) / 2 - 6

            for i, (d, cw) in enumerate(zip(displayed, cap_widths)):
                cap_rect = QRectF(start_x, cap_y, cw, cap_h)
                cap_path = QPainterPath()
                cap_path.addRoundedRect(cap_rect, 8, 8)
                p.fillPath(cap_path, QColor(theme.BG_ELEVATED))
                p.setPen(QPen(QColor(theme.BORDER_DEFAULT), 1))
                p.drawPath(cap_path)
                p.setFont(theme.font_heading(13))
                p.setPen(QColor(theme.TEXT_PRIMARY))
                p.drawText(cap_rect, Qt.AlignmentFlag.AlignCenter, d)
                start_x += cw + 8

            # Hint
            p.setFont(theme.font(11))
            p.setPen(QColor(theme.TEXT_MUTED))
            hint_rect = QRectF(0, cap_y + cap_h + 6, self.width(), 20)
            p.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, "✏ Click to change")

        p.end()


_MAC_NATIVE_VK: dict[int, str] = {
    0x37: "cmd_l", 0x36: "cmd_r",
    0x3A: "alt_l", 0x3D: "alt_r",
    0x38: "shift_l", 0x3C: "shift_r",
    0x3B: "ctrl_l", 0x3E: "ctrl_r",
}


def _qt_key_to_name(ev: QKeyEvent) -> str:
    import platform
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


_MODIFIER_NAMES = {
    "cmd", "cmd_l", "cmd_r", "alt", "alt_l", "alt_r",
    "ctrl", "ctrl_l", "ctrl_r", "shift", "shift_l", "shift_r",
}


def _is_modifier(key_name: str) -> bool:
    return key_name.lower().strip() in _MODIFIER_NAMES


_DISPLAY_NAMES: dict[str, str] = {
    "space": "Space", "esc": "Esc", "tab": "Tab",
    "caps_lock": "Caps Lock", "backspace": "⌫", "delete": "⌦",
    "home": "Home", "end": "End",
    "page_up": "PgUp", "page_down": "PgDn",
    "right": "→", "left": "←", "up": "↑", "down": "↓",
    "cmd": "⌘", "cmd_l": "⌘", "cmd_r": "Right ⌘",
    "alt": "⌥", "alt_l": "⌥", "alt_r": "Right ⌥",
    "ctrl": "⌃", "ctrl_l": "⌃", "ctrl_r": "Right ⌃",
    "shift": "⇧", "shift_l": "⇧", "shift_r": "Right ⇧",
}


def _display_single(key_name: str) -> str:
    low = key_name.lower().strip()
    if low in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[low]
    if low.startswith("f") and low[1:].isdigit():
        return low.upper()
    if len(low) == 1:
        return low.upper()
    return key_name.upper()


def _display_combo(combo_str: str) -> str:
    parts = [p.strip() for p in combo_str.split("+") if p.strip()]
    if not parts:
        return "None"
    if parts[-1] == "…":
        displayed = [_display_single(p) for p in parts[:-1]]
        return " + ".join(displayed) + " + …"
    displayed = [_display_single(p) for p in parts]
    return " + ".join(displayed)


# ── Mode selection card ─────────────────────────────────────────────────

# Removed _ModeCard


# ── SettingsPage ────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    hotkey_changed = Signal(str)
    settings_changed = Signal()
    capture_started = Signal()
    capture_ended = Signal()
    translation_target_changed = Signal(str)   # NEW

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(0)

        self._heading = QLabel(t("settings.title"))
        self._heading.setFont(theme.font_heading(20))
        self._heading.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        root.addWidget(self._heading)
        root.addSpacing(16)

        # ── Segment tab bar (centered) ──
        tab_container = QHBoxLayout()
        tab_container.setContentsMargins(0, 0, 0, 0)
        tab_container.addStretch()

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)
        self._tabs.setStyleSheet(theme.segment_tab_qss())
        for name in (t("settings.tab_hotkey"), t("settings.tab_audio"),
                     t("settings.tab_transcription"),
                     t("settings.tab_translation"), t("settings.tab_general")):
            self._tabs.addTab(name)
        tab_container.addWidget(self._tabs)
        tab_container.addStretch()
        root.addLayout(tab_container)
        root.addSpacing(20)

        self._pages: list[QWidget] = []
        self._stack_area = QVBoxLayout()
        root.addLayout(self._stack_area, stretch=1)

        self._build_hotkey_tab()
        self._build_audio_tab()
        self._build_transcription_tab()
        self._build_translation_tab()
        self._build_general_tab()

        for i, page in enumerate(self._pages):
            page.setVisible(i == 0)
        self._tabs.currentChanged.connect(self._switch_tab)

        i18n_bus.language_changed.connect(self.retranslate)

    def _switch_tab(self, idx: int) -> None:
        for i, page in enumerate(self._pages):
            page.setVisible(i == idx)

    def _add_page(self, widget: QWidget) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll.setWidget(widget)
        self._stack_area.addWidget(scroll)
        self._pages.append(scroll)

    # ── Hotkey tab ──

    def _build_hotkey_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(20)

        card = theme.make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(16)

        header = QLabel("Activation hotkey")
        header.setFont(theme.font(14, bold=False))
        header.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; border: none;")
        cl.addWidget(header)

        self._hotkey_capture = HotkeyCapture(self._settings.hotkey)
        self._hotkey_capture.key_captured.connect(self._on_hotkey_changed)
        self._hotkey_capture.capture_started.connect(self.capture_started.emit)
        self._hotkey_capture.capture_ended.connect(self.capture_ended.emit)
        cl.addWidget(self._hotkey_capture)

        cl.addSpacing(4)
        cl.addWidget(theme.separator())
        cl.addSpacing(4)

        mode_row, _ = theme.setting_row(
            "Activation Mode",
            "Choose between toggle (click to start/stop) or hold-to-record."
        )
        
        self._mode_combo = QComboBox()
        self._mode_combo.setStyleSheet(theme.COMBO_QSS)
        self._mode_combo.addItem("Toggle (Click)")
        self._mode_combo.addItem("Hold to Record")
        self._mode_combo.setFixedWidth(160)
        
        idx = 1 if self._settings.press_mode == "hold" else 0
        self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.currentIndexChanged.connect(
            lambda i: self._set_mode("hold" if i == 1 else "toggle")
        )
        mode_row.addWidget(self._mode_combo)
        cl.addLayout(mode_row)

        ly.addWidget(card)

        ly.addStretch()
        self._add_page(page)

    def _on_hotkey_changed(self, key_name: str) -> None:
        self._settings.set("hotkey", key_name)
        self.hotkey_changed.emit(key_name)

    def _set_mode(self, mode: str) -> None:
        self._settings.set("press_mode", mode)
        self.settings_changed.emit()

    # ── Audio tab ──

    def _build_audio_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        # --- Input device card ---
        card1 = theme.make_card()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(20, 18, 20, 18)
        c1.setSpacing(12)

        sec1 = QLabel("Input Device")
        sec1.setFont(theme.font(14, bold=True))
        sec1.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c1.addWidget(sec1)

        c1.addWidget(theme.separator())

        mic_row, _ = theme.setting_row(
            "Microphone",
            "Select recording device. Auto follows macOS system default.",
        )
        self._mic_combo = QComboBox()
        self._mic_combo.setFixedWidth(220)
        self._mic_combo.setStyleSheet(theme.COMBO_QSS)
        self._mic_combo.addItem("Auto (System Default)")
        self._refresh_mic_list()
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        mic_row.addWidget(self._mic_combo)
        c1.addLayout(mic_row)
        ly.addWidget(card1)

        # --- Recording options card ---
        card2 = theme.make_card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(20, 18, 20, 18)
        c2.setSpacing(12)

        sec2 = QLabel("Recording")
        sec2.setFont(theme.font(14, bold=True))
        sec2.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c2.addWidget(sec2)

        c2.addWidget(theme.separator())

        mute_row, _ = theme.setting_row(
            "Mute Speakers During Recording",
            "Automatically mute system speakers to avoid feedback from playback",
        )
        self._mute_toggle = theme.ToggleSwitch(self._settings.get("mute_speakers"))
        self._mute_toggle.toggled_signal.connect(lambda v: self._settings.set("mute_speakers", v))
        mute_row.addWidget(self._mute_toggle)
        c2.addLayout(mute_row)
        ly.addWidget(card2)

        ly.addStretch()
        self._add_page(page)

    def _refresh_mic_list(self) -> None:
        """Re-scan audio devices and rebuild the mic dropdown."""
        self._mic_combo.blockSignals(True)
        while self._mic_combo.count() > 1:
            self._mic_combo.removeItem(1)
        try:
            sd._terminate()
            sd._initialize()
            for d in sd.query_devices():
                if d["max_input_channels"] > 0:
                    self._mic_combo.addItem(d["name"])
        except Exception:
            pass
        current = self._settings.microphone
        if current != "auto":
            idx = self._mic_combo.findText(current)
            if idx >= 0:
                self._mic_combo.setCurrentIndex(idx)
        self._mic_combo.blockSignals(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_mic_list()
        self._refresh_review_warning()

    def _on_mic_changed(self, idx: int) -> None:
        self._settings.set("microphone", "auto" if idx == 0 else self._mic_combo.currentText())

    # ── Transcription tab ──

    def _build_transcription_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        # -- Language card --
        card1 = theme.make_card()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(20, 18, 20, 18)
        c1.setSpacing(12)

        sec1 = QLabel("Language")
        sec1.setFont(theme.font(14, bold=True))
        sec1.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c1.addWidget(sec1)

        c1.addWidget(theme.separator())

        lang_row, _ = theme.setting_row(
            "Recognition Language",
            "Force a specific language or let the model auto-detect",
        )
        self._lang_combo = QComboBox()
        self._lang_combo.setFixedWidth(180)
        self._lang_combo.setStyleSheet(theme.COMBO_QSS)
        langs = [
            ("Auto Detect", "auto"), ("English", "en"), ("Chinese", "zh"),
            ("Japanese", "ja"), ("Korean", "ko"), ("Spanish", "es"),
            ("French", "fr"), ("German", "de"), ("Arabic", "ar"),
            ("Hindi", "hi"), ("Italian", "it"), ("Portuguese", "pt"),
            ("Russian", "ru"), ("Dutch", "nl"), ("Turkish", "tr"),
        ]
        for display, code in langs:
            self._lang_combo.addItem(display, code)
        cur = self._settings.transcription_language
        for i, (_, code) in enumerate(langs):
            if code == cur:
                self._lang_combo.setCurrentIndex(i)
                break
        self._lang_combo.currentIndexChanged.connect(self._on_lang_changed)
        lang_row.addWidget(self._lang_combo)
        c1.addLayout(lang_row)
        ly.addWidget(card1)

        # -- Output card --
        card2 = theme.make_card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(20, 18, 20, 18)
        c2.setSpacing(12)

        sec2 = QLabel("Output")
        sec2.setFont(theme.font(14, bold=True))
        sec2.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c2.addWidget(sec2)

        c2.addWidget(theme.separator())

        clip_row, _ = theme.setting_row(
            "Save to Clipboard",
            "Copy transcribed text to clipboard automatically",
        )
        ct = theme.ToggleSwitch(self._settings.get("save_to_clipboard"))
        ct.toggled_signal.connect(lambda v: self._settings.set("save_to_clipboard", v))
        clip_row.addWidget(ct)
        c2.addLayout(clip_row)
        ly.addWidget(card2)

        ly.addStretch()
        self._add_page(page)

    # ── Translation tab ──

    def _build_translation_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        card = theme.make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(12)

        self._translation_section_label = QLabel(t("settings.translation.title"))
        self._translation_section_label.setFont(theme.font(14, bold=True))
        self._translation_section_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; border: none;"
        )
        cl.addWidget(self._translation_section_label)

        self._translation_desc_label = QLabel(t("settings.translation.desc"))
        self._translation_desc_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px; border: none;"
        )
        self._translation_desc_label.setWordWrap(True)
        cl.addWidget(self._translation_desc_label)

        cl.addWidget(theme.separator())

        # ── Mode row ──
        mode_row, mode_label = theme.setting_row(
            t("settings.translation.mode"),
            "",
        )
        self._translation_mode_label = mode_label

        self._translation_mode_combo = QComboBox()
        self._translation_mode_combo.setFixedWidth(220)
        self._translation_mode_combo.setStyleSheet(theme.COMBO_QSS)
        self._translation_mode_combo.addItem(
            t("settings.translation.mode_direct"), "direct"
        )
        self._translation_mode_combo.addItem(
            t("settings.translation.mode_review"), "review"
        )

        # Restore current selection
        current_mode = self._settings.translation_mode
        for i in range(self._translation_mode_combo.count()):
            if self._translation_mode_combo.itemData(i) == current_mode:
                self._translation_mode_combo.setCurrentIndex(i)
                break

        self._translation_mode_combo.currentIndexChanged.connect(
            self._on_translation_mode_changed
        )
        mode_row.addWidget(self._translation_mode_combo)
        cl.addLayout(mode_row)

        # Mode caption (small, multi-line)
        self._translation_mode_caption = QLabel(t("settings.translation.mode_desc"))
        self._translation_mode_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; border: none;"
            f" padding-left: 0px;"
        )
        self._translation_mode_caption.setWordWrap(True)
        cl.addWidget(self._translation_mode_caption)

        # Warning shown when Review mode is selected but no ASR model is
        # active — Review needs Qwen3-ASR / SenseVoice to produce the
        # original-language text first.
        self._translation_review_warning = QLabel(
            t("settings.translation.review_needs_asr")
        )
        self._translation_review_warning.setStyleSheet(
            f"color: {theme.ACCENT_ORANGE}; font-size: 11px; border: none;"
            " padding: 4px 0;"
        )
        self._translation_review_warning.setWordWrap(True)
        self._translation_review_warning.hide()
        cl.addWidget(self._translation_review_warning)

        # ── Target row ──
        target_row, target_label = theme.setting_row(
            t("settings.translation.target"),
            "",
        )
        self._translation_target_label = target_label

        self._translation_combo = QComboBox()
        self._translation_combo.setFixedWidth(220)
        self._translation_combo.setStyleSheet(theme.COMBO_QSS)
        for code, display in TRANSLATION_TARGETS:
            label = t("settings.translation.off") if code == "off" else display
            self._translation_combo.addItem(label, code)

        # Restore current selection
        current = self._settings.translation_target
        for i in range(self._translation_combo.count()):
            if self._translation_combo.itemData(i) == current:
                self._translation_combo.setCurrentIndex(i)
                break

        self._translation_combo.currentIndexChanged.connect(
            self._on_translation_target_changed
        )
        target_row.addWidget(self._translation_combo)
        cl.addLayout(target_row)

        ly.addWidget(card)
        ly.addStretch()
        self._add_page(page)
        # Initial visibility check (mode may already be Review with no ASR)
        self._refresh_review_warning()

    def _on_translation_target_changed(self, idx: int) -> None:
        code = self._translation_combo.itemData(idx)
        if not code:
            return
        self._settings.set("translation_target", code)
        self.translation_target_changed.emit(code)

    def _on_translation_mode_changed(self, idx: int) -> None:
        code = self._translation_mode_combo.itemData(idx)
        if not code:
            return
        self._settings.set("translation_mode", code)
        self._refresh_review_warning()

    def _refresh_review_warning(self) -> None:
        """Show the 'Review needs an ASR model' warning when applicable.
        Called on init, on mode change, and on every showEvent (so re-opening
        Settings after activating an ASR model clears the warning)."""
        if not hasattr(self, "_translation_review_warning"):
            return
        mode = self._settings.translation_mode
        active_id = self._settings.active_model_id
        is_asr_active = bool(active_id) and not active_id.startswith("seamless")
        needs_warn = (mode == "review") and not is_asr_active
        self._translation_review_warning.setVisible(needs_warn)

    # ── General tab ──

    def _build_general_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        # -- Appearance card --
        card1 = theme.make_card()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(20, 18, 20, 18)
        c1.setSpacing(12)

        sec1 = QLabel("Appearance")
        sec1.setFont(theme.font(14, bold=True))
        sec1.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c1.addWidget(sec1)

        c1.addWidget(theme.separator())

        # Language selector — applies instantly
        lang_row, lang_label = theme.setting_row(
            t("settings.language"), t("settings.language_desc")
        )
        self._ui_lang_label = lang_label
        self._ui_lang_combo = QComboBox()
        self._ui_lang_combo.setFixedWidth(160)
        self._ui_lang_combo.setStyleSheet(theme.COMBO_QSS)
        self._ui_lang_combo.addItem("English", "en")
        self._ui_lang_combo.addItem("中文", "zh")
        cur_lang = self._settings.get("language") or "en"
        self._ui_lang_combo.setCurrentIndex(1 if cur_lang == "zh" else 0)
        self._ui_lang_combo.currentIndexChanged.connect(self._on_ui_lang_changed)
        lang_row.addWidget(self._ui_lang_combo)
        c1.addLayout(lang_row)

        ly.addWidget(card1)

        # -- Startup card --
        card2 = theme.make_card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(20, 18, 20, 18)
        c2.setSpacing(12)

        sec2 = QLabel("Startup")
        sec2.setFont(theme.font(14, bold=True))
        sec2.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c2.addWidget(sec2)

        c2.addWidget(theme.separator())

        r1, _ = theme.setting_row(
            "Launch at Login",
            "ThunderTalk will start automatically when you log in",
        )
        t1 = theme.ToggleSwitch(self._settings.get("launch_at_startup"))
        t1.toggled_signal.connect(lambda v: self._settings.set("launch_at_startup", v))
        r1.addWidget(t1)
        c2.addLayout(r1)

        c2.addWidget(theme.separator())

        r2, _ = theme.setting_row(
            "Start Minimized",
            "Open to system tray without showing the main window",
        )
        t2 = theme.ToggleSwitch(self._settings.get("silent_launch"))
        t2.toggled_signal.connect(lambda v: self._settings.set("silent_launch", v))
        r2.addWidget(t2)
        c2.addLayout(r2)
        ly.addWidget(card2)

        # -- Logs card --
        card3 = theme.make_card()
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(20, 18, 20, 18)
        c3.setSpacing(12)

        sec3 = QLabel("Logs")
        sec3.setFont(theme.font(14, bold=True))
        sec3.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c3.addWidget(sec3)

        c3.addWidget(theme.separator())

        log_row, _ = theme.setting_row(
            "Enable Logging",
            "Save debug logs to disk for troubleshooting",
        )
        lt = theme.ToggleSwitch(self._settings.get("log_enabled"))
        lt.toggled_signal.connect(lambda v: self._settings.set("log_enabled", v))
        log_row.addWidget(lt)
        c3.addLayout(log_row)

        c3.addWidget(theme.separator())

        dir_row, _ = theme.setting_row("Data Directory")
        open_btn = theme.pill_button("Open Folder", width=110, height=32)
        open_btn.clicked.connect(self._open_log_dir)
        dir_row.addWidget(open_btn)
        c3.addLayout(dir_row)
        ly.addWidget(card3)

        ly.addStretch()
        self._add_page(page)

    def _on_lang_changed(self, idx: int) -> None:
        code = self._lang_combo.itemData(idx)
        if code:
            self._settings.set("transcription_language", code)
            self.settings_changed.emit()

    def _on_ui_lang_changed(self, idx: int) -> None:
        code = self._ui_lang_combo.itemData(idx)
        if code:
            self._settings.set("language", code)
            set_language(code)

    def retranslate(self) -> None:
        self._heading.setText(t("settings.title"))
        tabs = (t("settings.tab_hotkey"), t("settings.tab_audio"),
                t("settings.tab_transcription"),
                t("settings.tab_translation"), t("settings.tab_general"))
        for i, name in enumerate(tabs):
            self._tabs.setTabText(i, name)
        self._ui_lang_label.setText(t("settings.language"))
        self._translation_section_label.setText(t("settings.translation.title"))
        self._translation_desc_label.setText(t("settings.translation.desc"))
        self._translation_target_label.setText(t("settings.translation.target"))
        # Re-translate the "Off" entry in the combo (other entries are
        # native-language labels and shouldn't change with UI language).
        if self._translation_combo.count() > 0:
            self._translation_combo.setItemText(0, t("settings.translation.off"))
        # Mode combo
        self._translation_mode_label.setText(t("settings.translation.mode"))
        self._translation_mode_caption.setText(t("settings.translation.mode_desc"))
        if self._translation_mode_combo.count() >= 2:
            self._translation_mode_combo.setItemText(0, t("settings.translation.mode_direct"))
            self._translation_mode_combo.setItemText(1, t("settings.translation.mode_review"))

    def _open_log_dir(self) -> None:
        log_dir = Path.home() / ".thundertalk"
        log_dir.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            subprocess.run(["open", str(log_dir)], check=False)
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", str(log_dir)], check=False)
        elif platform.system() == "Windows":
            subprocess.run(["explorer", str(log_dir)], check=False)

