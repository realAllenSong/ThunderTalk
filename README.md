<p align="center">
  <img src="assets/icon.png" width="80" alt="ThunderTalk Logo" />
</p>

<h1 align="center">ThunderTalk</h1>

<p align="center">
  Lightning-fast, privacy-first voice-to-text for every desktop.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS%20(Apple%20Silicon)-brightgreen" alt="Platform" />
  <img src="https://img.shields.io/badge/version-0.3.1-orange" alt="Version" />
</p>

https://github.com/user-attachments/assets/51be7955-ef63-40db-b3f0-5dbed0943a21

---

## Features

- **Press a key, speak, get text.** One hotkey activates voice input anywhere on your desktop.
- **100% local & private** — your voice never leaves your device. No cloud, no subscription.
- **Multiple ASR backends** — MLX (Metal GPU on Apple Silicon) and ONNX (CPU).
- **Multiple ASR models** — SenseVoice and Qwen3-ASR in various sizes.
- **Hotwords** — custom vocabulary for domain-specific terms.
- **Smart hardware detection** — identifies your CPU, RAM, and GPU to recommend the best model.
- **Speaker mute** — optionally mutes system audio during recording to avoid feedback.
- **English & 中文 UI** — switch the interface language in Settings (instant, no restart).

## Download

Download the latest **ThunderTalk.app** from [Releases](https://github.com/realAllenSong/ThunderTalk/releases).

> **macOS**: After downloading, move `ThunderTalk.app` to your Applications folder. On first launch, grant **Microphone** and **Accessibility** permissions when prompted.

## Supported Models

### Speech-to-text (ASR)

| Model | Size | Backend | Languages | Accuracy | Hotwords |
|-------|------|---------|-----------|----------|----------|
| SenseVoice-Small | 241 MB | ONNX (CPU) | 5 | ★★★☆☆ | No |
| Qwen3-ASR-0.6B | 940 MB | ONNX (CPU) | 52 | ★★★★★ | Yes |
| Qwen3-ASR-0.6B | ~1.2 GB | MLX (Metal GPU) | 52 | ★★★★★ | Yes |
| Qwen3-ASR-1.7B | ~3.4 GB | MLX (Metal GPU) | 52 | ★★★★★ | Yes |

### Translation (optional)

| Model | Size | Backend | Languages | Use case |
|-------|------|---------|-----------|----------|
| SeamlessM4T v2 Large | ~9 GB | PyTorch + MPS / CPU | 100+ | Speech & text translation in **Direct** and **Review** modes |

Models are downloaded on-demand from the app's **Models** page and stored at `~/.thundertalk/models/`.

## System Requirements

### Apple Silicon (recommended)

| Mac | RAM | Best ASR model | Translation? |
|-----|-----|---------------|--------------|
| M1 / M2 (8 GB) | 8 GB | Qwen3-ASR-0.6B (MLX fp16) | ❌ Not enough RAM for SeamlessM4T |
| M1 Pro / M2 Pro / M3 (16 GB) | 16 GB | Qwen3-ASR-0.6B or 1.7B (MLX) | ⚠️ Works, but tight — close other heavy apps |
| M1 Max / M2 Max / M3 Max (24+ GB) | 24+ GB | Qwen3-ASR-1.7B (MLX) | ✅ Comfortable |
| M3/M4 Ultra | 32+ GB | Anything | ✅ Headroom for batched workflows |

> **MLX = Metal GPU** on Apple Silicon. RTF (real-time factor) is typically ~0.05–0.1 on M-series chips, i.e. inference is 10–20× faster than the audio you spoke.

### Intel Mac / older hardware

CPU-only ONNX is the only path. Expect:

- **SenseVoice-Small** (241 MB) — works on any Mac from the last 5 years; transcription is fast but only 5 languages and no hotwords.
- **Qwen3-ASR-0.6B (ONNX int8)** — works on any Apple Silicon; ~RTF 0.3 on M3 Max CPU. Slower on Intel Macs.
- **No translation.** SeamlessM4T needs MPS or a discrete GPU; on Intel CPU it's too slow to be usable.

### Disk space

- **Minimum** to run the app at all: ~250 MB (SenseVoice-Small).
- **Recommended** if you want translation: ~12 GB free (Qwen3-ASR-0.6B + SeamlessM4T + working files).

The app's **Models** page shows your detected hardware and tags each model with **Recommended** / **Needs Apple Silicon** / **Needs MLX** so you don't have to memorize the table above.

## Choosing a Model

| Goal | Pick |
|------|------|
| Fastest transcription, fewest languages | **SenseVoice-Small** (5 langs, no hotwords) |
| Most accurate, every Mac | **Qwen3-ASR-0.6B (ONNX int8)** |
| Most accurate, Apple Silicon GPU | **Qwen3-ASR-0.6B (MLX fp16)** ← default |
| Highest accuracy on hard accents / noisy audio | **Qwen3-ASR-1.7B (MLX fp16)** — needs ≥16 GB RAM |
| Speech translation (e.g. talk in Chinese, paste English) | **Direct mode** — picks SeamlessM4T directly |
| Speak in your own language, see a translation alongside | **Review mode** — ASR transcribes, then translates; you choose Replace or Keep Original |

### Author's pick — what I actually use

- **Pure transcription:** **Qwen3-ASR-0.6B (ONNX int8) + Hotwords**.
  Fast, accurate, runs on every Mac (no GPU needed). The ONNX build
  is ~940 MB and starts in well under a second. Adding domain
  hotwords (`onnx`, `MLX`, your team's product names, etc.) on the
  Hotwords page keeps technical jargon from getting mis-transcribed.

- **Occasional translation:** **Review mode** with **Qwen3-ASR-0.6B
  (ONNX int8) + Hotwords** as the recognizer, plus **SeamlessM4T v2
  Large** as the translator. The ASR transcribes the original
  language perfectly, SeamlessM4T translates it via T2TT (text →
  text — no second audio pass, much faster than Direct mode), and
  the Review popup lets you accept or skip the translation per
  utterance. Best of both worlds: you keep the verbatim original
  if you want, and one click swaps it for the translation when you
  do.

  Direct mode skips the ASR step entirely (audio → translated text
  in one pass via SeamlessM4T). It's simpler but you lose the
  original transcript and can't choose model+hotwords for
  recognition, so I default to Review.

## Troubleshooting

### A model download was interrupted (network drop, app quit, machine slept)

The downloader writes to a `.tmp` file and renames atomically on completion, so a half-finished download is **not** mistaken for a usable model. To resume: open the **Models** page and click **Download** again. If you see the model already marked downloaded but it crashes on Activate, delete its folder under `~/.thundertalk/models/<model-id>/` and re-download.

### "Translation model not downloaded"

You picked **Direct** or **Review** mode but never downloaded SeamlessM4T (~9 GB). The Translation card on the Models page shows a **Download** button next to that message — click it and the app handles fetch + auto-load. Until it finishes, the Translation status row reads *Loading translation model…*.

### App opens to a blank window or a single solid color

You're hitting a Qt theme conflict (typically with a third-party global stylesheet). Quit the app, then launch from Terminal: `/Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk`. If the console prints `Could not parse stylesheet`, file an issue with the output. (If you've been running a `feat/*` branch from source and toggled vibrancy / NSVisualEffectView, switch back to `main` — those experiments aren't shipped.)

### History panel says 0 sessions but I had sessions yesterday

ThunderTalk reads `~/.thundertalk/history.json`. If a write was interrupted (force-quit during save, disk full), the file becomes unparsable. The current build no longer silently resets it — instead it renames the bad file to `history.broken-<timestamp>.json` next to it. Open that file in any text editor and the entries should still be readable; you can manually paste them back into a fresh `history.json` once you've fixed the JSON.

### Microphone permission is granted but the app says "no audio"

macOS sometimes ties the permission to a specific binary path. After a Gatekeeper-triggered re-quarantine (e.g. moving the app between folders), revoke and re-grant under **System Settings → Privacy & Security → Microphone**.

### Hotkey doesn't trigger recording

ThunderTalk needs **Accessibility** permission to read global key events. Open **System Settings → Privacy & Security → Accessibility**, toggle ThunderTalk off, then on again. Restart the app.

### My machine is below the table — what's the minimum?

Anything that can run macOS 12 (Monterey) or later, with at least 4 GB free RAM and 250 MB free disk, will run **SenseVoice-Small**. Translation is unrealistic below 16 GB RAM regardless of CPU/GPU.

## Usage

1. Click the **ThunderTalk** icon in the menu bar → **Open Settings** → download a model.
2. Press the hotkey (default: **Right ⌘**) to start recording. Press again to stop.
3. The transcribed text is automatically pasted into the active application.

> Change the hotkey, language (English / 中文), and more from the **Settings** page.

## Development

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/realAllenSong/ThunderTalk.git
cd ThunderTalk
uv sync

# (Apple Silicon) Install MLX backend for GPU acceleration
uv sync --extra mlx

# Run from source
uv run python run.py

# Build macOS .app
.venv/bin/python build_macos.py
# Output: dist/ThunderTalk.app
```

## Tech Stack

- **UI**: [PySide6](https://doc.qt.io/qtforpython-6/) (Qt6)
- **ASR**: [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (ONNX), [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) (MLX)
- **Audio**: [sounddevice](https://python-sounddevice.readthedocs.io/)
- **Hotkeys**: Native NSEvent (macOS)
- **Build**: [PyInstaller](https://pyinstaller.org/) + Apple Development code signing

## License

ThunderTalk is **source-available** under the
[PolyForm Noncommercial License 1.0.0](LICENSE).

- ✅ **Free for personal use** — individuals, hobbyists, students, researchers, and non-commercial use by charities, schools, and public-interest organizations.
- ✅ **Free to modify and share** for any noncommercial purpose.
- 💼 **Commercial use requires a separate license** from the author, granted at the author's discretion. This includes bundling into paid products, offering as a hosted service, or internal use by for-profit organizations.

For commercial licensing inquiries, contact **zysong@seas.upenn.edu**.

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the CLA that accompanies every pull request.

## Acknowledgments

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — cross-platform ASR inference
- [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) — MLX-native Qwen3 ASR
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — lightweight ASR model
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — state-of-the-art ASR
