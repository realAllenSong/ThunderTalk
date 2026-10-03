<p align="center">
  <img src="assets/icon.png" width="80" alt="ThunderTalk logo" />
</p>

<h1 align="center">ThunderTalk</h1>

<p align="center">
  Free, open-source voice input for macOS.<br/>
  Press a key, speak, and the text appears at your cursor.<br/>
  Speech recognition runs on your own Mac: no account, no subscription, no cloud.
</p>

<p align="center">
  <a href="README.zh.md">中文文档</a> · <a href="https://realallensong.github.io/ThunderTalk/">Website</a> · <a href="https://github.com/realAllenSong/ThunderTalk/releases/latest">Download</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/platform-macOS-brightgreen" alt="Platform: macOS" />
  <a href="https://github.com/realAllenSong/ThunderTalk/releases/latest"><img src="https://img.shields.io/github/v/release/realAllenSong/ThunderTalk?color=orange" alt="Latest release" /></a>
  <a href="https://github.com/realAllenSong/ThunderTalk/releases"><img src="https://img.shields.io/github/downloads/realAllenSong/ThunderTalk/total?color=blue" alt="Downloads" /></a>
</p>

<p align="center">
  <img src="assets/demo.gif" width="760" alt="ThunderTalk: dictating a mixed Chinese-English sentence into an email, transcribing a three-person meeting with speaker labels, and reading text aloud" />
  <br/><sub>The recording bar and Studio screens are the app's own interface (the email window is a stand-in). The dictated sentence, the meeting transcript and the speech are real model output on an M3 Max; only the pacing is scripted.</sub>
</p>

---

ThunderTalk is a voice input app for macOS. Press a hotkey in any app, say what you want to write, and the text lands where your cursor is. Recognition happens on your machine, on the Apple Silicon GPU through MLX or on the CPU through ONNX, so your audio is never sent anywhere. It is MIT licensed, and works as an open-source alternative to Typeless, Wispr Flow, superwhisper and macOS Dictation.

The interface is deliberately quiet: warm paper background, near-black ink, one orange accent, and motion only where it tells you something (the live level meter, a spinner, a real progress bar).

## New: Studio

Studio replaces the old Lab page and is the headline of the 1.5 release. It has two parts.

**Transcribe.** Drop an audio or video recording (m4a, mp3, wav, mp4, mov and more) and get a timestamped transcript. For a conversation, choose **Multiple speakers** and MOSS-Transcribe-Diarize labels every turn; click a speaker to rename it. Export as TXT, Markdown, SRT, VTT or JSON. Decoding uses the tools built into macOS, so ffmpeg is not needed.

**Speak.** Turn text into speech with your choice of three local engines:

