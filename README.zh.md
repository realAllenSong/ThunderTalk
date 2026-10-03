<p align="center">
  <img src="assets/icon.png" width="80" alt="ThunderTalk Logo" />
</p>

<h1 align="center">ThunderTalk</h1>

<p align="center">
  免费开源的 macOS 语音输入应用。<br/>
  按下快捷键、开口说话，文字就出现在光标处。<br/>
  语音识别在你自己的 Mac 上完成：无需账号，无需订阅，没有云端。
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="https://realallensong.github.io/ThunderTalk/">官网</a> · <a href="https://github.com/realAllenSong/ThunderTalk/releases/latest">下载</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS-brightgreen" alt="Platform: macOS" />
  <a href="https://github.com/realAllenSong/ThunderTalk/releases/latest"><img src="https://img.shields.io/github/v/release/realAllenSong/ThunderTalk?color=orange" alt="最新版本" /></a>
  <a href="https://github.com/realAllenSong/ThunderTalk/releases"><img src="https://img.shields.io/github/downloads/realAllenSong/ThunderTalk/total?color=blue" alt="下载量" /></a>
</p>

<p align="center">
  <img src="assets/demo.gif" width="760" alt="ThunderTalk：把一句中英混说的话听写进邮件、转写三人对话并区分说话人、朗读一段文字" />
  <br/><sub>录音条和工作室画面取自 App 自身界面（邮件窗口是示意）。听写的句子、会议转写和朗读语音都是模型在 M3 Max 上的真实输出，只有镜头节奏是脚本控制的。</sub>
</p>

---

ThunderTalk 是一款 macOS 语音输入应用。在任意应用里按下快捷键，说出你想写的内容，文字就落在光标所在的位置。识别在你的本机完成：通过 MLX 使用 Apple Silicon 的 GPU，或通过 ONNX 使用 CPU，所以你的录音不会发往任何地方。它以 MIT 协议开源，是 **Typeless**、**Wispr Flow**、**superwhisper** 以及 macOS 自带听写的开源替代品。

界面刻意保持安静：暖色纸面背景、近黑的墨色、唯一一种橙色强调色，动效只用在有信息量的地方（实时音量条、加载圈、真实的下载进度）。

## 新功能：工作室（Studio）

工作室取代了原来的“实验室”页，是 1.5 版本的主打功能，分为两部分。

**转写。** 把音频或视频录音（m4a、mp3、wav、mp4、mov 等）拖进来，就能得到带时间戳的文字稿。多人对话请选择“多人对话”，MOSS-Transcribe-Diarize 会给每一轮发言标上说话人，点击说话人即可改名。可导出为 TXT、Markdown、SRT、VTT 或 JSON。解码使用 macOS 自带的工具，不需要安装 ffmpeg。

**朗读。** 把文字读出来，可在三个本地引擎之间选择：

- **VoxCPM2**（OpenBMB）：48 kHz 录音棚音质，支持声音克隆；在 Apple 芯片上约为实时速度。
- **IndexTTS-2.5**（bilibili）：克隆还原度很高，支持中、英、日、西、阿语；约为实时速度。
- **Kokoro**（8200 万参数）：小巧快速（CPU 上约为实时的 4 倍），内置 23 个精选音色（20 个中文、3 个英文）。
- VoxCPM2 与 IndexTTS 内置 18 个音色，都是短视频、Vlog、播客里常见的风格：影视解说、纪录片旁白、科普讲述、商业解读、新闻主播、霸道总裁、两种闺蜜分享、台湾腔，以及英文 YouTuber、播客和旁白。全部是“设计”出来的音色（由 VoxCPM2 根据文字描述生成，不是真人录音，也不模仿任何平台的音色），随应用一起提供。
- 每个音色都能试听：点音色上的 ▶，不用生成就能先听一听。
- 可调语速（0.75x 到 1.5x），带可拖动进度的播放器。结果可保存为 WAV（无损）或 M4A（体积小）。
- **我的声音：** 录制或导入 5 到 15 秒你自己的声音，就能用 VoxCPM2 或 IndexTTS 以你的声音朗读任何内容。参考音频保存在你 Mac 上的 `~/.thundertalk/voices`。请只克隆你有权使用的声音。

语音引擎只需下载一次，之后工作室里的一切都可以离线使用。

## 功能特性

**听写**

