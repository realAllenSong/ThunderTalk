"""Model facts and experimental CPU engine strings."""

MODELS = {
    # ── Cloud APIs (Models page) ──
    "cloud.title": {"en": "Cloud APIs", "zh": "云端接口"},
    "cloud.optional": {"en": "Optional", "zh": "可选"},
    "cloud.note": {
        "en": "Use your own API key for OpenAI, Gemini, Groq, ElevenLabs or any OpenAI-compatible "
              "server. Audio or text you send with a cloud engine goes to that provider and is billed "
              "to your account. ThunderTalk stays offline unless you choose one; keys are kept in the "
              "macOS Keychain.",
        "zh": "使用你自己的 API 密钥接入 OpenAI、Gemini、Groq、ElevenLabs 或任何兼容 OpenAI 的服务。"
              "使用云端引擎时，音频或文本会发送给对应服务商，并按你的账户计费。除非你主动选择，"
              "ThunderTalk 始终离线运行；密钥保存在 macOS 钥匙串中。",
    },
    "cloud.test": {"en": "Test", "zh": "测试"},
    "cloud.base_url": {"en": "Base URL", "zh": "接口地址"},
    "cloud.key": {"en": "API key", "zh": "API 密钥"},
    "cloud.key_hint": {"en": "Paste API key", "zh": "粘贴 API 密钥"},
    "cloud.key_saved_hint": {"en": "Saved in Keychain · paste to replace", "zh": "已存入钥匙串 · 粘贴可替换"},
    "cloud.save": {"en": "Save", "zh": "保存"},
    "cloud.remove": {"en": "Remove", "zh": "移除"},
    "cloud.asr": {"en": "Speech to text", "zh": "语音转文字"},
    "cloud.tts": {"en": "Speech", "zh": "朗读"},
    "cloud.voice": {"en": "Voice", "zh": "声音"},
    "cloud.use_dictation": {"en": "Use for dictation", "zh": "用于听写"},
    "cloud.in_use": {"en": "In use", "zh": "使用中"},
    "cloud.key_saved": {"en": "Key saved", "zh": "已保存密钥"},
    "cloud.key_env": {"en": "Key from environment", "zh": "密钥来自环境变量"},
    "cloud.key_optional": {"en": "Key optional", "zh": "密钥可选"},
    "cloud.no_key": {"en": "No key", "zh": "未设置密钥"},
    "cloud.saved": {"en": "Key saved to the Keychain.", "zh": "密钥已存入钥匙串。"},
    "cloud.removed": {"en": "Key removed.", "zh": "密钥已移除。"},
    "cloud.testing": {"en": "Testing…", "zh": "正在测试…"},
    "cloud.test_ok": {"en": "Works ({s} s).", "zh": "可以使用（{s} 秒）。"},
    "cloud.test_failed": {"en": "Test failed: {err}", "zh": "测试失败：{err}"},
    "cloud.not_ready": {"en": "Add an API key (and base URL for a custom server) first.",
                        "zh": "请先填写 API 密钥（自定义服务还需填写接口地址）。"},
    "cloud.confirm_title": {"en": "Dictate through {name}?", "zh": "通过云端听写？"},
    "cloud.confirm_body": {
        "en": "Each dictation will be sent to {name} and billed to your API account. Live preview "
              "is turned off for cloud engines so only the finished recording is sent. You can switch "
              "back to a local model at any time.",
        "zh": "每次听写的录音都会发送给 {name}，并按你的 API 账户计费。云端引擎不使用实时预览，"
              "只发送完整录音。你可以随时切回本地模型。",
    },
    "cloud.confirm_go": {"en": "Use cloud dictation", "zh": "使用云端听写"},
    "cloud.dictation_failed": {"en": "Cloud dictation failed: {err}", "zh": "云端听写失败：{err}"},
    "cloud.group": {"en": "Cloud", "zh": "云端"},
    "models.experimental": {"en": "Test model", "zh": "测试模型"},
    "models.cpu_gpu": {"en": "CPU / Apple GPU", "zh": "CPU / Apple GPU"},
    "models.params": {"en": "{n} parameters", "zh": "{n} 参数"},
    "models.params_unknown": {
        "en": "Parameter count unpublished",
        "zh": "参数量未公布",
    },
    "models.cpu": {"en": "CPU", "zh": "CPU"},
    "models.gpu": {"en": "Apple GPU", "zh": "Apple GPU"},
    "models.no_chinese": {"en": "No Chinese", "zh": "不支持中文"},
    "models.feature.hotwords": {"en": "Hotwords", "zh": "热词"},
    "models.feature.speakers": {"en": "Speaker labels", "zh": "说话人标签"},
    "models.feature.clone": {"en": "Voice cloning", "zh": "声音克隆"},
    "models.feature.timestamps": {"en": "Native timestamps", "zh": "原生时间戳"},
    "models.partial_languages": {
        "en": "50+ languages; only the publisher's named languages are listed.",
        "zh": "支持 50 多种语言；此处仅列出官方明确列出的语言。",
    },
    "models.qwen_dialects": {
        "en": "30 languages + 22 Chinese dialects (not 52 separate languages).",
        "zh": "30 种语言及 22 种中文方言（并非 52 种语言）。",
    },
    "models.speed.music_xl": {
        "en": "170 s song: ~6 min 14 s · M3 Max · planner on",
        "zh": "170 秒歌曲：约 6 分 14 秒 · M3 Max · 启用规划",
    },
    "models.speed.music_turbo_mps": {
        "en": "60 s instrumental: ~20 s · M3 Max · MPS, no planner",
        "zh": "60 秒纯音乐：约 20 秒 · M3 Max · MPS，未启用规划",
    },
    "models.speed.music_turbo": {
        "en": "170 s song: ~1 min 11 s · M3 Max · planner on",
        "zh": "170 秒歌曲：约 1 分 11 秒 · M3 Max · 启用规划",
    },
    "models.speed.unmeasured": {"en": "Speed not measured", "zh": "速度未测量"},
    "models.speed.qwen_mlx": {
        "en": "~11× real time · M3 Max",
        "zh": "约 11 倍实时速度 · M3 Max",
    },
    "models.speed.qwen_cpu": {
        "en": "~12× real time · M3 Max",
        "zh": "约 12 倍实时速度 · M3 Max",
    },
    "models.speed.parakeet": {
        "en": "~50× real time · M3 Max",
        "zh": "约 50 倍实时速度 · M3 Max",
    },
    "models.speed.fast": {"en": "Lightweight, fast", "zh": "轻量快速"},
    "models.speed.kokoro": {
        "en": "~4× real time · M3 Max",
        "zh": "约 4 倍实时速度 · M3 Max",
    },
    "models.speed.realtime": {
        "en": "~Real time · Apple silicon",
        "zh": "约实时速度 · Apple 芯片",
    },
    "model.blurb.funasr-nano-int8": {
        "en": "Chinese, English and Japanese, with hotwords. Faster and lighter; best when people talk nearby. Studio uses sentence-span timestamps.",
        "zh": "支持中英日语及热词。更快更省内存，旁边有人说话时最准。Studio 提供句段时间戳。",
    },
    "models.backbone_params": {
        "en": "~0.8B backbone parameters",
        "zh": "主干约 0.8B 参数",
    },
}
