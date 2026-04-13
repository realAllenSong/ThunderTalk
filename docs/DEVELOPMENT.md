# Development Guide

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- macOS: Accessibility permission for global hotkeys
- Linux: `libasound2-dev`, `xdotool`

## Setup

```bash
git clone https://github.com/songallen/ThunderTalk.git
cd ThunderTalk
uv sync
```

## Run

```bash
uv run python run.py
```

Or with the installed entry point:

```bash
uv run thundertalk
```

## Lint & Format

```bash
uv run ruff check thundertalk/
uv run ruff format thundertalk/
```

## Project Structure

```
thundertalk/
├── app.py              # Application entry, pipeline orchestration
├── core/
│   ├── audio.py        # Microphone recording (sounddevice)
│   ├── asr.py          # ASR engine (sherpa-onnx)
│   ├── hotkey.py       # Global shortcut (pynput)
│   ├── text_output.py  # Clipboard paste to active app
│   └── models.py       # Model registry, download, hardware detection
└── ui/
    ├── overlay.py       # Floating voice input capsule
    ├── main_window.py   # Settings / model management window
    └── tray.py          # System tray icon
```
