"""Model facts and experimental CPU engine strings."""

MODELS = {
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
    "model.blurb.fireredasr2-ctc-int8": {
        "en": "Chinese / English recognition. Studio uses sentence-span timestamps.",
        "zh": "中英文识别。Studio 提供句段时间戳。",
    },
    "model.blurb.fireredasr2-aed-int8": {
        "en": "Chinese / English recognition. This ONNX export uses Studio sentence-span timestamps.",
        "zh": "中英文识别。此 ONNX 版本使用 Studio 的句段时间戳。",
    },
    "model.blurb.funasr-nano-int8": {
        "en": "Chinese, English and Japanese, with hotwords. Studio uses sentence-span timestamps.",
        "zh": "支持中英日语及热词。Studio 提供句段时间戳。",
    },
    "studio.engine.tag.zipvoice": {
        "en": "CPU voice cloning · reference audio and its exact transcript required",
        "zh": "CPU 声音克隆 · 需要参考音频及其准确文本",
    },
    "models.backbone_params": {
        "en": "~0.8B backbone parameters",
        "zh": "主干约 0.8B 参数",
    },
}
