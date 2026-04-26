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
    "home.copy_translation": {"en": "Copy ↗", "zh": "复制译文"},
    "home.copied": {"en": "Copied!", "zh": "已复制！"},

    # ── Models ──────────────────────────────────────────────────────
    "models.title": {"en": "Models", "zh": "模型"},
    "models.hardware": {"en": "Hardware", "zh": "硬件"},
    "models.detecting_hw": {"en": "Detecting hardware…", "zh": "正在检测硬件…"},
    "models.active": {"en": "Active", "zh": "当前"},
    "models.use": {"en": "Use", "zh": "使用"},
    "models.download": {"en": "Download", "zh": "下载"},
    "models.delete": {"en": "Delete", "zh": "删除"},
    "models.downloading": {"en": "Downloading…", "zh": "下载中…"},
    # Translation card (top of Models page)
    "models.translation": {"en": "Translation", "zh": "翻译"},
    "models.translation_subtitle": {
        "en": "Speech translation via SeamlessM4T v2",
        "zh": "通过 SeamlessM4T v2 进行语音翻译",
    },
    "models.mode_off": {"en": "Off", "zh": "关闭"},
    "models.mode_direct": {"en": "Direct", "zh": "直译"},
    "models.mode_review": {"en": "Review", "zh": "审阅"},
    # Translator status row
    "models.translator.missing": {
        "en": "Translation model not downloaded.",
        "zh": "翻译模型未下载。",
    },
    "models.translator.loading": {
        "en": "Loading translation model…",
        "zh": "正在加载翻译模型…",
    },
    "models.translator.downloading": {
        "en": "Downloading translation model… this can take a while.",
        "zh": "正在下载翻译模型…可能需要一段时间。",
    },
    "models.translator.ready": {
        "en": "Translation model ready.",
        "zh": "翻译模型已就绪。",
    },
    "models.translator.error": {
        "en": "Translation model failed to load.",
        "zh": "翻译模型加载失败。",
    },
    # Variant row buttons / labels
    "models.recommended": {"en": "Recommended", "zh": "推荐"},
    "models.languages": {"en": "languages", "zh": "种语言"},
    "models.hotwords_supported": {"en": "Hotwords", "zh": "支持热词"},
    "models.variants_available": {
        "en": "{n} variants available",
        "zh": "可用变体 {n} 个",
    },
    "models.btn.activate": {"en": "Activate", "zh": "激活"},
    "models.btn.active": {"en": "✓ Active", "zh": "✓ 当前"},
    "models.btn.translator": {"en": "✓ Translator", "zh": "✓ 翻译器"},
    "models.btn.loading": {"en": "Loading…", "zh": "加载中…"},
    "models.btn.download": {"en": "Download", "zh": "下载"},
    "models.btn.coming_soon": {"en": "Coming Soon", "zh": "即将推出"},
    "models.btn.needs_apple_silicon": {
        "en": "Needs Apple Silicon",
        "zh": "需要 Apple Silicon",
    },
    "models.btn.needs_nvidia": {"en": "Needs NVIDIA GPU", "zh": "需要 NVIDIA GPU"},
    "models.btn.direct_uses_seamless": {
        "en": "Direct uses SeamlessM4T",
        "zh": "直译需 SeamlessM4T",
    },
    "models.btn.direct_review_only": {
        "en": "Direct / Review only",
        "zh": "仅直译 / 审阅模式",
    },
    "models.review_needs_asr": {
        "en": "Review mode needs an active ASR model. "
              "Activate Qwen3-ASR or SenseVoice below.",
        "zh": "审阅模式需要先激活一个 ASR 模型，请在下方激活 Qwen3-ASR 或 SenseVoice。",
    },

    # ── Hotwords ────────────────────────────────────────────────────
    "hotwords.title": {"en": "Hotwords", "zh": "热词"},
    "hotwords.desc": {
        "en": "Add terms the model should favor when transcribing.",
        "zh": "添加转录时需要优先识别的词汇。",
    },
    "hotwords.add": {"en": "Add", "zh": "添加"},
    "hotwords.placeholder": {"en": "Type a word and press Add",
                              "zh": "输入词汇后点击添加"},
    "hotwords.add_word": {"en": "Add Word", "zh": "添加词汇"},
    "hotwords.add_hint": {
        "en": "Press Enter or click Add. Words are saved automatically.",
        "zh": "按回车或点击添加，词汇会自动保存。",
    },
    "hotwords.custom_vocab": {"en": "Custom Vocabulary", "zh": "自定义词汇表"},
    "hotwords.empty": {
        "en": "No hotwords added yet. Add words above to get started.",
        "zh": "还没有热词，请在上方添加。",
    },
    "hotwords.count": {"en": "{n} words", "zh": "{n} 个词"},

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
    "review.translating": {"en": "TRANSLATING…", "zh": "翻译中…"},
    "review.translation_label": {"en": "TRANSLATION", "zh": "译文"},
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

    # ── Settings page sections / fields ─────────────────────────────
    "settings.section.activation_hotkey": {
        "en": "Activation hotkey", "zh": "激活快捷键",
    },
    "settings.section.activation_mode": {
        "en": "Activation Mode", "zh": "激活模式",
    },
    "settings.section.activation_mode_desc": {
        "en": "Choose between toggle (click to start/stop) or hold-to-record.",
        "zh": "选择切换模式（按一次启停）或按住录音模式。",
    },
    "settings.section.input_device": {"en": "Input Device", "zh": "输入设备"},
    "settings.section.recording": {"en": "Recording", "zh": "录音"},
    "settings.section.language": {"en": "Language", "zh": "语言"},
    "settings.section.output": {"en": "Output", "zh": "输出"},
    "settings.section.appearance": {"en": "Appearance", "zh": "外观"},
    "settings.section.startup": {"en": "Startup", "zh": "启动"},
    "settings.section.logs": {"en": "Logs", "zh": "日志"},
    "settings.section.performance": {"en": "Performance", "zh": "性能"},
    "settings.memory.label": {"en": "Memory profile", "zh": "内存模式"},
    "settings.memory.desc": {
        "en": "High keeps a 4096-token KV cache and full thread count for the longest possible single utterances; Low caps at 1024 tokens / 4 threads and saves ≈ 3 GB RAM. Takes effect on next app launch.",
        "zh": "高质量保留 4096 个 token 的 KV 缓存与全部推理线程，支持最长的单次录音；省内存档限制为 1024 token / 4 线程，可省 3 GB 左右内存。下次启动应用时生效。",
    },
    "settings.memory.high": {"en": "High (default)", "zh": "高质量（默认）"},
    "settings.memory.low": {"en": "Low (≈ 3 GB less RAM)", "zh": "省内存（少占 ~3 GB）"},
    "settings.memory.restart_hint": {
        "en": "Setting saved. Restart ThunderTalk for the new profile to take effect.",
        "zh": "已保存，下次启动 ThunderTalk 时生效。",
    },
    "settings.mode.toggle_click": {"en": "Toggle (Click)", "zh": "切换（点击）"},
    "settings.mode.hold_record": {"en": "Hold to Record", "zh": "按住录音"},
    "settings.mic.auto": {
        "en": "Auto (System Default)", "zh": "自动（系统默认）",
    },
    "settings.mic.label": {"en": "Microphone", "zh": "麦克风"},
    "settings.mic.desc": {
        "en": "Select recording device. Auto follows macOS system default.",
        "zh": "选择录音设备，自动模式跟随系统默认。",
    },
    "settings.mute.label": {
        "en": "Mute Speakers During Recording", "zh": "录音时静音扬声器",
    },
    "settings.mute.desc": {
        "en": "Automatically mute system speakers to avoid feedback from playback.",
        "zh": "录音时自动静音系统扬声器，防止扬声器声音被麦克风拾取。",
    },
    "settings.recog.label": {
        "en": "Recognition Language", "zh": "识别语言",
    },
    "settings.recog.desc": {
        "en": "Force a specific language or let the model auto-detect.",
        "zh": "指定识别语言，或让模型自动检测。",
    },
    "settings.recog.auto": {"en": "Auto Detect", "zh": "自动检测"},
    "settings.recog.en": {"en": "English", "zh": "英文"},
    "settings.recog.zh": {"en": "Chinese", "zh": "中文"},
    "settings.recog.ja": {"en": "Japanese", "zh": "日文"},
    "settings.recog.ko": {"en": "Korean", "zh": "韩文"},
    "settings.recog.es": {"en": "Spanish", "zh": "西班牙文"},
    "settings.recog.fr": {"en": "French", "zh": "法文"},
    "settings.recog.de": {"en": "German", "zh": "德文"},
    "settings.recog.ar": {"en": "Arabic", "zh": "阿拉伯文"},
    "settings.recog.hi": {"en": "Hindi", "zh": "印地文"},
    "settings.recog.it": {"en": "Italian", "zh": "意大利文"},
    "settings.recog.pt": {"en": "Portuguese", "zh": "葡萄牙文"},
    "settings.recog.ru": {"en": "Russian", "zh": "俄文"},
    "settings.recog.nl": {"en": "Dutch", "zh": "荷兰文"},
    "settings.recog.tr": {"en": "Turkish", "zh": "土耳其文"},
    "settings.clipboard.label": {
        "en": "Save to Clipboard", "zh": "保存到剪贴板",
    },
    "settings.clipboard.desc": {
        "en": "Copy transcribed text to clipboard automatically.",
        "zh": "转录完成后自动将文本复制到剪贴板。",
    },
    "settings.startup.launch.label": {
        "en": "Launch at Login", "zh": "登录时启动",
    },
    "settings.startup.launch.desc": {
        "en": "ThunderTalk will start automatically when you log in.",
        "zh": "登录系统时自动启动 ThunderTalk。",
    },
    "settings.startup.silent.label": {
        "en": "Start Minimized", "zh": "静默启动",
    },
    "settings.startup.silent.desc": {
        "en": "Open to system tray without showing the main window.",
        "zh": "启动到状态栏，不打开主窗口。",
    },
    "settings.logs.enable.label": {
        "en": "Enable Logging", "zh": "启用日志",
    },
    "settings.logs.enable.desc": {
        "en": "Save debug logs to disk for troubleshooting.",
        "zh": "将调试日志保存到磁盘，便于排查问题。",
    },
    "settings.logs.dir": {"en": "Data Directory", "zh": "数据目录"},
    "settings.logs.open": {"en": "Open Folder", "zh": "打开文件夹"},
    "settings.hotkey.click_to_change": {
        "en": "✏ Click to change", "zh": "✏ 点击修改",
    },
    "settings.hotkey.press_keys": {"en": "Press keys…", "zh": "按下按键…"},

    # ── About page — update flow ────────────────────────────────────
    "about.update.checking": {
        "en": "Checking for updates…",
        "zh": "正在检查更新…",
    },
    "about.update.up_to_date": {
        "en": "You're on the latest version.",
        "zh": "当前已是最新版本。",
    },
    "about.update.available": {
        "en": "Update available: v{version}",
        "zh": "发现新版本：v{version}",
    },
    "about.update.notes_link": {
        "en": "Release notes",
        "zh": "发布说明",
    },
    "about.update.download": {
        "en": "Download Update",
        "zh": "下载更新",
    },
    "about.update.downloading": {
        "en": "Downloading… {pct}%",
        "zh": "下载中…  {pct}%",
    },
    "about.update.download_failed": {
        "en": "Download failed. Try again, or download manually from Releases.",
        "zh": "下载失败，请稍后重试或前往 Releases 手动下载。",
    },
    "about.update.install_restart": {
        "en": "Quit & Install",
        "zh": "退出并安装",
    },
    "about.update.installing": {
        "en": "Installing — ThunderTalk will relaunch in a moment.",
        "zh": "正在安装，ThunderTalk 将自动重启。",
    },
    "about.update.dev_mode": {
        "en": "Auto-update disabled (running from source).",
        "zh": "开发模式：自动更新已禁用。",
    },
    "about.update.check_failed": {
        "en": "Couldn't reach GitHub. Check your network and try again.",
        "zh": "无法连接 GitHub，请检查网络后重试。",
    },

    # Post-update permission hint — shown ONCE on first launch
    # after the version on disk changes. Ad-hoc code signing
    # changes the cdhash on every build, which resets macOS
    # permission grants tied to the old bundle.
    "post_update.title": {
        "en": "Updated to ThunderTalk v{version}",
        "zh": "已更新到 ThunderTalk v{version}",
    },
    "post_update.body": {
        "en": (
            "macOS may have cleared this version's permissions "
            "because the app signature changed.\n\n"
            "If the hotkey doesn't trigger recording or you see "
            "\"no audio\", open System Settings → Privacy & "
            "Security and:\n"
            "  • Remove the old ThunderTalk entry under "
            "Accessibility, then re-add this one.\n"
            "  • Re-grant Microphone access on the next recording "
            "attempt."
        ),
        "zh": (
            "由于应用签名变化，macOS 可能已经清除了上一版的权限授权。\n\n"
            "如果按下快捷键没反应或者录音报 \"no audio\"，"
            "请打开「系统设置」→「隐私与安全性」：\n"
            "  • 在「辅助功能」里删掉旧的 ThunderTalk 条目，"
            "再把当前这个版本添加进去。\n"
            "  • 下次录音时按提示重新授予「麦克风」权限。"
        ),
    },
    "post_update.open_settings": {
        "en": "Open System Settings",
        "zh": "打开系统设置",
    },
    "post_update.dismiss": {
        "en": "Got it",
        "zh": "知道了",
    },

    # ── About page ──────────────────────────────────────────────────
    "about.tagline": {
        "en": "« Lightning-fast, privacy-first voice-to-text »",
        "zh": "« 极速本地化、隐私优先的语音转文字 »",
    },
    "about.check_updates": {"en": "Check for Updates", "zh": "检查更新"},
    "about.website": {"en": "Website", "zh": "官网"},
    "about.report_issue": {"en": "Report Issue", "zh": "反馈问题"},
    "about.license": {"en": "License", "zh": "许可证"},
    "about.copyright": {
        "en": "© 2026 realAllenSong · PolyForm Noncommercial 1.0.0",
        "zh": "© 2026 realAllenSong · PolyForm Noncommercial 1.0.0",
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
