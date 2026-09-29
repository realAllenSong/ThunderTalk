"""Studio strings (transcribe · speak · clone a voice). Merged into
``i18n._STRINGS`` at import time. Keep {placeholders} identical across languages."""

from __future__ import annotations

STUDIO: dict[str, dict[str, str]] = {
    "common.cancel": {"en": "Cancel", "zh": "取消"},
    "nav.studio": {"en": "Studio", "zh": "工作室"},

    # ── page ─────────────────────────────────────────────────────────
    "studio.title": {"en": "Studio", "zh": "工作室"},
    "studio.subtitle": {
        "en": "Turn recordings into text, and text into natural speech — all on this Mac.",
        "zh": "把录音变成文字，把文字读成自然的语音——全部在这台 Mac 上完成。",
    },
    "studio.tab.transcribe": {"en": "Transcribe", "zh": "转写"},
    "studio.tab.speak": {"en": "Speak", "zh": "朗读"},
    "studio.cancelling": {"en": "Cancelling…", "zh": "正在取消…"},
    "studio.cancelling_hint": {"en": "Cancelling — finishing the current step…", "zh": "正在取消——等当前这一步结束…"},
    "studio.cancelled": {"en": "Cancelled.", "zh": "已取消。"},
    "studio.download": {"en": "Download", "zh": "下载"},
    "studio.choose_model": {"en": "Choose a model", "zh": "选择模型"},
    "studio.save": {"en": "Save…", "zh": "保存…"},
    "studio.saved": {"en": "Saved {name}", "zh": "已保存 {name}"},

    # ── errors ───────────────────────────────────────────────────────
    "studio.err.no_speech": {"en": "No speech was found in this file.", "zh": "这个文件里没有检测到语音。"},
    "studio.err.no_model": {
        "en": "No dictation model is loaded yet. Pick one on the Models page.",
        "zh": "还没有加载听写模型，请先到「模型」页选择一个。",
    },
    "studio.err.tts_missing": {
        "en": "The voice engine isn't downloaded yet.", "zh": "语音引擎还没有下载。",
    },
    "studio.err.memory": {
        "en": "Not enough memory for this. Close other apps, or try a shorter passage.",
        "zh": "内存不足。请关闭其他应用，或换一段短一点的内容。",
    },
    "studio.err.decode": {"en": "Couldn't read this file: {msg}", "zh": "无法读取这个文件：{msg}"},
    "studio.err.other": {"en": "Something went wrong: {msg}", "zh": "出错了：{msg}"},

    # ── transcribe ───────────────────────────────────────────────────
    "studio.drop.hint": {"en": "Drop a recording here", "zh": "把录音拖到这里"},
    "studio.drop.sub": {
        "en": "or click to choose — audio or video: MP3, M4A, WAV, MP4, MOV and more",
        "zh": "或点击选择——音频或视频：MP3、M4A、WAV、MP4、MOV 等",
    },
    "studio.drop.change": {"en": "click to choose another", "zh": "点击更换文件"},
    "studio.drop.dialog": {"en": "Choose a recording", "zh": "选择录音文件"},
    "studio.drop.filter": {"en": "Audio and video", "zh": "音频和视频"},
    "studio.mode.fast": {"en": "One speaker", "zh": "单人"},
    "studio.mode.speakers": {"en": "Multiple speakers", "zh": "多人对话"},
    "studio.mode.fast.desc": {
        "en": "Uses your dictation model ({model}) — nothing extra to download.",
        "zh": "使用你的听写模型（{model}），无需额外下载。",
    },
    "studio.mode.speakers.desc": {
        "en": "Labels who said what. Uses MOSS-Transcribe-Diarize — also the faster, more accurate choice for long recordings.",
        "zh": "标出谁说了什么，使用 MOSS-Transcribe-Diarize——处理长录音时也更快、更准。",
    },
    "studio.moss.missing": {
        "en": "The speaker model needs a one-time {size} download.",
        "zh": "说话人模型需要一次性下载 {size}。",
    },
    "studio.moss.downloading": {"en": "Downloading the speaker model", "zh": "正在下载说话人模型"},
    "studio.moss.ready": {"en": "Speaker model is ready", "zh": "说话人模型已就绪"},
    "studio.transcribe.go": {"en": "Transcribe", "zh": "开始转写"},
    "studio.progress.decode": {"en": "Reading the audio…", "zh": "正在读取音频…"},
    "studio.progress.load_moss": {"en": "Loading the speaker model…", "zh": "正在加载说话人模型…"},
    "studio.progress.diarize": {
        "en": "Listening for speakers — long recordings take a while…",
        "zh": "正在识别说话人——较长的录音需要一些时间…",
    },
    "studio.progress.eta": {"en": "about {t} left", "zh": "约剩 {t}"},
    "studio.progress.part": {"en": "Transcribing part {i} of {n}…", "zh": "正在转写第 {i} / {n} 段…"},
    "studio.stats": {
        "en": "{dur} of audio in {took} — {x}× faster than real time",
        "zh": "{dur} 的音频，用时 {took}，比实时快 {x} 倍",
    },
    "studio.speaker_count": {"en": "{n} speakers", "zh": "{n} 位说话人"},
    "studio.timestamps": {"en": "Timestamps", "zh": "时间戳"},
    "studio.text_only": {"en": "Text only", "zh": "纯文本"},
    "studio.copy": {"en": "Copy", "zh": "复制"},
    "studio.copied": {"en": "Copied", "zh": "已复制"},
    "studio.export": {"en": "Export…", "zh": "导出…"},
    "studio.export.txt": {"en": "Plain text (.txt)", "zh": "纯文本（.txt）"},
    "studio.export.md": {"en": "Markdown (.md)", "zh": "Markdown（.md）"},
    "studio.export.srt": {"en": "Subtitles (.srt)", "zh": "字幕（.srt）"},
    "studio.export.vtt": {"en": "Web subtitles (.vtt)", "zh": "网页字幕（.vtt）"},
    "studio.export.json": {"en": "JSON (.json)", "zh": "JSON（.json）"},
    "studio.rename_speaker": {"en": "Rename speaker", "zh": "重命名说话人"},
    "studio.rename_speaker.prompt": {"en": "Name for “{name}”:", "zh": "“{name}”的名字："},

    # ── speak: engine ────────────────────────────────────────────────
    "studio.engine.title": {"en": "Voice engine", "zh": "语音引擎"},
    "studio.engine.title_clone": {"en": "Voice engine for cloned voices", "zh": "克隆声音所需的语音引擎"},
    "studio.engine.body": {
        "en": "The built-in voices need a one-time {size} download. After that everything runs on this Mac, offline.",
        "zh": "内置音色需要一次性下载 {size}，之后完全在本机离线运行。",
    },
    "studio.engine.body_clone": {
        "en": "Speaking in your own voice uses a second model — a one-time {size} download. It runs on this Mac, offline.",
        "zh": "用你自己的声音朗读需要另一个模型——一次性下载 {size}，在本机离线运行。",
    },
    "studio.engine.download": {"en": "Download voice engine", "zh": "下载语音引擎"},
    "studio.engine.connecting": {"en": "Connecting…", "zh": "正在连接…"},
    "studio.engine.ready": {"en": "Voice engine is ready", "zh": "语音引擎已就绪"},

    # ── speak: voices ────────────────────────────────────────────────
    "studio.voices.builtin": {"en": "Built-in voices", "zh": "内置音色"},
    "studio.voices.mine": {"en": "My voices", "zh": "我的声音"},
    "studio.voices.mine_sub": {"en": "cloned · {sec} s", "zh": "已克隆 · {sec} 秒"},
    "studio.voices.mine_hint": {
        "en": "Record 5–15 seconds of your own voice and it can read anything you type.",
        "zh": "录 5–15 秒你自己的声音，它就能朗读你输入的任何内容。",
    },
    "studio.voices.clone": {"en": "Clone my voice", "zh": "克隆我的声音"},
    "studio.voices.manage": {"en": "Manage", "zh": "管理"},
    "studio.voices.pick_mine": {"en": "Select one of your voices first.", "zh": "请先选中你的一个声音。"},
    "studio.voices.rename": {"en": "Rename", "zh": "重命名"},
    "studio.voices.name": {"en": "Voice name", "zh": "声音名称"},
    "studio.voices.edit_text": {"en": "Edit what was said", "zh": "修改朗读内容"},
    "studio.voices.delete": {"en": "Delete", "zh": "删除"},
    "studio.voices.delete_title": {"en": "Delete “{name}”?", "zh": "删除“{name}”？"},
    "studio.voices.delete_body": {
        "en": "The saved recording is removed from this Mac. You can clone the voice again any time.",
        "zh": "保存的录音会从这台 Mac 上删除，之后可以随时重新克隆。",
    },
    "studio.voices.saved": {"en": "Saved voice “{name}”", "zh": "已保存声音“{name}”"},
    "studio.voices.need_text": {
        "en": "This voice has no transcript. Use Manage ▸ Edit what was said.",
        "zh": "这个声音缺少对应文字，请在「管理」里补充朗读内容。",
    },

    # ── speak: text & generation ─────────────────────────────────────
    "studio.text.placeholder": {"en": "Type or paste what you want to hear…", "zh": "输入或粘贴想听的文字…"},
    "studio.text.count": {"en": "{n} characters · about {est}", "zh": "{n} 字 · 约 {est}"},
    "studio.lang.label": {"en": "Language", "zh": "语言"},
    "studio.lang.auto": {"en": "Detect automatically", "zh": "自动识别"},
    "studio.lang.chinese": {"en": "Chinese", "zh": "中文"},
    "studio.lang.english": {"en": "English", "zh": "英语"},
    "studio.lang.japanese": {"en": "Japanese", "zh": "日语"},
    "studio.lang.korean": {"en": "Korean", "zh": "韩语"},
    "studio.lang.french": {"en": "French", "zh": "法语"},
    "studio.lang.german": {"en": "German", "zh": "德语"},
    "studio.lang.spanish": {"en": "Spanish", "zh": "西班牙语"},
    "studio.lang.italian": {"en": "Italian", "zh": "意大利语"},
    "studio.lang.portuguese": {"en": "Portuguese", "zh": "葡萄牙语"},
    "studio.lang.russian": {"en": "Russian", "zh": "俄语"},
    "studio.speed.label": {"en": "Speed", "zh": "语速"},
    "studio.speak.go": {"en": "Generate speech", "zh": "生成语音"},
    "studio.speak.loading": {"en": "Loading the voice engine…", "zh": "正在加载语音引擎…"},
    "studio.speak.piece": {"en": "Speaking part {i} of {n}…", "zh": "正在生成第 {i} / {n} 段…"},
    "studio.speak.done": {
        "en": "{dur} of speech, made in {took} · {voice}",
        "zh": "{dur} 的语音，用时 {took} · {voice}",
    },
    "studio.speak.warn": {
        "en": "Part of this passage may sound off — {snippet}. Generating again usually fixes it.",
        "zh": "其中一段可能听起来不自然——{snippet}。再生成一次通常就能解决。",
    },
    "studio.player.keys": {"en": "Space: play / pause · ← →: back / forward 5 s", "zh": "空格：播放 / 暂停 · ← →：后退 / 前进 5 秒"},
    "studio.play_failed": {"en": "Couldn't play — check your output device", "zh": "无法播放——请检查输出设备"},
    "studio.save.wav": {"en": "lossless", "zh": "无损"},
    "studio.save.m4a": {"en": "small file", "zh": "体积小"},

    # ── clone dialog ─────────────────────────────────────────────────
    "studio.clone.title": {"en": "Clone a voice", "zh": "克隆声音"},
    "studio.clone.intro": {
        "en": "Record 5–15 seconds of natural speech in a quiet room, or import a clean recording of one person. "
              "It stays on this Mac.",
        "zh": "在安静的环境里录 5–15 秒自然的说话，或导入一段只有一个人、干净的录音。内容只保存在这台 Mac 上。",
    },
    "studio.clone.record": {"en": "Record", "zh": "录音"},
    "studio.clone.import": {"en": "Choose a file…", "zh": "选择文件…"},
    "studio.clone.try_reading": {"en": "Try reading: {text}", "zh": "可以试着朗读：{text}"},
    "studio.clone.sample_en": {
        "en": "“The morning light comes slowly through the window, and I make a cup of tea before the day begins.”",
        "zh": "“The morning light comes slowly through the window, and I make a cup of tea before the day begins.”",
    },
    "studio.clone.sample_zh": {
        "en": "“今天天气很好，我打算先去咖啡店坐一会儿，然后沿着河边慢慢走回家。”",
        "zh": "“今天天气很好，我打算先去咖啡店坐一会儿，然后沿着河边慢慢走回家。”",
    },
    "studio.clone.length": {"en": "{sec} seconds", "zh": "{sec} 秒"},
    "studio.clone.warn.clipping": {"en": "too loud — move back from the mic", "zh": "音量过大——离麦克风远一点"},
    "studio.clone.warn.trimmed_long": {"en": "only the first ~20 s are used", "zh": "只使用前约 20 秒"},
    "studio.clone.warn.too_quiet": {"en": "too quiet to clone from", "zh": "声音太轻，无法克隆"},
    "studio.clone.warn.short": {"en": "under 5 s — 5–15 s clones better", "zh": "不足 5 秒——5–15 秒效果更好"},
    "studio.clone.said": {"en": "What is said in the recording", "zh": "录音里说的内容"},
    "studio.clone.said_ph": {"en": "The words spoken in the clip…", "zh": "录音里说的话…"},
    "studio.clone.said_hint": {
        "en": "Check the words — a wrong transcript makes the clone worse.",
        "zh": "请核对文字——文字有误会让克隆效果变差。",
    },
    "studio.clone.transcribing": {"en": "Writing out what was said…", "zh": "正在自动识别录音内容…"},
    "studio.clone.name_ph": {"en": "Name this voice", "zh": "给这个声音起个名字"},
    "studio.clone.save": {"en": "Save voice", "zh": "保存声音"},
    "studio.clone.err_mic": {"en": "Couldn't open the microphone: {msg}", "zh": "无法打开麦克风：{msg}"},
    "studio.clone.err_short_rec": {"en": "That was too short — try again.", "zh": "录音太短，请重试。"},
    "studio.clone.err_silent": {
        "en": "Nothing was recorded. Check microphone permission in System Settings ▸ Privacy & Security.",
        "zh": "没有录到声音。请在「系统设置 ▸ 隐私与安全性」里检查麦克风权限。",
    },
}
