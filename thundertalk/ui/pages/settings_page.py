"""Settings page — segment-control tabs: Hotkey, Microphone, System, Hotwords.

Layout and cards match 闪电说 style:
- Rounded segment pill tab bar at top center
- Card sections with bold in-card titles
- Side-by-side toggle/press mode cards
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
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

import sounddevice as sd

from thundertalk.ui import theme

if TYPE_CHECKING:
    from thundertalk.core.settings import Settings


# ── Hotkey Capture Widget ───────────────────────────────────────────────

class HotkeyCapture(QWidget):
    """Click the capsule, press a key combo to set hotkey.

    Displays current hotkey as key-cap badges.
    While capturing: shows held modifiers live.
    """

    key_captured = Signal(str)

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
    hotwords_changed = Signal(list)
    settings_changed = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 20)
        root.setSpacing(0)

        # ── Segment tab bar (centered) ──
        tab_container = QHBoxLayout()
        tab_container.setContentsMargins(0, 0, 0, 0)
        tab_container.addStretch()

        self._tabs = QTabBar()
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)
        self._tabs.setStyleSheet(theme.segment_tab_qss())
        for name in ("Hotkey", "Microphone", "System", "Hotwords"):
            self._tabs.addTab(name)
        tab_container.addWidget(self._tabs)
        tab_container.addStretch()
        root.addLayout(tab_container)
        root.addSpacing(24)

        self._pages: list[QWidget] = []
        self._stack_area = QVBoxLayout()
        root.addLayout(self._stack_area, stretch=1)

        self._build_hotkey_tab()
        self._build_mic_tab()
        self._build_system_tab()
        self._build_hotwords_tab()

        for i, page in enumerate(self._pages):
            page.setVisible(i == 0)
        self._tabs.currentChanged.connect(self._switch_tab)

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

    # ── Microphone tab ──

    def _build_mic_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(20)

        hint = QLabel("Select recording device and test your microphone")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        ly.addWidget(hint)

        ly.addWidget(theme.section_heading("Microphone"))

        self._mic_combo = QComboBox()
        self._mic_combo.setStyleSheet(theme.COMBO_QSS)
        self._mic_combo.addItem("Auto (System Default)")
        try:
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
        self._mic_combo.currentIndexChanged.connect(self._on_mic_changed)
        ly.addWidget(self._mic_combo)

        # Options card
        card = theme.make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(16)

        row, _ = theme.setting_row(
            "Mute Speakers During Recording",
            "Automatically mute system speakers to avoid feedback",
        )
        self._mute_toggle = theme.ToggleSwitch(self._settings.get("mute_speakers"))
        self._mute_toggle.toggled_signal.connect(lambda v: self._settings.set("mute_speakers", v))
        row.addWidget(self._mute_toggle)
        cl.addLayout(row)

        cl.addWidget(theme.separator())

        dir_row, _ = theme.setting_row("Recording Directory")
        open_btn = theme.pill_button("Open Folder", width=110, height=32)
        open_btn.clicked.connect(self._open_log_dir)
        dir_row.addWidget(open_btn)
        cl.addLayout(dir_row)

        ly.addWidget(card)
        ly.addStretch()
        self._add_page(page)

    def _on_mic_changed(self, idx: int) -> None:
        self._settings.set("microphone", "auto" if idx == 0 else self._mic_combo.currentText())

    # ── System tab ──

    def _build_system_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        hint = QLabel("Configure startup behavior and log settings")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        ly.addWidget(hint)

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

        row, _ = theme.setting_row("Theme", "Use light, dark, or match system setting")
        lbl = QLabel("Dark")
        lbl.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px; border: none;"
            f" background: {theme.BG_ELEVATED}; border: 1px solid {theme.BORDER_SUBTLE};"
            " border-radius: 8px; padding: 4px 14px;"
        )
        row.addWidget(lbl)
        c1.addLayout(row)
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

        r1, _ = theme.setting_row("Launch at Login",
            "ThunderTalk will start when you log in")
        t1 = theme.ToggleSwitch(self._settings.get("launch_at_startup"))
        t1.toggled_signal.connect(lambda v: self._settings.set("launch_at_startup", v))
        r1.addWidget(t1)
        c2.addLayout(r1)

        c2.addWidget(theme.separator())

        r2, _ = theme.setting_row("Start Minimized",
            "Open to system tray without showing window")
        t2 = theme.ToggleSwitch(self._settings.get("silent_launch"))
        t2.toggled_signal.connect(lambda v: self._settings.set("silent_launch", v))
        r2.addWidget(t2)
        c2.addLayout(r2)

        if platform.system() == "Darwin":
            c2.addWidget(theme.separator())
            r3, _ = theme.setting_row("Show in Dock",
                "Hide app icon from Dock; access via tray icon only")
            t3 = theme.ToggleSwitch(self._settings.get("show_in_dock"))
            t3.toggled_signal.connect(lambda v: self._settings.set("show_in_dock", v))
            r3.addWidget(t3)
            c2.addLayout(r3)

        ly.addWidget(card2)

        # -- Transcription card --
        card3 = theme.make_card()
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(20, 18, 20, 18)
        c3.setSpacing(12)

        sec3 = QLabel("Transcription")
        sec3.setFont(theme.font(14, bold=True))
        sec3.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c3.addWidget(sec3)

        c3.addWidget(theme.separator())

        lang_row, _ = theme.setting_row("Language", "Language for speech recognition")
        self._lang_combo = QComboBox()
        self._lang_combo.setFixedWidth(180)
        self._lang_combo.setStyleSheet(theme.COMBO_QSS)
        langs = [
            ("Auto Detect", "auto"), ("English", "en"), ("Chinese", "zh"),
            ("Japanese", "ja"), ("Korean", "ko"), ("Spanish", "es"),
            ("French", "fr"), ("German", "de"),
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
        c3.addLayout(lang_row)

        c3.addWidget(theme.separator())

        clip_row, _ = theme.setting_row("Save to Clipboard",
            "Copy transcribed text to clipboard automatically")
        ct = theme.ToggleSwitch(self._settings.get("save_to_clipboard"))
        ct.toggled_signal.connect(lambda v: self._settings.set("save_to_clipboard", v))
        clip_row.addWidget(ct)
        c3.addLayout(clip_row)
        ly.addWidget(card3)

        # -- Logs card --
        card4 = theme.make_card()
        c4 = QVBoxLayout(card4)
        c4.setContentsMargins(20, 18, 20, 18)
        c4.setSpacing(12)

        sec4 = QLabel("Logs")
        sec4.setFont(theme.font(14, bold=True))
        sec4.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        c4.addWidget(sec4)

        c4.addWidget(theme.separator())

        log_row, _ = theme.setting_row("Enable Logging",
            "Save debug logs to disk")
        lt = theme.ToggleSwitch(self._settings.get("log_enabled"))
        lt.toggled_signal.connect(lambda v: self._settings.set("log_enabled", v))
        log_row.addWidget(lt)
        c4.addLayout(log_row)

        c4.addWidget(theme.separator())

        dir_row, _ = theme.setting_row("Log Directory")
        open_btn = theme.pill_button("Open Folder", width=110, height=32)
        open_btn.clicked.connect(self._open_log_dir)
        dir_row.addWidget(open_btn)
        c4.addLayout(dir_row)
        ly.addWidget(card4)

        ly.addStretch()
        self._add_page(page)

    def _on_lang_changed(self, idx: int) -> None:
        code = self._lang_combo.itemData(idx)
        if code:
            self._settings.set("transcription_language", code)
            self.settings_changed.emit()

    def _open_log_dir(self) -> None:
        log_dir = Path.home() / ".thundertalk"
        log_dir.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Darwin":
            subprocess.run(["open", str(log_dir)], check=False)
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", str(log_dir)], check=False)
        elif platform.system() == "Windows":
            subprocess.run(["explorer", str(log_dir)], check=False)

    # ── Hotwords tab ──

    def _build_hotwords_tab(self) -> None:
        page = QWidget()
        ly = QVBoxLayout(page)
        ly.setContentsMargins(0, 0, 0, 0)
        ly.setSpacing(16)

        hint = QLabel(
            "Add domain-specific terms to improve recognition accuracy."
        )
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        hint.setWordWrap(True)
        ly.addWidget(hint)

        card = theme.make_card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 18, 20, 18)
        cl.setSpacing(12)

        sec = QLabel("Custom Vocabulary")
        sec.setFont(theme.font(14, bold=True))
        sec.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; border: none;")
        cl.addWidget(sec)

        cl.addWidget(theme.separator())

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._hw_input = QLineEdit()
        self._hw_input.setPlaceholderText("Type a word or phrase…")
        self._hw_input.setStyleSheet(theme.INPUT_QSS)
        add_row.addWidget(self._hw_input)

        add_btn = theme.accent_button("Add", height=36)
        add_btn.setFixedWidth(72)
        add_btn.clicked.connect(self._add_hotword)
        self._hw_input.returnPressed.connect(self._add_hotword)
        add_row.addWidget(add_btn)
        cl.addLayout(add_row)

        self._hw_list = QListWidget()
        self._hw_list.setStyleSheet(
            f"QListWidget {{ background: {theme.BG_INPUT}; color: {theme.TEXT_PRIMARY};"
            f" border: 1px solid {theme.BORDER_SUBTLE}; border-radius: 10px; font-size: 13px; }}"
            f"QListWidget::item {{ padding: 8px 14px; border-bottom: 1px solid {theme.BORDER_SUBTLE}; }}"
            f"QListWidget::item:selected {{ background: {theme.ACCENT_BLUE_A10}; }}"
        )
        self._hw_list.setMinimumHeight(180)
        for word in self._settings.hotwords:
            self._hw_list.addItem(word)
        cl.addWidget(self._hw_list)

        rm_btn = theme.pill_button("Remove Selected", height=32)
        rm_btn.clicked.connect(self._remove_hotword)
        cl.addWidget(rm_btn)

        ly.addWidget(card)
        ly.addStretch()
        self._add_page(page)

    def _add_hotword(self) -> None:
        word = self._hw_input.text().strip()
        if not word:
            return
        words = self._settings.hotwords
        if word not in words:
            words.append(word)
            self._settings.set("hotwords", words)
            self._hw_list.addItem(word)
            self.hotwords_changed.emit(words)
        self._hw_input.clear()

    def add_hotword_external(self, word: str) -> None:
        word = word.strip()
        if not word:
            return
        words = self._settings.hotwords
        if word not in words:
            words.append(word)
            self._settings.set("hotwords", words)
            self._hw_list.addItem(word)
            self.hotwords_changed.emit(words)

    def _remove_hotword(self) -> None:
        items = self._hw_list.selectedItems()
        if not items:
            return
        words = self._settings.hotwords
        for item in items:
            w = item.text()
            if w in words:
                words.remove(w)
            self._hw_list.takeItem(self._hw_list.row(item))
        self._settings.set("hotwords", words)
        self.hotwords_changed.emit(words)
