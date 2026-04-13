<p align="center">
  <img src="assets/logo-placeholder.svg" width="80" alt="ThunderTalk Logo" />
</p>

<h1 align="center">ThunderTalk</h1>

<p align="center">
  Lightning-fast, privacy-first voice-to-text for every desktop.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-brightgreen" alt="Platform" />
  <img src="https://img.shields.io/badge/version-0.1.0-orange" alt="Version" />
</p>

---

## Features

- **Press a key, speak, get text.** One hotkey activates voice input anywhere on your desktop.
- **100% local & private** — your voice never leaves your device. No cloud, no subscription.
- **Multiple ASR models** — choose from SenseVoice-Small, FunASR-Nano-MLT, or Qwen3-ASR.
- **Smart hardware detection** — identifies your CPU, RAM, and GPU to recommend the best model.
- **Microphone on-demand** — only acquired during recording, released immediately after.
- **Cross-platform** — macOS, Windows, and Linux.

## Quick Start

```bash
# 1. Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone and install
git clone https://github.com/songallen/ThunderTalk.git
cd ThunderTalk
uv sync

# 3. Run
uv run python run.py
```

On first launch, open **Settings** from the system tray to download a model. Then press **Option + Space** (macOS) or **Alt + Space** (Windows/Linux) to start voice input.

> **macOS users**: Grant Accessibility permission to your terminal in System Settings → Privacy & Security → Accessibility for global hotkeys to work.

## Supported Models

| Model | Size | Languages | Accuracy | Hotwords |
|-------|------|-----------|----------|----------|
| SenseVoice-Small | 241 MB | 5 | ★★★☆☆ | No |
| FunASR-Nano-MLT | ~2 GB | 31 | ★★★★☆ | Yes |
| Qwen3-ASR-0.6B | 940 MB | 52 | ★★★★★ | Yes |
| Qwen3-ASR-1.7B | ~3 GB | 52 | ★★★★★ | Coming Soon |

Models are downloaded on-demand and stored at `~/.thundertalk/models/`.

## Tech Stack

- **UI**: [PySide6](https://doc.qt.io/qtforpython-6/) (Qt6 for Python)
- **ASR engine**: [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) Python bindings (ONNX Runtime + llama.cpp)
- **Audio**: [sounddevice](https://python-sounddevice.readthedocs.io/)
- **Hotkeys**: [pynput](https://pynput.readthedocs.io/)
- **Package management**: [uv](https://docs.astral.sh/uv/)

## Project Structure

```
thundertalk/
├── app.py              # Application entry, pipeline orchestration
├── core/
│   ├── audio.py        # Microphone recording (sounddevice)
│   ├── asr.py          # ASR engine wrapper (sherpa-onnx)
│   ├── hotkey.py       # Global shortcut listener (pynput)
│   ├── text_output.py  # Paste text to active application
│   └── models.py       # Model registry, download, hardware detection
└── ui/
    ├── overlay.py       # Floating voice input capsule
    ├── main_window.py   # Settings / model management window
    └── tray.py          # System tray icon
```

## Contributing

We welcome contributions! Please read our [Contributing Guide](CONTRIBUTING.md) before submitting a pull request.

## Roadmap

- [x] Core voice-to-text pipeline
- [x] Multi-model support with download manager
- [x] Hardware detection
- [ ] Personal dictionary with auto-learning
- [ ] Hotword / custom vocabulary
- [ ] GPU acceleration (Metal / CUDA)
- [ ] Voice assistant mode (LLM integration)

## License

[Apache-2.0](LICENSE)

## Acknowledgments

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) — cross-platform ASR inference
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — lightweight ASR model
- [FunASR](https://github.com/FunAudioLLM/Fun-ASR) — multilingual ASR
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — state-of-the-art ASR
- [PySide6](https://doc.qt.io/qtforpython-6/) — Qt6 Python bindings
