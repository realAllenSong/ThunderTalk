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

## New in v1.7.0

- **See text while you speak:** Live Preview shows provisional words in the recording bar. It is on by default; the final text still comes from recognition of the full recording.
- **AI proofreading:** optional AI Proofread fixes misrecognized names, terms and homophones using your existing provider, without rewriting what you said.
- **Do more in Studio:** transcribe web links, queue several files or links, burn subtitles into a video copy, and generate AI meeting notes.
- **Smaller download:** about 224 MB zipped and 592 MiB installed, down from 330 MB and 961 MiB. Translation and IndexTTS share an optional 90 MB PyTorch download; model weights are extra.
- **Chinese translation fixed:** Direct mode with Chinese (Mandarin) as the target now returns Chinese instead of leaving the speech in English.

## New: Studio

Studio replaces the old Lab page. It has two parts: Transcribe and Speak.

**Transcribe.** Drop an audio or video recording (m4a, mp3, wav, mp4, mov and more) and get a timestamped transcript. Choose from your downloaded speech models; the active dictation model is selected by default. For speaker labels, select **MOSS-Transcribe-Diarize** and enable **Label speakers**; click a speaker to rename it. Selecting MOSS offers its download if needed. Export as TXT, Markdown, SRT, VTT or JSON. Common formats decode with the tools built into macOS; some formats such as WebM or MKV may need ffmpeg.

**From a link.** Paste a single-video link from YouTube, Bilibili or another site supported by yt-dlp into Studio's link field, then transcribe. yt-dlp ships with the app; you do not need to install it. The downloaded audio is transcribed on your Mac and deleted afterwards. Members-only and other sign-in restrictions are flagged; ThunderTalk does not sign in for you. If a site supplies a noticeably shorter preview than its advertised duration, Studio warns that the transcript covers only that part. Playlists, channels and live streams are not supported.

**Batch queue.** Add several files or links, choose the export formats (TXT, Markdown, SRT, VTT or JSON) and a destination folder, then select **Transcribe all**. Items run one at a time and results save automatically. You can save beside each source file instead; link results then go to Downloads. Cancel one item or the whole queue. Successful items leave the queue automatically, while failed or cancelled items remain for retry or removal. The latest transcript stays below, and every completed transcription is saved in **History**. Existing exports are kept; duplicate names get a numbered suffix.

