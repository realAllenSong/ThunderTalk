"""Strings for the speech engines built into macOS (Apple Speech, system voices)."""

APPLE = {
    "apple.variant": {"en": "Built in", "zh": "系统自带"},
    "models.ane": {"en": "CPU / Neural Engine", "zh": "CPU / 神经网络引擎"},
    "models.speed.apple": {
        "en": "~15× real time · M3 Max",
        "zh": "约 15 倍实时速度 · M3 Max",
    },
    "models.speed.apple_tts": {
        "en": "~37× real time · M3 Max",
        "zh": "约 37 倍实时速度 · M3 Max",
    },
    "model.blurb.apple-speech": {
        "en": "macOS's own on-device recognizer: nothing to download, audio never leaves the Mac, "
              "about 3× faster than Qwen3-ASR ONNX. Fine for clear, close speech (5.8 % Chinese CER vs 3.3 %), "
              "but it drops most words in noisy, far-away, fast or sung speech, and English words "
              "inside Chinese sentences (35 % vs 3 %). Languages are the dictation languages installed on this Mac.",
        "zh": "macOS 自带的本机识别：无需下载，音频不离开这台 Mac，速度约为 Qwen3-ASR ONNX 的 3 倍。"
              "清晰近讲的语音没问题（中文字错率 5.8%，Qwen 为 3.3%），但嘈杂、远场、语速快或唱歌时会漏掉大部分内容，"
              "中文句子里夹的英文词也常认错（35%，Qwen 为 3%）。可用语言取决于本机已安装的听写语言。",
    },
    "apple.btn.setup": {"en": "Add a language", "zh": "添加语言"},
    "apple.unavailable": {
        "en": "Apple speech recognition is not available on this Mac.",
        "zh": "这台 Mac 无法使用 Apple 语音识别。",
    },
    "apple.no_locales": {
        "en": "No dictation language is installed for on-device recognition. Add one in System Settings ▸ "
              "Keyboard ▸ Dictation ▸ Languages, then activate again.",
        "zh": "这台 Mac 没有安装可本机识别的听写语言。请在“系统设置 ▸ 键盘 ▸ 听写 ▸ 语言”中添加，然后重新启用。",
    },
    "apple.denied": {
        "en": "Speech Recognition access is turned off for ThunderTalk. Turn it on in System Settings ▸ "
              "Privacy & Security ▸ Speech Recognition, then activate Apple again.",
        "zh": "ThunderTalk 的“语音识别”权限已关闭。请在“系统设置 ▸ 隐私与安全性 ▸ 语音识别”中打开，然后重新启用 Apple。",
    },
    "apple.locale_unsupported": {
        "en": "Apple can't recognise this language on this Mac. Add it in System Settings ▸ Keyboard ▸ "
              "Dictation ▸ Languages, or pick another model.",
        "zh": "这台 Mac 上的 Apple 识别不支持这种语言。请在“系统设置 ▸ 键盘 ▸ 听写 ▸ 语言”中添加，或换一个模型。",
    },
    "studio.engine.tag.apple": {"en": "the voices installed in macOS · no download · CPU",
                                "zh": "macOS 已安装的系统语音 · 无需下载 · CPU"},
    "studio.voices.system": {"en": "System", "zh": "系统"},
    "studio.voices.none_apple": {
        "en": "No Chinese or English system voice is installed. Add one in System Settings ▸ "
              "Accessibility ▸ Spoken Content ▸ System voice ▸ Manage Voices.",
        "zh": "没有安装中文或英文系统语音。请在“系统设置 ▸ 辅助功能 ▸ 朗读内容 ▸ 系统声音 ▸ 管理声音”中添加。",
    },
    "studio.voice.quality.enhanced": {"en": "Enhanced", "zh": "增强"},
    "studio.voice.quality.premium": {"en": "Premium", "zh": "高级"},
}
