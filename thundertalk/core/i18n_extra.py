"""UI v2 strings (sidebar status, home hero, onboarding, toasts, overlay).

Merged into ``i18n._STRINGS`` at import time so ``t("key")`` finds them.
Keep {placeholders} identical across languages.
"""

from __future__ import annotations

EXTRA: dict[str, dict[str, str]] = {
    "runtime.required": {"en": "PyTorch component download needed ({size} MB), shared by translation and IndexTTS. Restart after installation.", "zh": "需要下载 PyTorch 组件（{size} MB），翻译与 IndexTTS 共用。安装后请重启。"},
    "runtime.restart": {"en": "Component installed. Restart ThunderTalk to enable translation and IndexTTS.", "zh": "组件已安装。请重启 ThunderTalk 以启用翻译和 IndexTTS。"},
    "runtime.restart_short": {"en": "Restart needed", "zh": "需要重启"},
    "runtime.ready": {"en": "Component ready", "zh": "组件已就绪"},
    "runtime.unsupported": {"en": "This component requires Python 3.12 on Apple Silicon macOS.", "zh": "此组件需要 Apple Silicon macOS 和 Python 3.12。"},
    "runtime.verifying": {"en": "Verifying and installing {name}…", "zh": "正在验证并安装 {name}…"},
    "runtime.integrity": {"en": "Component checksum failed. Please download again.", "zh": "组件校验失败，请重新下载。"},
    # ── Sidebar status card ─────────────────────────────────────────
    "status.ready": {"en": "Ready", "zh": "已就绪"},
    "status.setup": {"en": "Setup needed", "zh": "需要设置"},
    "status.loading": {"en": "Loading model…", "zh": "正在加载模型…"},
    "status.error": {"en": "Model failed to load", "zh": "模型加载失败"},
    "status.listening": {"en": "Listening…", "zh": "正在聆听…"},
    "status.transcribing": {"en": "Transcribing…", "zh": "正在转写…"},
    "status.no_model": {"en": "No model installed", "zh": "尚未安装模型"},
    "status.to_dictate": {"en": "to dictate", "zh": "开始听写"},
    "status.tap_to_fix": {"en": "Click to choose a model", "zh": "点击选择模型"},

    # ── Home hero ───────────────────────────────────────────────────
    "home.hero.ready.title": {"en": "Ready to dictate", "zh": "随时可以听写"},
    "home.hero.ready.sub": {
        "en": "Press {key} in any app, speak, then press it again. Your words land right at the cursor.",
        "zh": "在任意应用里按 {key}，说话，再按一次结束。文字会直接出现在光标处。",
    },
    "home.hero.nomodel.title": {"en": "Download a model to get started", "zh": "下载一个模型，马上开始"},
    "home.hero.nomodel.sub": {
        "en": "ThunderTalk runs 100% on your device — your voice never leaves it. Pick a speech model; it takes about a minute.",
        "zh": "ThunderTalk 完全在本机运行，声音不会离开你的设备。选一个语音模型，大约一分钟就绪。",
    },
    "home.hero.nomodel.cta": {"en": "Choose a model", "zh": "选择模型"},
    "home.hero.loading.title": {"en": "Warming up your model…", "zh": "正在预热模型…"},
    "home.hero.loading.sub": {
        "en": "First load can take a few seconds. You can dictate as soon as this turns green.",
        "zh": "首次加载可能需要几秒。状态变绿后即可使用。",
    },
    "home.hero.error.title": {"en": "Couldn't load the model", "zh": "无法加载模型"},
    "home.hero.error.cta": {"en": "Open Models", "zh": "打开模型页"},
    "home.hero.rec.title": {"en": "Listening…", "zh": "正在聆听…"},
    "home.hero.rec.sub": {"en": "Press {key} again to finish.", "zh": "再按一次 {key} 结束。"},
    "home.hero.trans.title": {"en": "Transcribing…", "zh": "正在转写…"},
    "home.hero.trans.sub": {"en": "Almost there.", "zh": "马上就好。"},
    "home.hero.try": {"en": "Try it in the box below", "zh": "在下方输入框里试一试"},

    # ── Home permissions banner ─────────────────────────────────────
    "home.perm.mic": {
        "en": "Microphone access is off — ThunderTalk can't hear you.",
        "zh": "麦克风权限未开启，ThunderTalk 听不到你的声音。",
    },
    "home.perm.mic_prompt": {
        "en": "ThunderTalk needs microphone access to hear you.",
        "zh": "ThunderTalk 需要麦克风权限才能听到你的声音。",
    },
    "home.perm.acc": {
        "en": "Accessibility access is off — ThunderTalk can't type text for you.",
        "zh": "辅助功能权限未开启，ThunderTalk 无法替你输入文字。",
    },
    "home.perm.fix": {"en": "Fix", "zh": "去开启"},

    # ── Home stats / history ────────────────────────────────────────
    "home.words": {"en": "Words dictated", "zh": "已听写字数"},
    "home.time_saved": {"en": "Est. time saved", "zh": "预计节省时间"},
    "home.time_saved.tip": {
        "en": "Compared with typing the same text at 40 words per minute.",
        "zh": "与以每分钟 40 字的速度打字相比。",
    },
    "home.search": {"en": "Search transcripts", "zh": "搜索转写记录"},
    "home.no_results": {"en": "No transcripts match “{q}”", "zh": "没有匹配“{q}”的记录"},
    "home.delete": {"en": "Delete", "zh": "删除"},
    "home.delete.confirm_title": {"en": "Delete this transcript?", "zh": "删除这条转写记录？"},
    "home.delete.confirm_body": {
        "en": "It will be removed from your history. This can't be undone from the app.",
        "zh": "它将从历史记录中移除，且无法在应用内恢复。",
    },
    "home.deleted": {"en": "Transcript deleted", "zh": "已删除该记录"},
    "home.copied_toast": {"en": "Copied to clipboard", "zh": "已复制到剪贴板"},
    "home.empty.title": {"en": "Your transcripts will appear here", "zh": "你的转写记录会显示在这里"},
    "home.empty.sub": {
        "en": "Nothing yet — say something and it shows up in seconds.",
        "zh": "还没有记录——说点什么，几秒后就会出现在这里。",
    },
    "home.show_more": {"en": "Show more", "zh": "展开"},
    "home.show_less": {"en": "Show less", "zh": "收起"},
    "home.min_short": {"en": "{m}m", "zh": "{m} 分钟"},
    "home.sec_short": {"en": "{s}s", "zh": "{s} 秒"},
    "home.hr_min_short": {"en": "{h}h {m}m", "zh": "{h} 小时 {m} 分"},

    # ── Toasts ──────────────────────────────────────────────────────
    "toast.model_ready": {"en": "{name} is ready", "zh": "{name} 已就绪"},
    "toast.model_failed": {"en": "Couldn't load the model", "zh": "模型加载失败"},
    "toast.download_failed": {"en": "Download failed", "zh": "下载失败"},
    "toast.hotkey_saved": {"en": "Hotkey set to {key}", "zh": "快捷键已设为 {key}"},
    "toast.hotword_added": {"en": "Added “{w}”", "zh": "已添加“{w}”"},
    "toast.hotword_learned": {"en": "Learned a new word: “{w}”", "zh": "学会了新词：“{w}”"},
    "toast.setup_done": {"en": "You're all set — happy dictating!", "zh": "全部就绪，尽情听写吧！"},

    # ── Overlay (was hard-coded English) ────────────────────────────
    "overlay.no_speech": {"en": "No speech detected", "zh": "没有检测到语音"},
    "overlay.too_short": {"en": "Too short", "zh": "录音太短"},
    "overlay.load_model": {"en": "Load a model first", "zh": "请先加载模型"},
    "overlay.no_model": {"en": "No model loaded", "zh": "尚未加载模型"},
    "overlay.mic_unavailable": {"en": "Mic unavailable", "zh": "麦克风不可用"},
    "overlay.no_translator": {"en": "Translation model not loaded", "zh": "翻译模型尚未加载"},
    "overlay.press_to_stop": {"en": "{key} to finish", "zh": "按 {key} 结束"},
    "overlay.done": {"en": "Pasted", "zh": "已粘贴"},
    "overlay.waiting_studio": {"en": "Waiting for Studio…", "zh": "正在等待工作室…"},
    "overlay.mic_denied": {
        "en": "Microphone access is off — see Home",
        "zh": "麦克风权限未开启，请查看主页",
    },

    # ── Models page ─────────────────────────────────────────────────
    "models.subtitle": {
        "en": "Pick the speech model that fits your machine. Everything runs locally.",
        "zh": "选择适合你设备的语音模型。所有识别都在本机完成。",
    },
    "models.best_for_you": {"en": "Best for you", "zh": "为你推荐"},
    "models.format_one": {"en": "1 format", "zh": "1 种格式"},
    "models.formats": {"en": "{n} formats", "zh": "{n} 种格式"},
    "models.hw.ram": {"en": "{gb} GB memory", "zh": "{gb} GB 内存"},
    "models.hw.mlx_ready": {"en": "Metal / MLX ready", "zh": "支持 Metal / MLX"},
    "models.hw.cpu_mode": {"en": "CPU mode", "zh": "CPU 模式"},
    "models.hw.nvidia": {"en": "NVIDIA GPU", "zh": "NVIDIA GPU"},
    "models.cancel": {"en": "Cancel", "zh": "取消"},
    "models.cancelling": {"en": "Cancelling…", "zh": "正在取消…"},
    "models.downloading_pct": {"en": "Downloading… {pct}%", "zh": "下载中… {pct}%"},
    "models.connecting": {"en": "Connecting…", "zh": "正在连接…"},
    "models.extracting": {"en": "Extracting…", "zh": "正在解压…"},
    "models.downloaded": {"en": "{name} downloaded", "zh": "{name} 下载完成"},
    "models.download_failed": {"en": "Download failed: {err}", "zh": "下载失败：{err}"},
    "models.dismiss": {"en": "Dismiss", "zh": "关闭"},
    "models.big_download_title": {"en": "Large download", "zh": "较大的下载"},
    "models.big_download_body": {
        "en": "{name} is about {size}. Keep ThunderTalk open until it finishes — you can cancel any time.",
        "zh": "{name} 约 {size}。下载期间请保持 ThunderTalk 运行，随时可以取消。",
    },
    "models.big_download_go": {"en": "Download", "zh": "开始下载"},

    "model.blurb.qwen3-asr-06b-mlx": {
        "en": "30 languages and 22 Chinese dialects.",
        "zh": "支持 30 种语言及 22 种中文方言。",
    },
    "model.blurb.qwen3-asr-06b-int8": {
        "en": "Runs on the CPU, at about the same speed as the GPU build.",
        "zh": "在 CPU 上运行，速度与 GPU 版相当。",
    },
    "model.blurb.qwen3-asr-17b-mlx": {
        "en": "Higher accuracy. Needs about 5 GB of free memory.",
        "zh": "精度更高，需要约 5 GB 可用内存。",
    },
    "model.blurb.parakeet-tdt-06b-v3-int8": {
        "en": "25 European languages, punctuation and casing built in.",
        "zh": "支持 25 种欧洲语言，自带标点与大小写。",
    },
    "model.blurb.parakeet-tdt-06b-v2-int8": {
        "en": "English only — the fastest and most accurate English model here.",
        "zh": "仅英文——这里速度最快、英文最准的模型。",
    },
    "model.blurb.sensevoice-small-int8": {
        "en": "Tiny and fast: Chinese, English, Japanese, Korean, Cantonese.",
        "zh": "小巧快速：中、英、日、韩、粤语。",
    },
    "model.blurb.moss-transcribe-diarize-mlx": {
        "en": "Multi-speaker transcription with speaker labels (see Studio).",
        "zh": "多人对话转写并标注说话人（见工作室）。",
    },
    "model.blurb.seamless-m4t-v2-large": {
        "en": "100+ source speech languages; translation targets vary by language.",
        "zh": "支持 100 多种源语音语言；翻译目标的支持范围有所不同。",
    },

    # ── Settings page ───────────────────────────────────────────────
    "settings.subtitle": {
        "en": "Tune how ThunderTalk listens, types and behaves.",
        "zh": "调整 ThunderTalk 如何聆听、输入与运行。",
    },
    "settings.hotkey.needs_modifier": {
        "en": "Add ⌘, ⌥, ⌃ or ⇧ — a bare key would trigger while you type.",
        "zh": "请加上 ⌘、⌥、⌃ 或 ⇧——单独的按键会在你打字时误触发。",
    },
    "settings.mic.rescan": {"en": "Rescan devices", "zh": "重新扫描设备"},

    # ── Hotwords page ───────────────────────────────────────────────
    "hotwords.unsupported": {
        "en": "The active model ({name}) doesn't use hotwords. Switch to a Qwen3-ASR model on the Models page to apply them.",
        "zh": "当前模型（{name}）不使用热词。请在“模型”页切换到 Qwen3-ASR 模型后再生效。",
    },
    "hotwords.remove": {"en": "Remove", "zh": "移除"},
    "hotwords.add_hint2": {
        "en": "Press Enter to add. Separate several words with commas or new lines.",
        "zh": "按回车添加。多个词可用逗号或换行分隔。",
    },

    # ── Onboarding ──────────────────────────────────────────────────
    "keys.right": {"en": "Right", "zh": "右侧"},
    "onb.step": {"en": "Step {n} of {total}", "zh": "第 {n} 步，共 {total} 步"},
    "onb.next": {"en": "Continue", "zh": "继续"},
    "onb.back": {"en": "Back", "zh": "返回"},
    "onb.skip": {"en": "Skip setup", "zh": "跳过设置"},
    "onb.finish": {"en": "Finish", "zh": "完成"},
    "onb.continue_anyway": {"en": "Continue anyway", "zh": "仍然继续"},
    "onb.start": {"en": "Get started", "zh": "开始设置"},

    "onb.welcome.title": {"en": "Speak. It types.", "zh": "开口，即成文字。"},
    "onb.welcome.sub": {
        "en": "Voice-to-text for every app on your computer — private, instant, and free.",
        "zh": "适用于电脑上所有应用的语音转文字——私密、即时、免费。",
    },
    "onb.feat.private.title": {"en": "100% on-device", "zh": "完全本地运行"},
    "onb.feat.private.sub": {
        "en": "Your voice never leaves this computer.",
        "zh": "你的声音不会离开这台电脑。",
    },
    "onb.feat.fast.title": {"en": "Real-time", "zh": "实时转写"},
    "onb.feat.fast.sub": {
        "en": "Tuned for Apple Silicon (MLX) and regular CPUs.",
        "zh": "针对 Apple 芯片（MLX）和普通 CPU 优化。",
    },
    "onb.feat.anywhere.title": {"en": "Works everywhere", "zh": "随处可用"},
    "onb.feat.anywhere.sub": {
        "en": "Types straight into any text field, in any app.",
        "zh": "直接输入到任何应用的任何输入框。",
    },

    "onb.perm.title": {"en": "Two quick permissions", "zh": "两项权限"},
    "onb.perm.sub": {
        "en": "macOS asks for these so ThunderTalk can hear you and type for you.",
        "zh": "macOS 需要你授权，ThunderTalk 才能听到你并替你输入。",
    },
    "onb.perm.mic.title": {"en": "Microphone", "zh": "麦克风"},
    "onb.perm.mic.why": {
        "en": "To hear what you say. Audio is processed on your device.",
        "zh": "用来听你说话，音频只在本机处理。",
    },
    "onb.perm.acc.title": {"en": "Accessibility", "zh": "辅助功能"},
    "onb.perm.acc.why": {
        "en": "To notice your hotkey and paste text where your cursor is.",
        "zh": "用来监听快捷键，并把文字粘贴到光标所在处。",
    },
    "onb.perm.granted": {"en": "Granted", "zh": "已授权"},
    "onb.perm.allow": {"en": "Allow", "zh": "允许"},
    "onb.perm.open": {"en": "Open Settings", "zh": "打开系统设置"},
    "onb.perm.reset": {"en": "Reset and grant again", "zh": "重置并重新授权"},
    "onb.perm.reset_failed": {
        "en": "macOS could not reset this permission. Open System Settings and remove and re-add ThunderTalk.",
        "zh": "macOS 无法重置此权限。请在系统设置中移除 ThunderTalk 后重新添加。",
    },
    "onb.perm.hint": {
        "en": "System Settings → Privacy & Security → Microphone / Accessibility: turn on ThunderTalk. If it is missing from Accessibility, click + and choose ThunderTalk.app in Applications. If a switch is already on but access is denied, turn it off and on, then quit and reopen ThunderTalk.",
        "zh": "系统设置 → 隐私与安全性 → 麦克风 / 辅助功能：开启 ThunderTalk。辅助功能中没有此应用时，点击 +，选择“应用程序”中的 ThunderTalk.app。若开关已开启但仍无权限，请关闭后再开启，然后退出并重新打开 ThunderTalk。",
    },

    "onb.model.title": {"en": "Pick your voice model", "zh": "选择语音模型"},
    "onb.model.sub": {
        "en": "We picked the best fit for this device. You can switch any time in Models.",
        "zh": "已为这台设备选好最合适的模型，之后可随时在“模型”页更换。",
    },
    "onb.model.download": {"en": "Download {size}", "zh": "下载 {size}"},
    "onb.model.use": {"en": "Use this model", "zh": "使用此模型"},
    "onb.model.other": {"en": "Choose a different model…", "zh": "选择其他模型…"},
    "onb.model.download_complete": {"en": "Download complete", "zh": "下载完成"},
    "onb.model.ready": {"en": "Model ready", "zh": "模型已就绪"},
    "onb.model.loading": {"en": "Getting it ready…", "zh": "正在准备…"},
    "onb.model.failed": {
        "en": "Download interrupted. Check your connection, then Retry. Hugging Face keeps partial downloads for retry; archive downloads restart. Nothing is downloaded until you choose Retry.",
        "zh": "下载中断。请检查网络后点击“重试”。Hugging Face 下载会保留部分文件以便继续；压缩包下载会重新开始。只有点击“重试”才会再次下载。",
    },
    "onb.model.retry": {"en": "Retry", "zh": "重试"},


    "onb.start_download": {"en": "Download {size} & set up", "zh": "下载 {size} 并设置"},
    "onb.welcome.download": {
        "en": "Setup downloads {name} once. {detail} It can download while you grant permissions.",
        "zh": "设置时将一次性下载 {name}。{detail} 授权期间可同时下载。",
    },
    "onb.model.estimate": {
        "en": "About {size}; roughly {minutes}–{upper} min at 10 MB/s, depending on your connection.",
        "zh": "约 {size}；以 10 MB/秒下载需约 {minutes}–{upper} 分钟，实际取决于网络。",
    },
    "onb.model.cpu": {
        "en": "Runs on the CPU of any Mac: as accurate as the GPU version, faster, and half the download. 30 languages and 22 Chinese dialects. Switch any time in Models.",
        "zh": "在任何 Mac 的 CPU 上运行：准确率与 GPU 版相当，速度更快，下载量只有一半。支持中英等 30 种语言和 22 种中文方言，可随时在“模型”页更换。",
    },
    "onb.model.gpu": {
        "en": "Your Apple GPU and at least 16 GB of memory suit this fast 0.6B model. 30 languages and 22 Chinese dialects. Switch any time in Models.",
        "zh": "你的 Apple GPU 和至少 16 GB 内存适合这款快速的 0.6B 模型。支持中英等 30 种语言和 22 种中文方言，可随时在“模型”页更换。",
    },
    "onb.model.disk_full": {
        "en": "Not enough disk space. Free space on this Mac, then Retry. Allow extra space for temporary download files and extraction.",
        "zh": "磁盘空间不足。请释放此 Mac 的空间后重试，并为临时下载文件和解压预留额外空间。",
    },
    "onb.model.load_failed": {
        "en": "The model could not start. Close memory-heavy apps and Retry, or choose a different model. Downloaded files are kept.",
        "zh": "模型无法启动。请关闭占用大量内存的应用后重试，或选择其他模型。已下载的文件会保留。",
    },
    "onb.model.required": {"en": "Choose Download or Activate in Models to start dictating.", "zh": "请在“模型”页选择下载或启用，即可开始听写。"},
    "onb.perm.review": {"en": "Review permissions", "zh": "检查权限"},
    "onb.perm.review_hint": {"en": "Go Back to Permissions and enable Microphone and Accessibility before trying the hotkey.", "zh": "请返回“权限”，开启麦克风和辅助功能后再试快捷键。"},
    "onb.try.hold": {"en": "Hold {key}, say a sentence, then release. This box is already selected.", "zh": "按住 {key} 说一句话，再松开。输入框已选中。"},
    "onb.try.ready": {"en": "Ready — say a short sentence with your hotkey.", "zh": "已就绪，请按快捷键说一句简短的话。"},
    "onb.try.right_cmd": {"en": "The default Right ⌘ key is just to the right of Space.", "zh": "默认的右侧 ⌘ 键位于空格键右边。"},
    "onb.try.later": {"en": "Try later", "zh": "稍后试用"},

    "onb.try.title": {"en": "Try it out", "zh": "试一试"},
    "onb.try.sub": {
        "en": "Press {key}, say a sentence, then press {key} again. This box is already selected.",
        "zh": "按 {key} 说一句话，再按一次 {key}。输入框已选中。",
    },
    "onb.try.placeholder": {"en": "Your words will appear here…", "zh": "你说的话会出现在这里…"},
    "onb.try.success": {"en": "It works — that's all there is to it.", "zh": "成功了——就是这么简单。"},
    "onb.try.tip": {
        "en": "ThunderTalk keeps running in your menu bar. You can close this window any time.",
        "zh": "ThunderTalk 会继续在菜单栏中运行，可随时关闭此窗口。",
    },
    "onb.rerun": {"en": "Run setup again", "zh": "重新运行设置向导"},
    "onb.rerun.desc": {
        "en": "Walk through permissions, model and a test dictation again.",
        "zh": "重新走一遍权限、模型与试用流程。",
    },

    # ── Tray ────────────────────────────────────────────────────────
    "tray.status.ready": {"en": "Ready · {name}", "zh": "已就绪 · {name}"},
    "tray.status.setup": {"en": "Setup needed — open ThunderTalk", "zh": "需要设置——打开 ThunderTalk"},
    "tray.status.loading": {"en": "Loading model…", "zh": "正在加载模型…"},
    "tray.status.recording": {"en": "Listening…", "zh": "正在聆听…"},
    "tray.toggle": {"en": "Start / Stop Dictation", "zh": "开始 / 停止听写"},
    "tray.open_app": {"en": "Open ThunderTalk", "zh": "打开 ThunderTalk"},
}