- 一个全局快捷键，任意应用都能用。默认是 Right ⌘；可以在“设置”里选择“切换”（按一下开始、再按一下结束）或“长按”，也可以更换按键或组合键。
- 多种语音模型：Qwen3-ASR 0.6B 与 1.7B、SenseVoice-Small、NVIDIA Parakeet-TDT、MOSS-Transcribe-Diarize。应用会读取你的硬件并推荐合适的模型。
- 中文、英文，以及同一句话里的中英混说。界面本身也有英文和中文两种语言。
- 热词：把总是听错的产品名、缩写、人名教给它（适用于 Qwen3-ASR 系列模型）。
- 逆文本规整：口述的数字会变成阿拉伯数字，中英文都支持（“twenty five”变成 25，“三百五十二”变成 352）。
- 可选的翻译，通过 SeamlessM4T v2 支持 100+ 种语言：“直译”模式边说边译，“审阅”模式先转写，再由你选择替换或保留原文。
- 可搜索的历史记录，保存为 `~/.thundertalk` 里的普通文件。
- **AI 整理与语音编辑：**使用已有且登录的 Codex、Claude Code、Gemini、Grok 或 Cursor CLI，已安装模型的 Ollama / LM Studio，Cherry Studio API 服务，或自定义 OpenAI 兼容 API。不内置或下载 AI 模型。在设置中启用、选择服务与模型，并为各应用选择风格或关闭。先立即粘贴原文；没有输入、点击、滚动或切换应用时才替换为整理结果。超时保留原文。终端没有标准的粘贴撤销，因此跳过替换。
- 中英双语整句语音命令：「换行 / new line」「新段落 / new paragraph」「删掉上一句 / delete that」（撤销最后一段未被操作的听写）及「制表符 / tab key」。开启 AI 整理后，录音前选中文字，再说「改得正式一点 / make this more formal」「简短一点 / make this shorter」或「翻译成英文 / translate to English」即可编辑选中内容。读取选区需要 macOS 辅助功能支持，完整剪贴板会保存并恢复。

**日常细节**

- 第一次运行有引导：欢迎、权限、选模型、试一试。
- 模型下载显示来自真实字节数的进度，网络中断后可以续传，“取消”立即生效。
- 应用内自动更新：有新版本发布时弹出一个小提示，点一下就会下载、替换并重启。
- 可选的录音时静音扬声器，避免麦克风拾取你自己的声音。
- 内存模式（“设置 → 性能”）：牺牲一些 KV 缓存与线程数，换取约 3 GB 的内存占用降低。

**隐私**

- 无账号、无订阅、无使用次数限制。
- 音频在你的 Mac 上识别，绝不上传。
- AI 整理默认关闭，此时网络用于下载模型及检查 GitHub Releases 更新。启用后，云端 CLI 使用你的账号，将听写/选中文字发送给对应服务。Ollama 和 LM Studio 在本机推理；Cherry Studio 与自定义 API 可能将文字转发给云端模型。
- 代码开源，可以阅读、自行构建、随意 fork。

## 下载

用 Homebrew：

```sh
brew install --cask realallensong/tap/thundertalk
```

