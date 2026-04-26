<p align="center">
  <img src="assets/icon.png" width="80" alt="ThunderTalk Logo" />
</p>

<h1 align="center">ThunderTalk</h1>

<p align="center">
  极速、隐私优先的桌面语音转文字工具。
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-brightgreen" alt="Platform" />
  <img src="https://img.shields.io/badge/version-1.1.6-orange" alt="Version" />
</p>

https://github.com/user-attachments/assets/51be7955-ef63-40db-b3f0-5dbed0943a21

---

## 功能特性

- **按一下快捷键，开口说话，文字直出。** 一个全局快捷键即可在任何应用中输入语音。
- **100% 本地、完全私密** — 录音永不离开设备，无云端、无订阅。
- **多种 ASR 后端** — 支持 MLX（Apple Silicon Metal GPU）和 ONNX（CPU）。
- **多种 ASR 模型** — 支持 SenseVoice 与 Qwen3-ASR 的多个尺寸。
- **热词** — 自定义词汇表，专业术语再也不会识别错。
- **智能硬件检测** — 自动识别 CPU / 内存 / GPU 并推荐最优模型。
- **录音时静音扬声器** — 防止扬声器声被麦克风拾取造成回声。
- **中英双语界面** — 在「设置」中即时切换，无需重启。

## 下载

