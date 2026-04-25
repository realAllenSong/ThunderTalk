"""Lightweight i18n with live switching.

Usage:
    from thundertalk.core.i18n import t, bus, set_language
    label.setText(t("home.speaking_time"))
    bus.language_changed.connect(self.retranslate)
    set_language("zh")  # switches instantly and notifies listeners
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal

_SETTINGS_PATH = Path.home() / ".thundertalk" / "settings.json"


def _read_language() -> str:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            lang = json.load(f).get("language", "en")
            return lang if lang in ("en", "zh") else "en"
    except Exception:
        return "en"


LANG = _read_language()


class _I18nBus(QObject):
    language_changed = Signal()


bus = _I18nBus()


def set_language(code: str) -> None:
    """Switch UI language at runtime and notify listeners."""
    global LANG
    if code not in ("en", "zh") or code == LANG:
        return
    LANG = code
    bus.language_changed.emit()


_STRINGS: dict[str, dict[str, str]] = {
    # ── Sidebar ─────────────────────────────────────────────────────
    "nav.home": {"en": "Home", "zh": "主页"},
    "nav.models": {"en": "Models", "zh": "模型"},
    "nav.hotwords": {"en": "Hotwords", "zh": "热词"},
    "nav.settings": {"en": "Settings", "zh": "设置"},
    "nav.about": {"en": "About", "zh": "关于"},

    # ── Home ────────────────────────────────────────────────────────
    "home.speaking_time": {"en": "Speaking Time", "zh": "发声时长"},
    "home.characters": {"en": "Characters", "zh": "字符数"},
    "home.sessions": {"en": "Sessions", "zh": "次数"},
    "home.recent": {"en": "Recent", "zh": "最近"},
    "home.clear": {"en": "Clear", "zh": "清空"},
    "home.ready": {"en": "Ready to go", "zh": "准备就绪"},
    "home.ready_sub": {"en": "Press your hotkey to start recording",
                       "zh": "按下快捷键开始录音"},
    "home.today": {"en": "Today", "zh": "今天"},
    "home.yesterday": {"en": "Yesterday", "zh": "昨天"},
    "home.copy": {"en": "Copy", "zh": "复制"},
    "home.copied": {"en": "Copied!", "zh": "已复制！"},

    # ── Models ──────────────────────────────────────────────────────
    "models.title": {"en": "Models", "zh": "模型"},
    "models.hardware": {"en": "Hardware", "zh": "硬件"},
    "models.active": {"en": "Active", "zh": "当前"},
    "models.use": {"en": "Use", "zh": "使用"},
    "models.download": {"en": "Download", "zh": "下载"},
    "models.delete": {"en": "Delete", "zh": "删除"},
    "models.downloading": {"en": "Downloading…", "zh": "下载中…"},

    # ── Hotwords ────────────────────────────────────────────────────
    "hotwords.title": {"en": "Hotwords", "zh": "热词"},
    "hotwords.desc": {
        "en": "Add terms the model should favor when transcribing.",
        "zh": "添加转录时需要优先识别的词汇。",
    },
    "hotwords.add": {"en": "Add", "zh": "添加"},
    "hotwords.placeholder": {"en": "Type a word and press Add",
                              "zh": "输入词汇后点击添加"},

    # ── Settings ────────────────────────────────────────────────────
    "settings.title": {"en": "Settings", "zh": "设置"},
    "settings.tab_general": {"en": "General", "zh": "通用"},
    "settings.tab_transcription": {"en": "Transcription", "zh": "转录"},
    "settings.tab_hotkey": {"en": "Hotkey", "zh": "快捷键"},
    "settings.tab_audio": {"en": "Audio", "zh": "音频"},
    "settings.tab_advanced": {"en": "Advanced", "zh": "高级"},
    "settings.tab_translation": {"en": "Translation", "zh": "翻译"},
    "settings.translation.title": {"en": "Speech Translation", "zh": "语音翻译"},
    "settings.translation.desc": {
        "en": "Speak in any supported language; output text is translated to your target language. Requires SeamlessM4T v2 model.",
        "zh": "用任何支持的语言说话，输出文本会翻译为目标语言。需要下载 SeamlessM4T v2 模型。",
    },
    "settings.translation.target": {"en": "Translate to", "zh": "翻译为"},
    "settings.translation.off": {"en": "Off (pure transcription)", "zh": "关闭（纯转录）"},
    "settings.translation.mode": {"en": "Mode", "zh": "模式"},
    "settings.translation.mode_direct": {
        "en": "Direct (replace text)",
        "zh": "直接（替换文本）",
    },
    "settings.translation.mode_review": {
        "en": "Review (popup confirm)",
        "zh": "确认（弹窗预览）",
    },
    "settings.translation.mode_desc": {
        "en": "Direct: replaces text immediately. Review: pastes original first, shows a popup with the translation; click Replace to swap.",
        "zh": "直接：直接替换文字。确认：先粘贴原文，弹窗显示译文；点击「替换」可切换到译文。",
    },

    # ── Translation Review popup ────────────────────────────────────
    "review.title": {"en": "Translation: {lang}", "zh": "翻译为：{lang}"},
    "review.original": {"en": "Original", "zh": "原文"},
    "review.translated": {"en": "Translated", "zh": "译文"},
    "review.keep": {"en": "Keep", "zh": "保留"},
    "review.replace": {"en": "Replace", "zh": "替换"},
    "review.translating": {"en": "Translating", "zh": "翻译中"},
    "settings.translation.review_needs_asr": {
        "en": "Review mode needs an active ASR model. Activate Qwen3-ASR or SenseVoice in the Models page first.",
        "zh": "Review 模式需要先在 Models 页面激活一个 ASR 模型（Qwen3-ASR 或 SenseVoice）。",
    },

    "settings.language": {"en": "Language", "zh": "语言"},
    "settings.language_desc": {
        "en": "Interface language (restart required).",
        "zh": "界面语言（需重启生效）。",
    },
    "settings.theme": {"en": "Theme", "zh": "主题"},
    "settings.theme_desc": {
        "en": "Light or dark appearance (restart required).",
        "zh": "浅色或深色外观（需重启生效）。",
    },
    "settings.theme_dark": {"en": "Dark", "zh": "深色"},
    "settings.theme_light": {"en": "Light", "zh": "浅色"},

    "settings.launch_at_startup": {"en": "Launch at startup", "zh": "开机自启"},
    "settings.launch_at_startup_desc": {
        "en": "Start ThunderTalk when you log in.",
        "zh": "登录时自动启动 ThunderTalk。",
    },
    "settings.silent_launch": {"en": "Silent launch", "zh": "静默启动"},
    "settings.silent_launch_desc": {
        "en": "Start without showing the main window.",
        "zh": "启动时不显示主窗口。",
    },
    "settings.show_in_dock": {"en": "Show in Dock", "zh": "在程序坞显示"},
    "settings.show_in_dock_desc": {
        "en": "Display icon in the macOS Dock (restart required).",
        "zh": "在 macOS 程序坞显示图标（需重启生效）。",
    },

    "settings.hotkey": {"en": "Hotkey", "zh": "快捷键"},
    "settings.hotkey_desc": {
        "en": "Global key to start and stop recording.",
        "zh": "用于全局启停录音的按键。",
    },
    "settings.press_mode": {"en": "Press mode", "zh": "按键模式"},
    "settings.press_mode_desc": {
        "en": "Toggle = press to start, press to stop. Hold = press and hold while speaking.",
        "zh": "切换 = 按一次开始，再按停止。长按 = 按住说话。",
    },
    "settings.press_mode_toggle": {"en": "Toggle", "zh": "切换"},
    "settings.press_mode_hold": {"en": "Hold", "zh": "长按"},

    "settings.microphone": {"en": "Microphone", "zh": "麦克风"},
    "settings.mute_speakers": {"en": "Mute speakers while recording", "zh": "录音时静音扬声器"},
    "settings.mute_speakers_desc": {
        "en": "Prevents the mic from picking up playback.",
        "zh": "防止麦克风拾取扬声器声音。",
    },

    "settings.transcription_language": {"en": "Transcription language", "zh": "转录语言"},
    "settings.transcription_language_desc": {
        "en": "Language the model should expect.",
        "zh": "模型要识别的语言。",
    },
    "settings.auto": {"en": "Auto", "zh": "自动"},
    "settings.english": {"en": "English", "zh": "英文"},
    "settings.chinese": {"en": "Chinese", "zh": "中文"},

    "settings.log_enabled": {"en": "Log to file", "zh": "写入日志文件"},
    "settings.log_enabled_desc": {
        "en": "Save logs to ~/.thundertalk/thundertalk.log for debugging.",
        "zh": "将日志保存到 ~/.thundertalk/thundertalk.log 用于调试。",
    },

    # ── Tray ────────────────────────────────────────────────────────
    "tray.open": {"en": "Open Settings", "zh": "打开设置"},
    "tray.quit": {"en": "Quit", "zh": "退出"},

    # ── Overlay ─────────────────────────────────────────────────────
    "overlay.listening": {"en": "Listening…", "zh": "正在聆听…"},
    "overlay.transcribing": {"en": "Transcribing…", "zh": "正在转写…"},

    # ── Capture ─────────────────────────────────────────────────────
    "capture.press_key": {"en": "Press any key…", "zh": "按任意键…"},
    "capture.change": {"en": "Change", "zh": "更改"},
    "capture.cancel": {"en": "Cancel", "zh": "取消"},
}


def t(key: str) -> str:
    entry = _STRINGS.get(key)
    if not entry:
        return key
    return entry.get(LANG) or entry.get("en") or key