- **VoxCPM2** (OpenBMB): studio-quality 48 kHz speech and voice cloning; about real time on Apple silicon.
- **IndexTTS-2.5** (bilibili): very faithful voice cloning in Chinese, English, Japanese, Spanish and Arabic; about real time.
- **Kokoro** (82M): small and fast (about 4 times faster than real time, on the CPU), with 23 curated voices (20 Chinese, 3 English).
- 18 built-in voices for VoxCPM2 and IndexTTS in the styles people know from short videos, vlogs and podcasts: film recap, documentary, explainer and business-explainer narration, news, a cool CEO, two chatty lifestyle voices, a Taiwanese accent, and English YouTuber, podcast and narrator voices. All are designed voices (created by VoxCPM2 from a text description, not recordings of real people or imitations of any platform's voices) that ship with the app.
- Every voice has a short preview: click ▶ on a voice to hear it before you generate anything.
- Speed control (0.75x to 1.5x) and a seekable player. Save the result as WAV (lossless) or M4A (small).
- **My voices:** record or import 5 to 15 seconds of your own voice and it can read anything you type, with VoxCPM2 or IndexTTS. The reference is stored on your Mac in `~/.thundertalk/voices`. Please only clone voices you have the right to use.

The speech engines are one-time downloads. After that, everything in Studio works offline.

## Features

**Dictation**

- One global hotkey that works in any app. The default is Right ⌘; choose Toggle (press to start, press to stop) or Hold in Settings, and change the key or combination there too.
- Several speech models: Qwen3-ASR 0.6B and 1.7B, SenseVoice-Small, NVIDIA Parakeet-TDT, and MOSS-Transcribe-Diarize. The app reads your hardware and recommends one.
- Chinese, English and Chinese-English mixed in one sentence. The interface itself is available in English and 中文.
- Hotwords: teach it product names, acronyms and people it keeps getting wrong (Qwen3-ASR models).
- Inverse text normalisation: spoken numbers become digits, in English and Chinese ("twenty five" becomes 25, "三百五十二" becomes 352).
- Optional translation into 100+ languages with SeamlessM4T v2, in **Direct** mode (translate as you speak) or **Review** mode (transcribe first, then choose Replace or Keep original).
- A searchable history, stored as a plain file in `~/.thundertalk`.
- **AI cleanup and voice editing:** use your existing logged-in Codex, Claude Code, Gemini, Grok or Cursor CLI, an already installed Ollama / LM Studio model, Cherry Studio's API server, or a custom OpenAI-compatible API. No AI model is bundled or downloaded. Enable it in Settings, choose a provider/model, and set app-specific styles or turn it off per app. Raw text is pasted immediately; cleanup replaces it only if you haven't typed, clicked, scrolled or switched apps. Timeouts leave raw text untouched. Terminal results are skipped because terminal input has no standard paste undo.
- Whole-utterance voice commands in English and Chinese: “new line / 换行”, “new paragraph / 新段落”, “delete that / 删掉上一句” (undo the last untouched dictation), and “tab key / 制表符”. With AI cleanup enabled, select text before recording and say “make this shorter”, “make this more formal / 改得正式一点” or “translate to English / 翻译成英文” to edit the selection. Selection reading requires macOS Accessibility support; the full clipboard is saved and restored.

**Everyday details**

- A guided first run: welcome, permissions, choose a model, try it.
- Model downloads show real progress from the byte count, resume after an interrupted connection, and Cancel stops immediately.
- An in-app updater: a small prompt appears when a release is published; one click downloads, swaps and relaunches.
- Optional speaker mute while recording, so the microphone does not pick up your own audio.
- A memory profile (Settings, Performance) that trades some KV-cache size and thread count for roughly 3 GB less RAM.

**Privacy**

- No account, no subscription, no usage limits.
- Audio is recognised on your Mac and never uploaded.
- With AI cleanup off (the default), network traffic is downloading models and checking GitHub Releases for updates. When enabled, cloud CLIs send dictated/selected text to that provider using your account. Ollama and LM Studio inference stays local; Cherry Studio and custom APIs may forward text to cloud models.
- The code is open. Read it, build it, fork it.

## Download

With Homebrew:

```sh
brew install --cask realallensong/tap/thundertalk
```

Or download the latest **ThunderTalk.app** from [Releases](https://github.com/realAllenSong/ThunderTalk/releases/latest), move it to your Applications folder and open it. On first launch, grant **Microphone** and **Accessibility** access when prompted (Accessibility is what lets ThunderTalk type the text for you).

ThunderTalk is signed ad hoc rather than notarised (an Apple Developer ID costs $99 a year), so macOS shows a warning the first time you open a browser download. See [First launch: "ThunderTalk can't be opened"](#first-launch-thundertalk-cant-be-opened) below; it takes about ten seconds.

## Using ThunderTalk

1. Open ThunderTalk and follow the first-run setup, or go to **Models** and download one.
2. Click into any text field in any app.
3. Press the hotkey (default **Right ⌘**), speak, and press it again. The text is pasted at the cursor.

Change the hotkey, press mode, microphone and language in **Settings**. Open **Studio** for file transcription and text to speech.

## Supported models

### Speech recognition

| Model | Size | Backend | Languages | Accuracy | Hotwords |
|-------|------|---------|-----------|----------|----------|
| SenseVoice-Small | 241 MB | ONNX (CPU) | 5 | ★★★☆☆ | No |
| Qwen3-ASR-0.6B | 940 MB | ONNX (CPU) | 52 | ★★★★★ | Yes |
| Qwen3-ASR-0.6B | ~1.9 GB | MLX (Metal GPU) | 52 | ★★★★★ | Yes |
| Qwen3-ASR-1.7B | ~4.7 GB | MLX (Metal GPU) | 52 | ★★★★★ | Yes |
| MOSS-Transcribe-Diarize 0.9B | ~1.8 GB | MLX (Metal GPU) | 50+ | ★★★★★ | No |
| Parakeet-TDT 0.6B v3 | 640 MB | ONNX (CPU) | 25 (European) | ★★★★★ | No |
| Parakeet-TDT 0.6B v2 | 640 MB | ONNX (CPU) | English | ★★★★★ | No |

> **MOSS-Transcribe-Diarize** ([OpenMOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize), first place in the 2nd MLC-SLM Challenge at INTERSPEECH 2026) is a multi-speaker model. In dictation it pastes clean text (there is an optional S01:/S02: speaker-label toggle on its model card), and it powers Studio's Multiple speakers mode, with speaker labels and timestamps for recordings up to about 90 minutes in a single pass.
>
> **Parakeet-TDT** (NVIDIA) runs on the CPU, with punctuation and casing built in. On an M3 Max it transcribes about 50 times faster than real time (RTF 0.019), roughly 4 times faster than Qwen3-ASR ONNX.

### Text to speech (Studio)

| Model | Size | Backend | Used for |
|-------|------|---------|----------|
| VoxCPM2 (8-bit, Apache-2.0) | 3.2 GB | MLX (Metal GPU) | Built-in voices, cloning |
| IndexTTS-2.5 (8-bit, bilibili Model Use License) | 1.7 GB + 2.3 GB encoder | MLX (Metal GPU) | Built-in voices, cloning |
| Kokoro v1.1 multi-lang (Apache-2.0) | 364 MB | ONNX (CPU) | 23 built-in voices (20 Chinese, 3 English) |

Each is a one-time download offered from the Speak tab when you pick one of its voices. IndexTTS runs through a patched copy of the [mlx-indextts2](https://github.com/vanch007/mlx-indextts2) MLX port (see `third_party/README.md`).

### Translation

| Model | Size | Backend | Languages | Use case |
|-------|------|---------|-----------|----------|
| SeamlessM4T v2 Large | ~9 GB | PyTorch + MPS / CPU | 100+ | Speech and text translation in **Direct** and **Review** modes |

The translation engine (PyTorch and Transformers) is bundled in `ThunderTalk.app`, so nothing needs installing; only the SeamlessM4T model file is downloaded on demand. Models are downloaded from the app's **Models** page and stored in `~/.thundertalk/models/`.

## System requirements

An **Apple Silicon Mac (M1 or newer) with macOS 15 (Sequoia) or later.** The release build is arm64-only, and the speech libraries it bundles need macOS 15 (MLX), 14 (ONNX Runtime) and 13 (Qt). Intel Macs are not supported by the release build.

### Suggested models by memory

| Mac | RAM | Suggested ASR model | Translation |
|-----|-----|---------------------|-------------|
| M1 / M2 | 8 GB | SenseVoice-Small or Qwen3-ASR-0.6B | Not enough RAM for SeamlessM4T |
| M1 Pro / M2 Pro / M3 | 16 GB | Qwen3-ASR-0.6B or 1.7B | Works, but close other heavy apps |
| Max / Ultra chips | 24 GB or more | Anything | Comfortable |

Measured on an M3 Max so far (same recordings for every model):

| Model | English WER | Chinese CER | Speed |
|-------|------------:|------------:|------:|
| Qwen3-ASR-0.6B (MLX, GPU) | 4.7 % | 5.0 % | ~11x real time |
| Qwen3-ASR-0.6B (ONNX int8, CPU) | 5.5 % | 4.5 % | ~12x |
| Parakeet-TDT 0.6B v2 (CPU) | 3.0 % | English only | ~50x |
| Parakeet-TDT 0.6B v3 (CPU) | 4.5 % | no Chinese | ~50x |
| MOSS-Transcribe-Diarize (MLX, Studio) | 3.6 % | 3.5 % | 15-21x |

Smaller chips will be slower. If you have another Mac, the numbers from `ThunderTalk --selftest` help everyone: see [#6](https://github.com/realAllenSong/ThunderTalk/issues/6).

### Disk space

- **App bundle:** about 820 MB on disk. The translation engine (PyTorch and Transformers) is included, which is why it is this size, and why translation works as soon as its model is downloaded.
- **Minimum to run:** about 1.1 GB (the app plus SenseVoice-Small).
- **Recommended with translation:** about 13 GB free (the app, Qwen3-ASR-0.6B, SeamlessM4T and working files).

The **Models** page shows your detected hardware and tags each model **Recommended**, **Needs Apple Silicon** or **Needs MLX**, so you do not have to memorise these tables.

## Choosing a model

| Goal | Pick |
|------|------|
| Fastest start, fewest languages | **SenseVoice-Small** (5 languages, no hotwords) |
| Most accurate on the CPU | **Qwen3-ASR-0.6B (ONNX int8)** |
| Most accurate, Apple Silicon GPU | **Qwen3-ASR-0.6B (MLX fp16)**, the default |
| Hard accents or noisy audio | **Qwen3-ASR-1.7B (MLX fp16)**, needs 16 GB of RAM or more |
| Meetings and interviews with speaker labels | **MOSS-Transcribe-Diarize 0.9B (MLX)**, Apple Silicon only |
| Fastest English dictation | **Parakeet-TDT 0.6B v2 (ONNX int8)** |
| European languages on the CPU | **Parakeet-TDT 0.6B v3 (ONNX int8)** |
| Speak in one language, paste another | **Direct mode**, which uses SeamlessM4T directly |
| Speak in your language and see a translation beside it | **Review mode**: ASR transcribes, then translates, and you choose Replace or Keep original |

The author's own setup: **Qwen3-ASR-0.6B (ONNX int8) with hotwords** for plain transcription, since it is fast, accurate and needs no GPU; and **Review mode** with that recognizer plus **SeamlessM4T v2** when a translation is needed. Review mode translates the transcript text (text to text), which is faster than Direct mode and lets you keep the original.

## Updates

ThunderTalk checks GitHub Releases shortly after it launches and shows a small prompt when a newer version exists. **Update Now** switches to the About page, downloads the new zip, quits, swaps the bundle in `/Applications/ThunderTalk.app`, removes the quarantine attribute and relaunches. **Later** dismisses the prompt until the next launch. You can also check by hand from **About, Check for Updates**.

After an update you may need to grant **Accessibility** and **Microphone** access again; see [After an update, the hotkey or microphone stops working](#after-an-update-the-hotkey-or-microphone-stops-working).

## Troubleshooting

### A model download was interrupted

Downloads are written to a temporary file and only renamed when complete, so a half-finished download is never mistaken for a usable model, and resuming continues from where it stopped. Open **Models** and press **Download** again. If a model is marked as downloaded but crashes when activated, delete `~/.thundertalk/models/<model-id>/` and download it again.

### "Translation model not downloaded"

You chose **Direct** or **Review** mode but have not downloaded SeamlessM4T (about 9 GB). The Translation card on the Models page has a **Download** button; press it and the app fetches and loads the model.

### Studio says a model or engine is missing

The speaker model, and the voice engines for Speak, are one-time downloads. Studio shows the size and a Download button where it is needed. Fast single-speaker transcription needs a dictation model to be loaded: pick one on the Models page.

### The app opens to a blank window or a single solid colour

This is usually a Qt styling conflict. Quit, then launch from Terminal with `/Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk`. If the console prints `Could not parse stylesheet`, open an issue with the output.

### The history page says 0 sessions but I had sessions yesterday

ThunderTalk reads `~/.thundertalk/history.json`. If a write was interrupted (force-quit while saving, disk full) the file can become unreadable. The app does not silently reset it; it renames the bad file to `history.broken-<timestamp>.json` next to it. Open that file in a text editor, fix the JSON, and paste the entries back into a fresh `history.json`.

### The microphone is allowed but the app says "no audio"

macOS sometimes ties the permission to a specific binary path, for example after the app was moved between folders. Remove and re-add it under **System Settings, Privacy & Security, Microphone**.

### The hotkey does not start recording

ThunderTalk needs **Accessibility** permission to read global key events. In **System Settings, Privacy & Security, Accessibility**, switch ThunderTalk off and on again, then restart the app.

### First launch: "ThunderTalk can't be opened"

Because the app is signed ad hoc, Gatekeeper warns about browser downloads. To allow it:

1. Drag `ThunderTalk.app` into `/Applications`.
2. Try to open it once; macOS refuses and shows the warning.
3. Open **System Settings, Privacy & Security**, scroll near the bottom and click **Open Anyway** next to the "ThunderTalk was blocked" line.
4. Confirm in the next dialog.

Or in one command:

```bash
xattr -dr com.apple.quarantine /Applications/ThunderTalk.app
open /Applications/ThunderTalk.app
```

macOS remembers the choice. Updates that arrive through the in-app updater strip the quarantine attribute automatically, so only the first browser download needs this.

### After an update, the hotkey or microphone stops working

Ad-hoc signing gives every build a different code-directory hash, and macOS keys its privacy permissions to that hash. After the updater swaps the bundle, macOS treats the new binary as a different app, so the old **Accessibility** entry quietly stops applying and **Microphone** access has to be granted again. ThunderTalk shows a one-time note about this after an update. To fix it:

1. Open **System Settings, Privacy & Security, Accessibility**.
2. Remove the old `ThunderTalk` entry (the one that is on but greyed out, or has a stale path).
3. Click `+`, choose `/Applications/ThunderTalk.app`, and add it again.
4. Switch it on and try the hotkey. The first recording may ask for microphone access again; allow it.

This is a constraint of shipping an un-notarised app. Signing and notarising each release with an Apple Developer ID would remove it, at a cost of $99 a year and a few more release steps.

### What is the minimum machine?

Any Apple Silicon Mac with macOS 15 should run **SenseVoice-Small** (163 MB), the lightest model. Only an M3 Max has been measured so far; reports from 8 GB machines are welcome in [#6](https://github.com/realAllenSong/ThunderTalk/issues/6). Translation is unrealistic below 16 GB of RAM.

## Build from source

You need macOS, Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install (Apple Silicon: MLX backend + translation engine)
git clone https://github.com/realAllenSong/ThunderTalk.git
cd ThunderTalk
uv sync --extra mlx --extra translation

# Run from source
uv run python run.py

# Build the macOS app (output: dist/ThunderTalk.app)
.venv/bin/python build_macos.py
```

Always pass **both** extras. `uv sync --extra mlx` on its own removes the `translation` extra (PyTorch) from the environment, and a build made without it ships with broken translation ("PyTorch was not found" in the build log is the tell). On an Intel Mac, drop `--extra mlx`.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Tech stack

- **UI:** [PySide6](https://doc.qt.io/qtforpython-6/) (Qt 6), with the colours, type and spacing defined in one theme module
- **Speech recognition:** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (ONNX), [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) and [mlx-audio](https://github.com/Blaizzy/mlx-audio) (MLX)
- **Text to speech:** VoxCPM2 through mlx-audio, IndexTTS-2.5 through a vendored MLX port, Kokoro through sherpa-onnx; all in-process
- **Translation:** SeamlessM4T v2 through PyTorch and Transformers
- **Audio:** [sounddevice](https://python-sounddevice.readthedocs.io/) for capture; macOS `afconvert` for decoding files
- **Hotkeys:** native NSEvent on macOS
- **Build:** [PyInstaller](https://pyinstaller.org/), ad-hoc signed

## FAQ

**Is ThunderTalk free?** Yes. It is MIT licensed, with no account, no subscription and no usage limits.

**Does it work offline?** Yes. Once a model is downloaded, recognition, translation, Studio and text to speech all run on your Mac without a connection.

**How is it different from Typeless, Wispr Flow or superwhisper?** Those are paid, closed-source apps that typically process audio in the cloud. ThunderTalk is free, open source and fully local, so you can read the code and your voice stays on your machine.

**Does it support Chinese and mixed Chinese-English dictation?** Yes. Qwen3-ASR handles 52 languages, including Chinese and English mixed in the same sentence, and the interface is available in English and 中文.

**Does it work on Intel Macs?** Not the release build: it is built for Apple Silicon only. Running from source on Intel with the CPU (ONNX) models has not been tested.

**Which apps does dictation work in?** Any app with a text cursor: browsers, editors, Slack, mail, terminals. ThunderTalk pastes where the cursor is.

## License

ThunderTalk is open source under the [MIT License](LICENSE). Use it, fork it, ship it in your own product. A star or a pull request is always appreciated.

## Acknowledgments

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) for cross-platform ASR inference
- [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) for MLX-native Qwen3 ASR
- [mlx-audio](https://github.com/Blaizzy/mlx-audio) for MLX inference of MOSS-Transcribe-Diarize and VoxCPM2
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) for a lightweight ASR model
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) for state-of-the-art ASR
- [OpenMOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize) for MOSS-Transcribe-Diarize