**Subtitles in a video.** After transcribing a local video, choose **Add subtitles to video…** and pick **Burn subtitles into the video (always visible)** or **Add a subtitle track (viewer can turn on/off)**. Burned-in subtitles use white PingFang SC text on a semi-opaque dark rounded box, sized to the video, so they stay readable over busy footage and on-screen text. A subtitle track keeps the video picture unchanged. This action needs ffmpeg, which is not bundled: if you use [Homebrew](https://brew.sh/), install it with `brew install ffmpeg` in Terminal, then try again. Normal transcription of common formats does not need it. Link transcription downloads audio, so burn-in requires a local video file.

**Transcription history.** Reopen, rename, delete or search saved transcripts by title or text in Studio. Full transcripts load only when opened; the list loads 50 entries at a time. Files, links and batch items are stored under `~/.thundertalk/transcripts/<id>/` as transcript JSON and metadata, including source, duration, model, date, speaker names and generated notes.

**AI meeting notes.** After transcription, generate a short summary, key points, decisions, action items and open questions in the transcript's English or Chinese. Notes reuse the provider and model selected on the **AI Proofread** page, even with proofreading switched off; no model is downloaded. Copy or save notes as Markdown, or include them in the transcript's Markdown export. A queue checkbox generates notes after each transcription and also saves Markdown. Long transcripts use up to 16 chunks of about 6,000 characters plus one merge, with a 60-second timeout per call and Cancel. Cloud CLIs send transcript text to their provider; Ollama and LM Studio use your local server. Notes are asked to mark missing owners or deadlines as “not mentioned”. Review the generated notes against the transcript.

**Speak.** Turn text into speech with your choice of three local engines:

- **VoxCPM2** (OpenBMB): studio-quality 48 kHz speech and voice cloning; about real time on Apple silicon.
- **IndexTTS-2.5** (bilibili): very faithful voice cloning in Chinese, English, Japanese, Spanish and Arabic; about real time.
- **Kokoro** (82M): small and fast (about 4 times faster than real time, on the CPU), with 23 curated voices (20 Chinese, 3 English).
- 18 built-in voices for VoxCPM2 and IndexTTS in the styles people know from short videos, vlogs and podcasts: film recap, documentary, explainer and business-explainer narration, news, a cool CEO, two chatty lifestyle voices, a Taiwanese accent, and English YouTuber, podcast and narrator voices. All are designed voices (created by VoxCPM2 from a text description, not recordings of real people or imitations of any platform's voices) that ship with the app.
- Every voice has a short preview: click ▶ on a voice to hear it before you generate anything.
- Speed control (0.75x to 1.5x) and a seekable player. Save the result as WAV (lossless) or M4A (small).
- **My voices:** record or import 5 to 15 seconds of your own voice and it can read anything you type, with VoxCPM2 or IndexTTS. The reference is stored on your Mac in `~/.thundertalk/voices`. Please only clone voices you have the right to use.

The speech engines are one-time downloads. After that, file transcription and speech generation work offline. Optional AI notes use your chosen provider; web links and cloud providers require a connection.

## Features

**Dictation**

- One global hotkey that works in any app. The default is Right ⌘; choose Toggle (press to start, press to stop) or Hold in Settings, and change the key or combination there too.
- **Live Preview:** read provisional text in the floating recording bar while you speak. **Settings ▸ Live Preview** is on by default. Words can change as you continue; stopping triggers full-clip recognition for the final pasted text. Repeating preview windows are discarded; a conservative merge can recover Latin terms where a clean preview aligns with a Chinese span in the final text. Preview is hidden in Direct translation mode, and may pause or stop for a recording if the model cannot keep up.
- Several speech models: Qwen3-ASR 0.6B and 1.7B, SenseVoice-Small, NVIDIA Parakeet-TDT, and MOSS-Transcribe-Diarize. The app reads your hardware and recommends one.
- Chinese, English and Chinese-English mixed in one sentence. The interface itself is available in English and 中文.
- Hotwords: teach it product names, acronyms and people it keeps getting wrong (Qwen3-ASR models).
- Inverse text normalisation: spoken numbers become digits, in English and Chinese ("twenty five" becomes 25, "三百五十二" becomes 352).
- Optional translation into 100+ languages with SeamlessM4T v2, in **Direct** mode (paste the translation after you stop) or **Review** mode (transcribe first, then choose Replace or Keep original).
- A searchable history, stored as a plain file in `~/.thundertalk`.
- **Recent recordings:** on by default; keeps the last 20 dictations as 16 kHz mono WAV plus recognition metadata in `~/.thundertalk/recordings/`, stored only on this Mac. Switch off **Settings ▸ Keep recent recordings** to stop saving new audio (existing recordings remain). These files are never uploaded.
- **AI Proofread:** use your existing logged-in Codex, Claude Code, Gemini, Grok or Cursor CLI, an already installed Ollama / LM Studio model, Cherry Studio's API server, or a custom OpenAI-compatible API. No AI model is bundled or downloaded. Open **AI Proofread** in the sidebar: providers are detected automatically, each shows whether it is ready, needs a login or isn't running, and models are listed from the provider (a test call checks models that can't be listed). Search models by name or exact ID; Cursor effort variants are grouped, and supported reasoning levels are saved per provider with low effort as the default. Proofreading fixes misrecognized words — product and model names, jargon, homophones — using the sentence's context, your hotwords and the model's knowledge. It makes minimal edits: it never translates, switches between Chinese and English, reformats or answers. It applies to plain dictation, not Direct or Review translation. Raw text is pasted immediately; the proofread text replaces it only if you haven't typed, clicked, scrolled or switched apps. Transient failures retry once after five seconds; failures show a specific reason and leave raw text untouched. After replacement, the overlay shows only changes with a little context, striking out the old words beside their corrections. Changes advance in two-line pages with a page counter, staying visible for 3–6 seconds per page (up to 25 seconds of reading time); the last page counts any remaining changes. Hover to pause, click to dismiss, or start recording to replace the diff immediately. Terminal results are skipped because terminal input has no standard paste undo.

**Everyday details**

- A guided first run: welcome, permissions, choose a model, try it.
- Model downloads show real progress from the byte count, resume after an interrupted connection, and Cancel stops immediately.
- An in-app updater: a small prompt appears when a release is published; one click downloads, swaps and relaunches.
- Optional speaker mute while recording, so the microphone does not pick up your own audio.
- A memory profile (Settings, Performance) that trades some KV-cache size and thread count for roughly 3 GB less RAM.

**Privacy**

- No account, no subscription, no usage limits.
- Audio is recognised on your Mac and never uploaded.
- Network traffic includes model/component downloads, GitHub update checks and fetching Studio links. AI Proofread is off by default; optional proofreading and meeting notes use your selected provider. Cloud CLIs send dictated or transcript text to that provider using your account. Ollama and LM Studio inference stays local; Cherry Studio and custom APIs may forward text to cloud models.
- The code is open. Read it, build it, fork it.

## Download

With Homebrew:

```sh
brew install --cask realallensong/tap/thundertalk
```

Or download the latest **ThunderTalk.app** from [Releases](https://github.com/realAllenSong/ThunderTalk/releases/latest), move it to your Applications folder and open it. On first launch, grant **Microphone** and **Accessibility** access when prompted (Accessibility is what lets ThunderTalk type the text for you).

ThunderTalk is not yet notarised with an Apple Developer ID, so macOS shows a warning the first time you open a browser download. See [First launch: "ThunderTalk can't be opened"](#first-launch-thundertalk-cant-be-opened) below; it takes about ten seconds.

## Using ThunderTalk

1. Open ThunderTalk and follow the first-run setup, or go to **Models** and download one.
2. Click into any text field in any app.
3. Press the hotkey (default **Right ⌘**), speak, and press it again. The text is pasted at the cursor.

Change the hotkey, press mode, microphone and language in **Settings**. Open **Studio** for file/link transcription, batch exports, subtitles, meeting notes and text to speech.

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

> **MOSS-Transcribe-Diarize** ([OpenMOSS](https://github.com/OpenMOSS/MOSS-Transcribe-Diarize), first place in the 2nd MLC-SLM Challenge at INTERSPEECH 2026) is a multi-speaker model. In dictation it pastes clean text (there is an optional S01:/S02: speaker-label toggle on its model card), and it powers Studio's Label speakers option, with speaker labels and timestamps for recordings up to about 90 minutes in a single pass.
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

Translation and IndexTTS share an optional **90 MB PyTorch component**. Downloading either engine from the app also installs this component into `~/.thundertalk/runtime/`; restart once if prompted. Dictation, Studio transcription, VoxCPM2 and Kokoro need no component. Transformers and SciPy remain bundled because MLX speech uses them too. Model weights are downloaded separately from the **Models** page or the Studio engine card and stored in `~/.thundertalk/models/` or the Hugging Face cache.

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

- **App bundle:** about 592 MiB on disk; the zip download is about 224 MB (214 MiB). Measured against the installed v1.6.4 bundle: 961 MiB on disk and a 330 MB zip. PyTorch, torchaudio, SymPy, mpmath and NetworkX are now an optional 90 MB download shared by translation and IndexTTS. Model weights are additional.
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
| Speak in one language, paste another | **Direct mode**, which pastes a SeamlessM4T translation after you stop |
| Speak in your language and see a translation beside it | **Review mode**: ASR transcribes, then translates, and you choose Replace or Keep original |

The author's own setup: **Qwen3-ASR-0.6B (ONNX int8) with hotwords** for plain transcription, since it is fast, accurate and needs no GPU; and **Review mode** with that recognizer plus **SeamlessM4T v2** when a translation is needed. Review mode translates the transcript text (text to text), which is faster than Direct mode and lets you keep the original.

## Updates

ThunderTalk checks GitHub Releases shortly after it launches and shows a small prompt when a newer version exists. **Update Now** switches to the About page, downloads the new zip, quits, swaps the bundle in `/Applications/ThunderTalk.app`, removes the quarantine attribute and relaunches. **Later** dismisses the prompt until the next launch. You can also check by hand from **About, Check for Updates**.

After an update you may need to grant **Accessibility** and **Microphone** access again; see [After an update, the hotkey or microphone stops working](#after-an-update-the-hotkey-or-microphone-stops-working).

## Troubleshooting

### Reproduce a dictation issue

`tools/replay_dictation.py RECORDING.wav` reuses the JSON sidecar's model,
language and hotwords, then prints full-clip, chunked preview and merged text.
Run it from your checkout with `PYTHONPATH=$PWD`; use the shared machine's
`~/Library/Caches/ThunderTalk-bench/lock.py gpu -- <python> tools/replay_dictation.py …`
when available. `--model`, `--language`, `--hotword`, `--step` and
`--limit-seconds` allow controlled comparisons. The replay advances simulated
audio synchronously through the same preview window/commit/guard code; it does
not reproduce timer skips or the app's slow-model shutdown. Its outputs stay local.

### A model download was interrupted

Downloads are written to a temporary file and only renamed when complete, so a half-finished download is never mistaken for a usable model, and resuming continues from where it stopped. Open **Models** and press **Download** again. If a model is marked as downloaded but crashes when activated, delete `~/.thundertalk/models/<model-id>/` and download it again.

### "Translation model not downloaded"

You chose **Direct** or **Review** mode but have not downloaded SeamlessM4T (about 9 GB). The Translation card on the Models page has a **Download** button; press it and the app fetches and loads the model.

### Studio says a model or engine is missing

The speaker model, and the voice engines for Speak, are one-time downloads. Studio shows the size and a Download button where it is needed. Fast single-speaker transcription needs a dictation model to be loaded: pick one on the Models page.

### Studio cannot fetch a link or only transcribes a preview

Use a single-video page link, not a playlist, channel or live stream. Sites may require sign-in for members-only or age-restricted videos, or reject downloads temporarily; ThunderTalk does not sign in or bypass access limits. A preview warning means the site supplied only part of the advertised recording. Transcribe a full local file you have access to instead. Links require an internet connection.

### Studio asks for ffmpeg

Burning subtitles into a video copy requires ffmpeg. Some formats, including WebM/Opus audio from links, may also need it to decode. With [Homebrew](https://brew.sh/) installed, run `brew install ffmpeg` in Terminal and retry. Common formats such as M4A, MP3, WAV, MP4 and MOV use macOS's built-in decoder.

### AI Proofread or meeting notes are unavailable

Open **AI Proofread** in the sidebar, choose a provider marked Ready and a model, and follow its action: **Verify**, **How to log in**, or start the app's local server. Proofreading must be switched on for dictation; Studio notes only need the provider/model selected. Cloud CLIs, Cherry Studio and custom APIs may send text to cloud models; Ollama and LM Studio use your local server. Failed or timed-out proofreading keeps the original text; failed notes keep the transcript. For the notes limit of 16 parts, split a very long recording and try again.

### Translation or IndexTTS asks for a component or restart

These engines need the shared optional PyTorch component (about 90 MB) as well as their model weights. Use the in-app download and restart once if prompted. Other dictation, transcription and speech engines do not need this component.

### The app opens to a blank window or a single solid colour

This is usually a Qt styling conflict. Quit, then launch from Terminal with `/Applications/ThunderTalk.app/Contents/MacOS/ThunderTalk`. If the console prints `Could not parse stylesheet`, open an issue with the output.

### The history page says 0 sessions but I had sessions yesterday

ThunderTalk reads `~/.thundertalk/history.json`. If a write was interrupted (force-quit while saving, disk full) the file can become unreadable. The app does not silently reset it; it renames the bad file to `history.broken-<timestamp>.json` next to it. Open that file in a text editor, fix the JSON, and paste the entries back into a fresh `history.json`.

### The microphone is allowed but the app says "no audio"

An old permission grant can stop matching an updated ad-hoc binary while System Settings still shows the toggle on. In onboarding, or the Home permission banner, choose **Reset and grant again**. This resets only ThunderTalk’s microphone entry and asks macOS for access again. Permission status updates automatically while the page is visible. Restricted permissions must be changed by the administrator.

### The hotkey does not start recording

ThunderTalk needs **Accessibility** permission to read global key events. Choose **Reset and grant again** for Accessibility in onboarding or Home, then enable ThunderTalk in the settings pane that opens. The app rechecks access automatically.

### First launch: "ThunderTalk can't be opened"

Because the app is not notarised, Gatekeeper warns about browser downloads. To allow it:

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

Ad-hoc signing gives every build a different code-directory hash. macOS can retain an “on” toggle for the old hash while denying the updated app. In onboarding or Home, choose **Reset and grant again**, then grant the microphone prompt or enable Accessibility in the settings pane. This clears only the selected ThunderTalk permission; no other app’s grants are affected. Status is polled about once per second while the permission page is visible.

Local builds now reuse a signing certificate when available, preserving future grants across builds made with that certificate. Switching from an old ad-hoc build requires granting access once to the new identity. Developer ID signing and notarization are still pending; a self-signed local identity does not remove Gatekeeper warnings.

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
uv sync --extra mlx --extra translation --extra indextts

# Run from source
uv run python run.py

# Build the macOS app (output: dist/ThunderTalk.app)
.venv/bin/python build_macos.py
```

### Stable local signing

Run `tools/make_signing_identity.sh` once on the build Mac. It creates **ThunderTalk Local Signing** using OpenSSL, imports its private key into a dedicated `~/Library/Keychains/ThunderTalkLocalSigning.keychain-db`, and applies user trust **for code signing only** with `security add-trusted-cert`. macOS may request your login password for the trust setting. For an unattended machine, use `THUNDERTALK_DEFER_TRUST=1 tools/make_signing_identity.sh` and rerun without that variable when you can approve the prompt. Explicit certificate-leaf requirements and `codesign --verify` work for local builds even with that global trust setting deferred.

The encrypted keychain and its random password in `~/.thundertalk/signing/keychain-password` are private, local files; never commit or distribute either. The script preserves existing keychain search entries, grants `/usr/bin/codesign` access only to the dedicated keychain, deletes scratch keys on exit, and reuses the identity rather than replacing it. Keep a secure backup of the keychain/password: replacing the certificate invalidates existing permission grants. `build_macos.py` unlocks only this dedicated keychain and automatically uses the local identity when present, with an explicit requirement for `com.thundertalk.app` and the certificate leaf. It verifies the whole bundle after signing. When the identity is absent it retains ad-hoc signing; `SIGN_IDENTITY=-` forces that fallback.

For Developer ID, set `SIGN_IDENTITY` to your certificate name/hash and supply the existing `APPLE_ID`, `APPLE_APP_PASSWORD`, and `TEAM_ID` notarization variables. `SIGN_REQUIREMENT` can supply a reviewed team-based requirement when migrating/renewing that certificate. Do not share the local private key with end users.

Thread regression checks (no permission changes):

```sh
# Use lock.py gpu on shared Macs, and PYTHONPATH=$PWD with the shared interpreter.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/check_threaded_studio.py --preload
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/check_threaded_studio.py --case picker
python tools/check_threaded_studio.py --streams-only
```

The public Bilibili clip is fetched through Studio’s real `TranscribeWorker` link path. The checks reuse cached Qwen 0.6B/1.7B, MOSS and VoxCPM2 weights, start dictation on another thread during inference, and exercise preload. The memory admission guard can fall back to dictation’s model; each run prints the actual model id. `--streams-only --baseline-streams` intentionally reproduces the old native CPU-stream abort in an isolated process; use it only for diagnosing the baseline.

Use all three extras for source runs and release builds so PyInstaller can analyze the complete dependencies. The release bundle excludes PyTorch and its exclusive dependencies and installs pinned wheels on demand. The optional release runtime requires Python 3.12 / Apple Silicon macOS; Intel source runs are untested.

The optional component downloads immutable Python 3.12 arm64 wheels directly from PyPI. `thundertalk/core/runtime_manifest.json` records exact versions, URLs, sizes and SHA-256 hashes from `uv.lock`: torch/torchaudio 2.11.0, SymPy 1.14.0, mpmath 1.3.0 and NetworkX 3.6.1. No pip or separately installed Python is required on the user's Mac. Downloads resume after cancellation; failed integrity checks cannot publish a runtime. An installation lock and atomic directory rename protect concurrent app instances. The app activates the wheel directory before Transformers imports; if installed in an already-running session, restart when prompted. Native dylibs remain in the original wheel-relative layout; release signing must retain the existing `disable-library-validation` entitlement.

No extra GitHub release asset is required. Attach the normal app zip; do not attach the developer environment. When updating the runtime, choose a new runtime ID, update its wheel records from `uv.lock`, rebuild, and rerun the packaged checks. Do not change a runtime ID's pins in place.

```bash
PYTHONPATH=$PWD .venv/bin/python -m pytest -q
.venv/bin/python -m ruff check thundertalk tests tools/runtime_hook.py
# Run packaged checks sequentially (use the machine GPU lock on shared machines).
APP=dist/ThunderTalk.app/Contents/MacOS/ThunderTalk
"$APP" --selftest audio
"$APP" --selftest tts --engine kokoro
"$APP" --selftest tts --engine voxcpm2
"$APP" --selftest moss --file sample.m4a
"$APP" --selftest asr --file sample.m4a
# Install and verify the optional component through the same installer as the UI.
"$APP" --selftest runtime
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$APP" --selftest tts --engine indextts
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 "$APP" --selftest translate
```

Run the first checks with no optional runtime installed and models already downloaded. The runtime check downloads about 90 MB; the final two checks need their separately downloaded model weights. `translate` checks text and speech translation using a shipped English reference clip and requires Chinese characters in both outputs. Mandarin (`cmn`) speech translation uses an English text intermediate with the same SeamlessM4T model, then translates that text to Chinese: the checkpoint can otherwise copy English speech despite the correct Chinese decoder prefix. This adds one text translation pass; other target languages use direct speech translation.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Tech stack

- **UI:** [PySide6](https://doc.qt.io/qtforpython-6/) (Qt 6), with the colours, type and spacing defined in one theme module
- **Speech recognition:** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (ONNX), [mlx-qwen3-asr](https://github.com/nicoboss/mlx-qwen3-asr) and [mlx-audio](https://github.com/Blaizzy/mlx-audio) (MLX)
- **Text to speech:** VoxCPM2 through mlx-audio, IndexTTS-2.5 through a vendored MLX port, Kokoro through sherpa-onnx; all in-process
- **Translation:** SeamlessM4T v2 through PyTorch and Transformers
- **Audio:** [sounddevice](https://python-sounddevice.readthedocs.io/) for capture; macOS `afconvert` for decoding files
- **Hotkeys:** native NSEvent on macOS
- **Build:** [PyInstaller](https://pyinstaller.org/), stable local signing when available, ad-hoc fallback

## FAQ

**Is ThunderTalk free?** Yes. It is MIT licensed, with no account, no subscription and no usage limits.

**Does it work offline?** Yes. Once a model is downloaded, recognition, translation, file transcription and text to speech run on your Mac. Studio links need a connection. Optional AI proofreading and meeting notes depend on your chosen provider; cloud providers require a connection.

**How is it different from Typeless, Wispr Flow or superwhisper?** Those are paid, closed-source apps that typically process audio in the cloud. ThunderTalk is free and open source, with speech processed on your Mac. Optional AI proofreading and meeting notes use your selected provider; you can read the code, and your voice stays on your machine.

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