从 [Releases](https://github.com/realAllenSong/ThunderTalk/releases) 下载最新的 **ThunderTalk.app**。

> **macOS：** 下载后将 `ThunderTalk.app` 移到「应用程序」文件夹。首次启动时按提示授予「麦克风」与「辅助功能」权限。

## 支持的模型

### 语音识别（ASR）

| 模型 | 大小 | 后端 | 语言 | 准确度 | 热词 |
|------|------|------|------|--------|------|
| SenseVoice-Small | 241 MB | ONNX (CPU) | 5 | ★★★☆☆ | 否 |
| Qwen3-ASR-0.6B | 940 MB | ONNX (CPU) | 52 | ★★★★★ | 是 |
| Qwen3-ASR-0.6B | ~1.2 GB | MLX (Metal GPU) | 52 | ★★★★★ | 是 |
| Qwen3-ASR-1.7B | ~3.4 GB | MLX (Metal GPU) | 52 | ★★★★★ | 是 |

### 翻译（可选）

| 模型 | 大小 | 后端 | 语言 | 用途 |
|------|------|------|------|------|
| SeamlessM4T v2 Large | ~9 GB | PyTorch + MPS / CPU | 100+ | 在「直译」「审阅」模式下进行语音与文本翻译 |

模型按需在「模型」页面下载，存储路径为 `~/.thundertalk/models/`。

## 系统要求

### Apple Silicon（推荐）

| 设备 | 内存 | 推荐 ASR 模型 | 是否支持翻译？ |
|------|------|---------------|----------------|
| M1 / M2（8 GB） | 8 GB | Qwen3-ASR-0.6B (MLX fp16) | ❌ 内存不够 SeamlessM4T 跑 |
| M1 Pro / M2 Pro / M3（16 GB） | 16 GB | Qwen3-ASR-0.6B 或 1.7B (MLX) | ⚠️ 能跑但偏紧，建议关闭其他大型应用 |
| M1 Max / M2 Max / M3 Max（24+ GB） | 24+ GB | Qwen3-ASR-1.7B (MLX) | ✅ 宽裕 |
| M3/M4 Ultra | 32+ GB | 任意 | ✅ 可同时跑批量任务 |

> **MLX = Metal GPU 加速。** 在 M 系列芯片上 RTF（实时倍率）通常在 0.05–0.1 之间——也就是说推理速度比说话本身快 10–20 倍。

### Intel Mac / 老硬件

只能走纯 CPU 的 ONNX 后端：

- **SenseVoice-Small**（241 MB）— 近 5 年的 Mac 都能跑；速度快但只支持 5 种语言、无热词。
- **Qwen3-ASR-0.6B (ONNX int8)** — 任意 Apple Silicon 都能跑；M3 Max CPU 下 RTF ≈ 0.3，Intel Mac 慢一些。
- **不支持翻译。** SeamlessM4T 需要 MPS 或独立 GPU；Intel CPU 上跑不动。

### 磁盘空间

- **最低**（仅运行）：约 250 MB（SenseVoice-Small）。
- **推荐**（含翻译）：约 12 GB 空闲（Qwen3-ASR-0.6B + SeamlessM4T + 工作文件）。

「模型」页面会显示检测到的硬件，并为每个模型标注「推荐」/「需要 Apple Silicon」/「需要 MLX」，无需记忆上表。

## 选择模型

| 目标 | 选择 |
|------|------|
| 转录速度最快、语言最少 | **SenseVoice-Small**（5 种语言，无热词） |
| 准确率最高、任意 Mac | **Qwen3-ASR-0.6B (ONNX int8)** |
| 准确率最高、Apple Silicon GPU | **Qwen3-ASR-0.6B (MLX fp16)** ← 默认 |
| 处理重口音 / 嘈杂音频 | **Qwen3-ASR-1.7B (MLX fp16)** — 需 ≥16 GB 内存 |
| 语音翻译（如说中文，粘贴英文） | **直译模式** — 直接用 SeamlessM4T |
| 用母语说话同时看到译文 | **审阅模式** — ASR 转录后再翻译，弹窗让你选「替换」或「保留原文」 |

### 作者推荐——我自己平时这么用

- **纯转录：** **Qwen3-ASR-0.6B (ONNX int8) + 热词。**
  快、准，任意 Mac 都跑得动（无需 GPU 加速）。ONNX 版本约 940 MB，启动不到一秒。在「热词」页加入领域词汇（例如 `onnx`、`MLX`、团队产品名等），技术术语就不会再被识别错。

- **偶尔翻译：** **审阅模式** + **Qwen3-ASR-0.6B (ONNX int8) + 热词** 作为识别引擎，配 **SeamlessM4T v2 Large** 作为翻译引擎。
  ASR 完美转录原文，SeamlessM4T 走 T2TT（文本→文本，不再过一次音频，比直译模式快得多），审阅弹窗让你逐句决定是否替换。两全其美：原文随时可保留，需要译文时一键替换。

  直译模式跳过 ASR（音频→译文一步到位，全靠 SeamlessM4T），更简单但失去原始转录文本，也无法配合热词。所以我默认用审阅模式。

## 故障排查

### 模型下载中断了（断网、应用退出、电脑休眠）

下载器写入 `.tmp` 临时文件并在完成时原子重命名，所以**未完成的下载不会被错认成可用模型**。恢复方法：在「模型」页点「下载」即可。如果模型已标记为已下载但激活时崩溃，请删除 `~/.thundertalk/models/<模型 ID>/` 文件夹后重新下载。

### "Translation model not downloaded"（翻译模型未下载）

你选了「直译」或「审阅」模式但还没下载 SeamlessM4T（约 9 GB）。「模型」页面的 Translation 卡片旁边会出现「下载」按钮——点击后会自动完成下载与加载。下载期间状态条显示「正在加载翻译模型…」。

### 应用打开后是空白窗口或纯色

通常是 Qt 主题冲突（多见于第三方全局样式）。退出应用，从终端启动看输出：`/Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk`。如果控制台打印 `Could not parse stylesheet`，请将完整输出提交 issue。

### 历史面板显示「0 次会话」，但昨天明明用过

ThunderTalk 读取 `~/.thundertalk/history.json`。如果某次写入被中断（强制退出、磁盘满），文件可能损坏。当前版本不再静默清空——它会把损坏文件改名为同目录下的 `history.broken-<时间戳>.json`。用任意文本编辑器打开那个文件应能看到原始记录，修复 JSON 后粘贴回新的 `history.json` 即可恢复。

### 麦克风权限明明开了，应用还是说"no audio"

macOS 有时会把权限绑定到具体的二进制路径上。如果应用经历过 Gatekeeper 重新隔离（例如把 .app 在文件夹间移动），请在「系统设置 → 隐私与安全性 → 麦克风」中先撤销再重新授予。

### 快捷键不触发录音

ThunderTalk 需要「辅助功能」权限来读取全局键盘事件。在「系统设置 → 隐私与安全性 → 辅助功能」中先关闭再打开 ThunderTalk 的开关，然后重启应用。

### 从 Releases 下载首次打开提示「无法验证开发者 / 移到废纸篓」

ThunderTalk 使用 ad-hoc 签名（没有花 $99/年办 Apple Developer Program），所以从浏览器下载后首次打开会被 Gatekeeper 拦截。允许打开的方式：

1. 把 `ThunderTalk.app` 拖进 `/Applications`，跟普通应用一样。
2. 双击打开一次，macOS 会拒绝并弹出警告。
3. 打开「**系统设置 → 隐私与安全性**」，往下翻到底部，找到「*ThunderTalk 已被阻止使用*」那一行，点旁边的「**仍要打开**」。
4. 在二次确认弹窗里再点「打开」。

或者一行 Terminal 命令搞定：

```bash
xattr -dr com.apple.quarantine /Applications/ThunderTalk.app
open /Applications/ThunderTalk.app
```

之后再启动就不会再问了。从 ThunderTalk *内部*的自动更新（v1.1.0 起支持）会自动剥掉 quarantine 属性——只有最初一次浏览器下载需要这个步骤。

### 自动更新后快捷键 / 麦克风失灵

ad-hoc 签名导致每次构建的 cdhash 都不一样，而 macOS 的 TCC 权限数据库把权限绑死在这个 hash 上。所以即使 bundle ID 没变，自动更新替换 .app 之后系统认定那是「另一个 App」，旧版的「**辅助功能**」授权悄悄失效，「**麦克风**」也得在下一次录音时重新授予。

ThunderTalk 在更新后第一次启动会弹一个一次性提示说明这件事。修复步骤：

1. 打开「**系统设置 → 隐私与安全性 → 辅助功能**」。
2. 删掉旧的 `ThunderTalk` 条目（开关亮着但灰着的那个，或者路径已经过期的）。
3. 点 `+`，定位到 `/Applications/ThunderTalk.app`，重新加进来。
4. 把开关打开。
5. 再按一次快捷键。第一次录音时会再次询问麦克风权限，允许即可。

这是非公证 App 在现代 macOS 上的根本性限制。买了 Apple Developer ID 之后给每次发布做代码签名 + 公证就能彻底解决（cdhash 在不同版本间会变，但 team identifier 保持稳定，TCC 就能保留授权）——代价是每年 $99 和多几步发布流程。

### 我的设备规格低于上表，最低能跑什么？

任何能运行 macOS 12 (Monterey) 及以上、有至少 4 GB 空闲内存、250 MB 磁盘空间的 Mac 都能跑 **SenseVoice-Small**。**翻译功能在内存低于 16 GB 的设备上无论 CPU/GPU 都不现实。**

## 使用方法

1. 点击菜单栏的 **ThunderTalk** 图标 → **打开设置** → 下载一个模型。
2. 按下快捷键（默认：**Right ⌘**）开始录音，再按一下停止。
3. 转录的文本会自动粘贴到当前应用。

> 在「设置」页可以修改快捷键、界面语言（中文 / English）等。

## 开发

```bash
# 安装 uv（如果还没有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆并安装
git clone https://github.com/realAllenSong/ThunderTalk.git
cd ThunderTalk
uv sync

# (Apple Silicon) 安装 MLX 后端以使用 GPU 加速
uv sync --extra mlx

# 从源码运行
uv run python run.py

# 打包 macOS .app
.venv/bin/python build_macos.py
# 产物位于：dist/ThunderTalk.app
```

## 技术栈

- **UI：** [PySide6](https://doc.qt.io/qtforpython-6/) (Qt6)
- **ASR：** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (ONNX)、[mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) (MLX)
- **音频：** [sounddevice](https://python-sounddevice.readthedocs.io/)
- **快捷键：** macOS 原生 NSEvent
- **打包：** [PyInstaller](https://pyinstaller.org/) + Apple Development 代码签名

## 许可证

ThunderTalk 是 **源码可见**（source-available）软件，遵循 [PolyForm Noncommercial License 1.0.0](LICENSE)。

- ✅ **个人非商业使用免费** — 个人、爱好者、学生、研究人员，以及慈善机构、学校、公益组织等的非商业使用均可免费。
- ✅ **可自由修改与分享**，前提是用于非商业目的。
- 💼 **商业使用需要单独授权**，由作者酌情授予。这包括捆绑入付费产品、提供托管服务、营利组织内部使用等。

商业授权请联系 **zysong@seas.upenn.edu**。

欢迎贡献代码——每个 PR 需附带 [CONTRIBUTING.md](CONTRIBUTING.md) 中的 CLA。

## 致谢

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — 跨平台 ASR 推理
- [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) — MLX 原生的 Qwen3 ASR
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — 轻量级 ASR 模型
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — 业界领先的 ASR 模型