或者从 [Releases](https://github.com/realAllenSong/ThunderTalk/releases/latest) 下载最新的 **ThunderTalk.app**，移到“应用程序”文件夹后打开。首次启动时按提示授予“麦克风”和“辅助功能”权限（“辅助功能”让 ThunderTalk 能替你把文字输入到光标处）。

ThunderTalk 使用 ad-hoc 签名而没有做公证（Apple Developer ID 每年 99 美元），所以从浏览器下载后首次打开时 macOS 会给出警告。请看下文 [首次打开提示“无法打开 / 无法验证开发者”](#首次打开提示无法打开--无法验证开发者)，大约十秒就能搞定。

## 使用方法

1. 打开 ThunderTalk，按首次运行引导操作，或进入“模型”页下载一个模型。
2. 点进任意应用里的任意输入框。
3. 按下快捷键（默认 **Right ⌘**），说话，再按一次结束，文字会粘贴到光标处。

在“设置”里可以修改快捷键、按键模式、麦克风和界面语言。打开“工作室”可以做文件转写和文字转语音。

## 支持的模型

### 语音识别（ASR）

| 模型 | 大小 | 后端 | 语言 | 准确度 | 热词 |
|------|------|------|------|--------|------|
| SenseVoice-Small | 241 MB | ONNX (CPU) | 5 | ★★★☆☆ | 否 |
| Qwen3-ASR-0.6B | 940 MB | ONNX (CPU) | 52 | ★★★★★ | 是 |
| Qwen3-ASR-0.6B | ~1.9 GB | MLX (Metal GPU) | 52 | ★★★★★ | 是 |
| Qwen3-ASR-1.7B | ~4.7 GB | MLX (Metal GPU) | 52 | ★★★★★ | 是 |
| MOSS-Transcribe-Diarize 0.9B | ~1.8 GB | MLX (Metal GPU) | 50+ | ★★★★★ | 否 |
| Parakeet-TDT 0.6B v3 | 640 MB | ONNX (CPU) | 25（欧洲语言） | ★★★★★ | 否 |
| Parakeet-TDT 0.6B v2 | 640 MB | ONNX (CPU) | 英语 | ★★★★★ | 否 |

> **MOSS-Transcribe-Diarize**（[OpenMOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)，INTERSPEECH 2026 第二届 MLC-SLM 挑战赛第一名）是多说话人模型。听写时它粘贴纯文本（模型卡片上有可选的 S01:/S02: 说话人标签开关），同时它也驱动工作室的“多人对话”模式，单次即可转写最长约 90 分钟的录音，并附说话人标签和时间戳。
>
> **Parakeet-TDT**（NVIDIA）以 CPU 运行，自带标点和大小写。在 M3 Max 上识别速度约为实时的 50 倍（RTF 0.019），约为 Qwen3-ASR ONNX 的 4 倍。

### 文字转语音（工作室）

| 模型 | 大小 | 后端 | 用途 |
|------|------|------|------|
| VoxCPM2（8-bit，Apache-2.0） | 3.2 GB | MLX (Metal GPU) | 内置音色、声音克隆 |
| IndexTTS-2.5（8-bit，bilibili 模型使用许可） | 1.7 GB + 2.3 GB 编码器 | MLX (Metal GPU) | 内置音色、声音克隆 |
| Kokoro v1.1 多语言版（Apache-2.0） | 364 MB | ONNX (CPU) | 23 种内置音色（20 种中文、3 种英文） |

都是一次性下载：在“朗读”里选中某个引擎的音色时，应用会提示下载。IndexTTS 使用打过补丁的 [mlx-indextts2](https://github.com/vanch007/mlx-indextts2) MLX 移植版（见 `third_party/README.md`）。

### 翻译

| 模型 | 大小 | 后端 | 语言 | 用途 |
|------|------|------|------|------|
| SeamlessM4T v2 Large | ~9 GB | PyTorch + MPS / CPU | 100+ | 在“直译”“审阅”模式下进行语音与文本翻译 |

翻译和 IndexTTS 共用一个按需下载的 **90 MB PyTorch 组件**。在应用内下载这两个引擎时，组件会安装到 `~/.thundertalk/runtime/`；出现提示后请重启一次。听写、工作室转写、VoxCPM2 和 Kokoro 无需该组件。MLX 语音也使用 Transformers 和 SciPy，因此这两个库仍内置。模型权重从“模型”页面或工作室引擎卡片另行下载，存放在 `~/.thundertalk/models/` 或 Hugging Face 缓存中。

## 系统要求

**需要 Apple Silicon（M1 或更新）的 Mac，以及 macOS 15（Sequoia）或更高版本。** 发布版只为 arm64 构建，内置的语音库分别需要 macOS 15（MLX）、14（ONNX Runtime）和 13（Qt）。发布版不支持 Intel Mac。

### 按内存建议的模型

| Mac | 内存 | 建议的识别模型 | 翻译 |
|-----|------|----------------|------|
| M1 / M2 | 8 GB | SenseVoice-Small 或 Qwen3-ASR-0.6B | 内存不够跑 SeamlessM4T |
| M1 Pro / M2 Pro / M3 | 16 GB | Qwen3-ASR-0.6B 或 1.7B | 可以，但请关掉其他重型应用 |
| Max / Ultra 芯片 | 24 GB 及以上 | 都可以 | 宽裕 |

目前只在 M3 Max 上实测过（所有模型用同一批录音）：

| 模型 | 英文词错率 | 中文字错率 | 速度 |
|------|----------:|----------:|-----:|
| Qwen3-ASR-0.6B（MLX，GPU） | 4.7% | 5.0% | 约实时的 11 倍 |
| Qwen3-ASR-0.6B（ONNX int8，CPU） | 5.5% | 4.5% | 约 12 倍 |
| Parakeet-TDT 0.6B v2（CPU） | 3.0% | 仅英文 | 约 50 倍 |
| Parakeet-TDT 0.6B v3（CPU） | 4.5% | 不支持中文 | 约 50 倍 |
| MOSS-Transcribe-Diarize（MLX，工作室） | 3.6% | 3.5% | 15–21 倍 |

芯片越小越慢。如果你有别的 Mac，`ThunderTalk --selftest` 的结果对大家都很有用，见 [#6](https://github.com/realAllenSong/ThunderTalk/issues/6)。

### 磁盘空间

- **App 本体：** 磁盘占用约 592 MiB，zip 下载约 224 MB（214 MiB）。实测已安装的 v1.6.4 为 961 MiB，zip 为 330 MB。PyTorch、torchaudio、SymPy、mpmath 和 NetworkX 改为翻译与 IndexTTS 共用的可选组件，下载约 90 MB。模型权重另计。
- **最低运行需求：** 约 1.1 GB（App + SenseVoice-Small）。
- **含翻译的推荐配置：** 约 13 GB 空闲空间（App + Qwen3-ASR-0.6B + SeamlessM4T + 工作文件）。

“模型”页面会显示检测到的硬件，并为每个模型标注“推荐”“需要 Apple Silicon”或“需要 MLX”，无需记忆上面的表格。

## 选择模型

| 目标 | 选择 |
|------|------|
| 启动最快、语言最少 | **SenseVoice-Small**（5 种语言，无热词） |
| CPU 上准确率最高 | **Qwen3-ASR-0.6B (ONNX int8)** |
| 准确率最高、Apple Silicon GPU | **Qwen3-ASR-0.6B (MLX fp16)**（默认） |
| 重口音或嘈杂音频 | **Qwen3-ASR-1.7B (MLX fp16)**，需要 16 GB 及以上内存 |
| 带说话人标签的会议 / 访谈 | **MOSS-Transcribe-Diarize 0.9B (MLX)**，仅 Apple Silicon |
| 最快的英语听写 | **Parakeet-TDT 0.6B v2 (ONNX int8)** |
| CPU 上的欧洲语言 | **Parakeet-TDT 0.6B v3 (ONNX int8)** |
| 说一种语言，粘贴另一种 | **直译模式**，直接使用 SeamlessM4T |
| 用母语说话，同时看到译文 | **审阅模式**：ASR 转写后再翻译，由你选择替换或保留原文 |

作者自己的配置：纯转写用 **Qwen3-ASR-0.6B (ONNX int8) + 热词**，又快又准，不需要 GPU；需要翻译时用**审阅模式**，以它作为识别引擎，搭配 **SeamlessM4T v2**。审阅模式翻译的是转写后的文本（文本到文本），比直译模式快，并且能保留原文。

## 自动更新

ThunderTalk 启动后不久会检查 GitHub Releases，发现新版本就弹出一个小提示。点“立即更新”后，应用会跳转到“关于”页，下载新版本的 zip，退出、替换 `/Applications/ThunderTalk.app`、移除 quarantine 属性，然后自动重启。点“以后再说”则本次启动内不再提示。你也可以在“关于 → 检查更新”里手动检查。

更新之后可能需要重新授予“辅助功能”和“麦克风”权限，见下文 [自动更新后快捷键 / 麦克风失灵](#自动更新后快捷键--麦克风失灵)。

## 故障排查

### 模型下载中断了

下载器先写入临时文件，完成后才重命名，所以未完成的下载不会被当成可用模型，续传也会从中断处继续。在“模型”页再点一次“下载”即可。如果模型已标记为已下载但激活时崩溃，请删除 `~/.thundertalk/models/<模型 ID>/` 后重新下载。

### “Translation model not downloaded”（翻译模型未下载）

你选了“直译”或“审阅”模式，但还没下载 SeamlessM4T（约 9 GB）。“模型”页的翻译卡片上有“下载”按钮，点击后应用会自动下载并加载。

### 工作室提示缺少模型或引擎

说话人模型和“朗读”所需的语音引擎都是一次性下载。工作室会在需要的地方显示大小和下载按钮。单人快速转写需要先加载一个听写模型：请到“模型”页选一个。

### 应用打开后是空白窗口或纯色

通常是 Qt 样式冲突。退出应用，从终端启动：`/Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk`。如果控制台打印 `Could not parse stylesheet`，请把输出贴到 issue 里。

### 历史页显示“0 次”，但昨天明明用过

ThunderTalk 读取 `~/.thundertalk/history.json`。如果某次写入被中断（保存时强制退出、磁盘满），文件可能损坏。应用不会静默清空它，而是把损坏文件改名为同目录下的 `history.broken-<时间戳>.json`。用文本编辑器打开它，修复 JSON 后把内容粘贴回新的 `history.json` 即可。

### 麦克风权限明明开了，应用还是说“no audio”

macOS 有时会把权限绑定到具体的二进制路径上，例如把 App 在文件夹之间移动之后。请在“系统设置 → 隐私与安全性 → 麦克风”里先移除再重新添加。

### 快捷键不触发录音

ThunderTalk 需要“辅助功能”权限来读取全局键盘事件。在“系统设置 → 隐私与安全性 → 辅助功能”里先关闭再打开 ThunderTalk，然后重启应用。

### 首次打开提示“无法打开 / 无法验证开发者”

因为是 ad-hoc 签名，Gatekeeper 会对浏览器下载的应用给出警告。允许打开的方式：

1. 把 `ThunderTalk.app` 拖进 `/Applications`。
2. 双击打开一次，macOS 会拒绝并弹出警告。
3. 打开“系统设置 → 隐私与安全性”，往下翻到底部，点“ThunderTalk 已被阻止使用”那一行旁边的“仍要打开”。
4. 在二次确认弹窗里再点“打开”。

或者用一条命令：

```bash
xattr -dr com.apple.quarantine /Applications/ThunderTalk.app
open /Applications/ThunderTalk.app
```

macOS 会记住你的选择。通过应用内更新升级会自动移除 quarantine 属性，所以只有最初那一次浏览器下载需要这个步骤。

### 自动更新后快捷键 / 麦克风失灵

ad-hoc 签名让每次构建的代码目录哈希都不同，而 macOS 把隐私权限绑定在这个哈希上。更新器替换 .app 之后，系统会把新的二进制当成“另一个 App”，旧的“辅助功能”授权悄悄失效，“麦克风”也得重新授予。ThunderTalk 会在更新后的第一次启动给出一次性提示。修复步骤：

1. 打开“系统设置 → 隐私与安全性 → 辅助功能”。
2. 删掉旧的 `ThunderTalk` 条目（开关亮着但灰着的那个，或路径已过期的）。
3. 点 `+`，选择 `/Applications/ThunderTalk.app`，重新添加。
4. 打开开关，再按一次快捷键。第一次录音时可能会再次询问麦克风权限，允许即可。

这是发布未公证应用的固有限制。用 Apple Developer ID 给每次发布签名并公证就能消除它，代价是每年 99 美元和多几步发布流程。

### 最低能跑什么配置？

任何装了 macOS 15 的 Apple Silicon Mac 应该都能跑最轻的 **SenseVoice-Small**（163 MB）。目前只在 M3 Max 上实测过，欢迎在 [#6](https://github.com/realAllenSong/ThunderTalk/issues/6) 分享 8 GB 机器的结果。内存低于 16 GB 时翻译不现实。

## 从源码构建

需要 macOS、Python 3.12 或更高版本，以及 [uv](https://docs.astral.sh/uv/)。

```bash
# 安装 uv（如果还没有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆并安装（Apple Silicon：MLX 后端 + 翻译引擎）
git clone https://github.com/realAllenSong/ThunderTalk.git
cd ThunderTalk
uv sync --extra mlx --extra translation --extra indextts

# 从源码运行
uv run python run.py

# 打包 macOS 应用（产物：dist/ThunderTalk.app）
.venv/bin/python build_macos.py
```

源码运行和发布构建请使用 `uv sync --extra mlx --extra translation --extra indextts`，保留完整开发依赖供 PyInstaller 分析。发布包会排除 PyTorch 及其专用依赖，并通过锁定的 wheel 按需安装。发布版的可选组件仅支持 Python 3.12 / Apple Silicon macOS。Intel Mac 源码运行路径未经测试。

可选组件直接从 PyPI 下载固定的 Python 3.12 arm64 wheel。`thundertalk/core/runtime_manifest.json` 保存从 `uv.lock` 提取的准确版本、URL、大小和 SHA-256：torch/torchaudio 2.11.0、SymPy 1.14.0、mpmath 1.3.0、NetworkX 3.6.1。用户无需安装 pip 或 Python。取消后可续传，校验失败不会发布组件。安装锁与原子目录重命名保护多个应用实例。应用启动时先激活组件，再导入 Transformers；在当前会话安装后，按提示重启。原生 dylib 保持 wheel 中的相对布局；发布签名须保留已有的 `disable-library-validation` entitlement。

无需新增 GitHub Release 附件，只需发布正常的 app zip。更新组件时，使用新的 runtime ID，从 `uv.lock` 更新 wheel 清单，重新构建并运行打包自检；不要原地修改同一个 ID 的版本。

```bash
PYTHONPATH=$PWD .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check thundertalk tests tools/runtime_hook.py
# 顺序运行打包自检；共享机器上使用机器级 GPU 锁。
APP=dist/ThunderTalk.app/Contents/MacOS/ThunderTalk
"$APP" --selftest audio
"$APP" --selftest tts --engine kokoro
"$APP" --selftest tts --engine voxcpm2
"$APP" --selftest moss --file sample.m4a
"$APP" --selftest asr --file sample.m4a
# 使用与 UI 相同的代码下载并验证可选组件。
"$APP" --selftest runtime
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$APP" --selftest tts --engine indextts
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$APP" --selftest translate
```

前几项检查应在未安装可选组件、已下载模型权重时运行。runtime 检查下载约 90 MB；最后两项另需模型权重。translate 使用内置参考音频检查文本与语音生成。这是运行时冒烟检查，不断言翻译质量：在锁定的依赖版本下，目标设为 cmn 时，未修改的 v1.6.4 源码引擎和缩小后的应用都将英文片段输出为英文；文本翻译正确输出中文。这个已有的语音目标语言问题需要单独修复。

欢迎贡献代码，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 技术栈

- **UI：** [PySide6](https://doc.qt.io/qtforpython-6/)（Qt 6），颜色、字体和间距集中定义在一个主题模块里
- **语音识别：** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（ONNX）、[mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) 与 [mlx-audio](https://github.com/Blaizzy/mlx-audio)（MLX）
- **文字转语音：** VoxCPM2（mlx-audio）、IndexTTS-2.5（内置的 MLX 移植版）、Kokoro（sherpa-onnx），全部在进程内运行
- **翻译：** 通过 PyTorch 与 Transformers 运行的 SeamlessM4T v2
- **音频：** 采集用 [sounddevice](https://python-sounddevice.readthedocs.io/)，文件解码用 macOS 自带的 `afconvert`
- **快捷键：** macOS 原生 NSEvent
- **打包：** [PyInstaller](https://pyinstaller.org/)，ad-hoc 签名

## 常见问题

**ThunderTalk 收费吗？** 不收费。MIT 协议开源，无账号、无订阅、无使用次数限制。

**能离线使用吗？** 可以。模型下载完成后，识别、翻译、工作室和文字转语音都在你的 Mac 上运行，无需联网。

**和 Typeless、Wispr Flow、superwhisper 有什么区别？** 它们是付费闭源应用，通常在云端处理音频。ThunderTalk 免费、开源、完全本地，你可以阅读代码，语音数据也不会离开你的电脑。

**支持中文和中英混说吗？** 支持。Qwen3-ASR 支持 52 种语言，包括同一句话里中英混说，界面也有中文和英文两种语言。

**Intel Mac 能用吗？** 发布版不行：它只为 Apple Silicon 构建。在 Intel 上从源码运行 CPU（ONNX）模型没有测试过。

**可以在哪些应用里听写？** 任何有文字光标的应用：浏览器、编辑器、Slack、邮件、终端。光标在哪里，文字就粘贴到哪里。

## 许可证

ThunderTalk 基于 [MIT License](LICENSE) 开源。随意使用、fork、集成到你自己的产品里。给个 Star 或提个 PR 就是对项目最好的支持。

## 致谢

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)：跨平台 ASR 推理
- [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr)：MLX 原生的 Qwen3 ASR
- [mlx-audio](https://github.com/Blaizzy/mlx-audio)：MOSS-Transcribe-Diarize 与 VoxCPM2 的 MLX 推理
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice)：轻量级 ASR 模型
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)：业界领先的 ASR 模型
- [OpenMOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize)：MOSS-Transcribe-Diarize
