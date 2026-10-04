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
    "studio.err.model_busy": {
        "en": "Another Studio job is using an extra model. Try again when it finishes.",
        "zh": "另一项工作室任务正在使用额外模型，请等它完成后重试。",
    },
    "studio.err.decode": {"en": "Couldn't read this file: {msg}", "zh": "无法读取这个文件：{msg}"},
    "studio.err.other": {"en": "Something went wrong: {msg}", "zh": "出错了：{msg}"},

    # ── transcribe ───────────────────────────────────────────────────
    "studio.drop.hint": {"en": "Drop a recording here", "zh": "把录音拖到这里"},
    "studio.drop.another": {"en": "Drop another file or click to browse", "zh": "拖入下一个文件，或点击选择"},
    "studio.drop.sub": {
        "en": "or click to choose — audio or video: MP3, M4A, WAV, MP4, MOV and more. Several at once make a queue.",
        "zh": "或点击选择——音频或视频：MP3、M4A、WAV、MP4、MOV 等。一次选多个会排成队列。",
    },
    "studio.drop.change": {"en": "click to choose another", "zh": "点击更换文件"},
    "studio.drop.dialog": {"en": "Choose a recording", "zh": "选择录音文件"},
    "studio.drop.filter": {"en": "Audio and video", "zh": "音频和视频"},
    "studio.model": {"en": "Model", "zh": "模型"},
    "studio.model.active": {"en": "Active dictation model", "zh": "当前听写模型"},
    "studio.model.desc": {
        "en": "Runs on this Mac. Your dictation model is the fast default.",
        "zh": "在本机运行。默认使用你的听写模型，速度最快。",
    },
    "studio.model.moss.desc": {
        "en": "Can label who said what — also the faster, more accurate choice for long recordings.",
        "zh": "可以标出谁说了什么——处理长录音时也更快、更准。",
    },
    "studio.label_speakers": {"en": "Label speakers", "zh": "标注说话人"},
    "studio.moss.missing": {
        "en": "The speaker model needs a one-time {size} download.",
        "zh": "说话人模型需要一次性下载 {size}。",
    },
    "studio.moss.downloading": {"en": "Downloading the speaker model", "zh": "正在下载说话人模型"},
    "studio.moss.ready": {"en": "Speaker model is ready", "zh": "说话人模型已就绪"},
    "studio.transcribe.go": {"en": "Transcribe", "zh": "开始转写"},
    "studio.progress.decode": {"en": "Reading the audio…", "zh": "正在读取音频…"},
    "studio.progress.load_moss": {"en": "Loading the speaker model…", "zh": "正在加载说话人模型…"},
    "studio.progress.load_model": {"en": "Loading the selected model…", "zh": "正在加载所选模型…"},
    "studio.progress.memory_fallback": {
        "en": "Not enough free memory for another model. Using your dictation model instead.",
        "zh": "可用内存不足，无法加载额外模型。本次改用你的听写模型。",
    },
    "studio.progress.model_busy_fallback": {
        "en": "Another Studio job is using an extra model. Using your dictation model instead.",
        "zh": "另一项工作室任务正在使用额外模型。本次改用你的听写模型。",
    },
    "studio.progress.diarize": {
        "en": "Listening for speakers — long recordings take a while…",
        "zh": "正在识别说话人——较长的录音需要一些时间…",
    },
    "studio.progress.eta": {"en": "about {t} left", "zh": "约剩 {t}"},
    "studio.progress.yield": {
        "en": "Paused while you dictate…",
        "zh": "你正在听写，转写已暂停…",
    },
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
    "studio.summary": {"en": "AI meeting notes", "zh": "AI 会议纪要"},
    "studio.notes.queue": {"en": "Generate notes after each transcription", "zh": "每项转写完成后生成会议纪要"},
    "studio.notes.queue_hint": {"en": "Also saves a Markdown transcript with notes. Items run one at a time.",
                                "zh": "同时保存含纪要的 Markdown 文稿，逐项处理。"},
    "studio.notes.settings": {"en": "Open AI Proofread", "zh": "打开 AI 校对"},
    "studio.notes.no_provider": {
        "en": "Choose a ready provider and model on the AI Proofread page: sign in to an installed CLI or start your model server. No model is downloaded. Dictation proofreading can stay off.",
        "zh": "请在「AI 校对」页选择已就绪的服务与模型：登录已安装的 CLI，或启动模型服务。不会下载模型，听写校对开关可保持关闭。"},
    "studio.notes.local": {"en": "Notes use {provider} · {model}. Transcript text stays on this Mac.",
                           "zh": "纪要使用 {provider} · {model}，文稿内容留在本机。"},
    "studio.notes.cloud": {"en": "Notes use {provider} · {model}. Transcript text is sent to this provider, which may process it in the cloud.",
                           "zh": "纪要使用 {provider} · {model}。文稿内容会发送给此服务，可能在云端处理。"},
    "studio.notes.copy": {"en": "Copy notes", "zh": "复制纪要"},
    "studio.notes.collapse": {"en": "Hide notes", "zh": "收起纪要"},
    "studio.notes.expand": {"en": "Show notes", "zh": "展开纪要"},
    "studio.notes.save": {"en": "Save notes (Markdown)…", "zh": "保存纪要（Markdown）…"},
    "studio.notes.part": {"en": "Writing notes · part {i} of {n}…", "zh": "正在生成纪要 · 第 {i} / {n} 段…"},
    "studio.notes.merge": {"en": "Merging meeting notes…", "zh": "正在合并会议纪要…"},
    "studio.notes.done": {"en": "Meeting notes ready", "zh": "会议纪要已生成"},
    "studio.notes.err.no_provider": {"en": "No ready notes provider. Open the AI Proofread page.",
                                     "zh": "没有已就绪的纪要服务，请打开「AI 校对」页。"},
    "studio.notes.err.too_long": {"en": "This transcript exceeds the 16-part notes limit. Split the recording and try again.",
                                  "zh": "文稿超出纪要的 16 段上限，请拆分录音后重试。"},
    "studio.notes.err.empty": {"en": "There is no transcript text to summarize.", "zh": "没有可生成纪要的文稿内容。"},
    "studio.notes.err.failed": {"en": "Notes request failed or timed out (60 seconds per call). Check the provider on the AI Proofread page. The transcript is kept.",
                                "zh": "纪要请求失败或超时（每次请求限 60 秒）。请在「AI 校对」页检查服务，文稿已保留。"},
    "studio.notes.err.invalid": {"en": "The provider returned empty, oversized or wrong-language notes. Try again. The transcript is kept.",
                                 "zh": "服务返回的纪要为空、过长或语言不符，请重试。文稿已保留。"},

    # ── transcribe: links ────────────────────────────────────────────
    "studio.link.placeholder": {
        "en": "Or paste a video link — YouTube, Bilibili and more",
        "zh": "或粘贴视频链接——YouTube、B 站等",
    },
    "studio.link.add": {"en": "Add", "zh": "添加"},
    "studio.link.not_link": {"en": "That doesn't look like a link.", "zh": "这看起来不是一个链接。"},
    "studio.link.sub": {
        "en": "Link · {site} — only the audio is downloaded, and deleted afterwards",
        "zh": "链接 · {site}——只下载音频，用完即删",
    },
    "studio.progress.fetch": {"en": "Getting the video info…", "zh": "正在获取视频信息…"},
    "studio.progress.download": {"en": "Downloading the audio… {done} of {total}", "zh": "正在下载音频… {done} / {total}"},
    "studio.progress.download_nosize": {"en": "Downloading the audio… {done}", "zh": "正在下载音频… {done}"},
    "studio.link.err.missing": {
        "en": "Link downloads aren't available in this build.", "zh": "这个版本不支持链接下载。",
    },
    "studio.link.err.unsupported": {
        "en": "This site isn't supported. Try the page link of a single video.",
        "zh": "暂不支持这个网站。请试试单个视频的页面链接。",
    },
    "studio.link.err.playlist": {
        "en": "That's a playlist or channel. Paste the link of a single video.",
        "zh": "这是播放列表或频道，请粘贴单个视频的链接。",
    },
    "studio.link.err.live": {
        "en": "Live streams can't be transcribed. Try again once it has ended.",
        "zh": "直播无法转写，请等直播结束后再试。",
    },
    "studio.link.err.private": {"en": "This video is private.", "zh": "这是私密视频，无法访问。"},
    "studio.link.err.login": {
        "en": "This video needs a signed-in account (members-only, age-restricted, or the site asked to "
              "confirm you're not a bot). ThunderTalk doesn't sign in for you.",
        "zh": "这个视频需要登录账号才能观看（会员专享、年龄限制，或网站要求验证不是机器人）。ThunderTalk 不会代你登录。",
    },
    "studio.link.err.geo": {
        "en": "This video isn't available in your region.", "zh": "这个视频在你所在的地区不可用。",
    },
    "studio.link.err.unavailable": {
        "en": "This video is unavailable — it may have been deleted, or be blocked in your region.",
        "zh": "这个视频无法访问——可能已被删除，或在你所在的地区不可用。",
    },
    "studio.link.err.no_audio": {
        "en": "The site didn't offer a downloadable audio track for this video — it may need a "
              "signed-in account. Try again later, or use another link.",
        "zh": "网站没有为这个视频提供可下载的音频——可能需要登录账号。请稍后再试，或换一个链接。",
    },
    "studio.link.partial": {
        "en": "Only {got} of {total} could be downloaded — the site serves a preview of this video "
              "without signing in. The transcript covers that part only.",
        "zh": "只下载到 {total} 中的 {got}——未登录时网站只提供这个视频的试看片段，文稿只包含这一部分。",
    },
    "studio.link.err.blocked": {
        "en": "The site refused the download for now (rate limit). Wait a minute and try again.",
        "zh": "网站暂时拒绝了下载（访问过于频繁），请稍等一分钟再试。",
    },
    "studio.link.err.network": {
        "en": "Couldn't reach the site. Check your internet connection.",
        "zh": "无法连接到网站，请检查网络连接。",
    },
    "studio.link.err.other": {"en": "Couldn't download this link: {msg}", "zh": "无法下载这个链接：{msg}"},

    # ── transcribe: queue ────────────────────────────────────────────
    "studio.batch.title": {"en": "Queue · {n}", "zh": "队列 · {n}"},
    "studio.batch.go": {"en": "Transcribe all ({n})", "zh": "全部转写（{n}）"},
    "studio.batch.cancel_all": {"en": "Cancel all", "zh": "全部取消"},
    "studio.batch.clear": {"en": "Clear", "zh": "清空"},
    "studio.batch.save_to": {"en": "Save to", "zh": "保存到"},
    "studio.batch.next_to": {"en": "Next to each file", "zh": "原文件旁"},
    "studio.batch.folder": {"en": "Folder…", "zh": "文件夹…"},
    "studio.batch.next_to_hint": {"en": "Results from links go to Downloads.", "zh": "链接的结果保存到「下载」文件夹。"},
    "studio.batch.formats": {"en": "Formats: {list}", "zh": "格式：{list}"},
    "studio.batch.waiting": {"en": "Waiting", "zh": "等待中"},
    "studio.batch.running": {"en": "Working…", "zh": "处理中…"},
    "studio.batch.saved": {"en": "Saved · {files}", "zh": "已保存 · {files}"},
    "studio.batch.failed": {"en": "Failed — {msg}", "zh": "失败——{msg}"},
    "studio.batch.view": {"en": "View", "zh": "查看"},
    "studio.batch.remove": {"en": "Remove from the queue", "zh": "从队列中移除"},
    "studio.batch.cancel_item": {"en": "Cancel this one", "zh": "取消这一项"},
    "studio.batch.finished": {"en": "Queue finished: {ok} of {n} saved", "zh": "队列完成：{n} 项中已保存 {ok} 项"},

    # ── transcribe: history ──────────────────────────────────────────
    "studio.history": {"en": "History", "zh": "历史记录"},
    "studio.history.search": {"en": "Search titles and transcript text…", "zh": "搜索标题和文稿内容…"},
    "studio.history.more": {"en": "Load more", "zh": "加载更多"},
    "studio.history.rename": {"en": "Rename", "zh": "重命名"},
    "studio.history.delete": {"en": "Delete", "zh": "删除"},
    "studio.history.title": {"en": "Transcript title", "zh": "文稿标题"},
    "studio.history.delete_body": {
        "en": "Delete this saved transcript and its notes from this Mac?",
        "zh": "从本机删除这份文稿及其纪要？",
    },
    "studio.history.error": {"en": "Could not save or open history: {msg}", "zh": "无法保存或打开历史记录：{msg}"},

    # ── transcribe: burn subtitles into a video ──────────────────────
    "studio.burn": {"en": "Add subtitles to video…", "zh": "给视频加字幕…"},
    "studio.burn.hard": {"en": "Burn subtitles into the video (always visible)", "zh": "把字幕直接印在画面上（始终可见）"},
    "studio.burn.soft": {"en": "Add a subtitle track (viewer can turn on/off)", "zh": "添加字幕轨（观看时可以开关）"},
    "studio.burn.dialog": {"en": "Save the subtitled video", "zh": "保存带字幕的视频"},
    "studio.burn.need_ffmpeg": {"en": "Burning subtitles needs ffmpeg", "zh": "烧录字幕需要 ffmpeg"},
    "studio.burn.need_ffmpeg_body": {
        "en": "ffmpeg is a free video tool that ThunderTalk uses to write the new video. Install it with "
              "Homebrew by running “brew install ffmpeg” in Terminal, then try again.",
        "zh": "ffmpeg 是一个免费的视频工具，ThunderTalk 用它来写出新视频。请在「终端」里运行"
              "“brew install ffmpeg”通过 Homebrew 安装，然后再试一次。",
    },
    "studio.burn.copy_cmd": {"en": "Copy the command", "zh": "复制命令"},
    "studio.burn.close": {"en": "Close", "zh": "关闭"},
    "studio.progress.probe": {"en": "Reading the video…", "zh": "正在读取视频…"},
    "studio.progress.render": {"en": "Drawing the subtitles…", "zh": "正在绘制字幕…"},
    "studio.progress.encode": {"en": "Writing the video… {pct}%", "zh": "正在写入视频… {pct}%"},
    "studio.burn.saved": {"en": "Saved {name} ({size})", "zh": "已保存 {name}（{size}）"},
    "studio.burn.err.no_ffmpeg": {"en": "ffmpeg wasn't found.", "zh": "没有找到 ffmpeg。"},
    "studio.burn.err.no_video": {"en": "This file has no video picture.", "zh": "这个文件没有视频画面。"},
    "studio.burn.err.failed": {"en": "Couldn't write the video: {msg}", "zh": "无法写出视频：{msg}"},

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
    "studio.engine.title_named": {"en": "{name} voice engine", "zh": "{name} 语音引擎"},
    "studio.engine.size": {"en": "One-time download: {size}.", "zh": "一次性下载 {size}。"},
    "studio.engine.download_named": {"en": "Download {name} ({size})", "zh": "下载 {name}（{size}）"},
    "studio.engine.tag.voxcpm2": {"en": "studio quality, designed voices, cloning · Apple GPU",
                                  "zh": "录音棚音质、按描述设计音色、可克隆 · Apple GPU"},
    "studio.engine.tag.kokoro": {"en": "small and fast, many Chinese voices · runs on the CPU",
                                 "zh": "小巧快速、中文音色多 · CPU 运行"},
    "studio.engine.tag.indextts": {"en": "cloning with emotion control · Apple GPU",
                                   "zh": "克隆并可控制情感 · Apple GPU"},
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
    "studio.engine.pick": {"en": "Engine", "zh": "引擎"},
    "studio.voices.no_clone": {"en": "Kokoro has built-in voices only. Switch to VoxCPM2 or IndexTTS to use your own voice.",
                               "zh": "Kokoro 只有内置音色。想用你自己的声音，请切换到 VoxCPM2 或 IndexTTS。"},
    "studio.voices.select": {"en": "Select", "zh": "选择"},
    "studio.voices.select_all": {"en": "Select all", "zh": "全选"},
    "studio.voices.done": {"en": "Done", "zh": "完成"},
    "studio.voices.delete_picked": {"en": "Delete selected ({n})", "zh": "删除所选（{n}）"},
    "studio.voices.delete_many_title": {"en": "Delete {n} voices?", "zh": "删除 {n} 个声音？"},
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
        "en": "This voice has no transcript. Click ⋯ on the voice ▸ Edit what was said.",
        "zh": "这个声音缺少对应文字，请点声音右侧的 ⋯ ▸ 修改朗读内容。",
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
    "studio.speak.preparing": {"en": "Preparing the voice engine…", "zh": "正在准备语音引擎…"},
    "studio.speak.ready": {"en": "Voice engine ready", "zh": "语音引擎已就绪"},
    "studio.speak.ready_tip": {
        "en": "The voice engine is loaded — speech starts right away.",
        "zh": "语音引擎已加载，点击后立即开始生成。",
    },
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
    "studio.voices.preview": {"en": "Click ▶ on the right to hear this voice",
                              "zh": "点右侧 ▶ 试听这个声音"},
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
