# Apple's own speech engines, and a headless CLI

## Why

Every ThunderTalk engine is a download: the recommended dictation model is
879 MB, and Speak needs 364 MB (Kokoro) to 4 GB (IndexTTS) before it says a
word. macOS already ships an on-device recognizer (the Speech framework behind
Dictation) and a set of system voices. Offering them gives a zero-download
first run, a fallback when no model is installed, and a fair baseline to
measure our models against. Separately, scripts and agents wanted to
transcribe and speak without the window: a headless `--cli` that works from
source and from the packaged binary.

Constraint: the release must run on macOS 15, so the macOS 26 `SpeechAnalyzer`
/ `SpeechTranscriber` API is out; this uses `SFSpeechRecognizer` (macOS 10.15+)
and `AVSpeechSynthesizer` through PyObjC.

## What ships

**Apple recognition** (`core/apple_speech.py`, model id `apple-speech`).

- On device only: `requiresOnDeviceRecognition`, with
  `supportsOnDeviceRecognition` checked per locale. Locales without an
  installed on-device model are never offered; nothing is sent to Apple.
- `addsPunctuation` on; the dictation task hint for hotkey dictation; hotwords
  go in as `contextualStrings`.
- Word timestamps come from the transcription segments. Studio's file path
  uses them directly (cues are cut between words), with no forced alignment.
- Audio is recognised in pieces of 8–12 s cut at pauses (`segment_speech`), and
  each piece is a separate URL request (see the findings below).
- Auto language picks among the installed zh/en locales (see the findings for
  the rule).
- Models page card: "Apple · 系统自带" / "Apple · Built in", showing 0 MB,
  CPU / Neural Engine and the measured speed. Its language tags are the locales
  this Mac has installed. The card shows the hotword and native-timestamp
  features and a blurb with the measured accuracy. With no on-device language
  installed, the button becomes "Add a language" and opens Keyboard ▸
  Dictation.
- It can be selected as the dictation model like any other (`backend="apple"`
  in `AsrEngine`).
- Permission:
  - `NSSpeechRecognitionUsageDescription` is in the bundle's Info.plist.
  - The system prompt is requested when the model is first activated (GUI
    only).
  - If it is denied, both activation and dictation say where to turn it on
    (Privacy & Security ▸ Speech Recognition).
  - The CLI never prompts (`apple_speech.PROMPT = False`).

**Apple voices** (`core/tts_backends/apple.py`, engine `apple`).

- Rendering uses `AVSpeechSynthesizer.writeUtterance:toBufferCallback:`. The
  float PCM it returns is resampled from 22.05 kHz to the 24 kHz pipeline
  rate.
- `NSSpeechSynthesizer.startSpeakingString:toURL:` is the fallback. It is used
  when no buffer arrives within 8 s, which happens on a thread whose main
  queue nobody drains, such as a bare script.
- Voices are the installed zh/en voices: the Siri ones plus
  `com.apple.voice.*`. Eloquence and novelty voices are filtered out.
  - Quality (premium / enhanced / Siri / compact) is read from the voice.
    Premium and enhanced are shown on the chip; each voice's region is in its
    tooltip.
  - zh/en/mixed text works through the voice's own language. A Chinese voice
    reads embedded English words itself.
- On the Speak page the chips are tagged "系统 · 中文 · 女声" (System · Chinese
  · Female). Previews are rendered live on first click and cached, because
  system voices differ per Mac and can't be shipped as clips.
- It runs through the normal `speech.py` pipeline: piece planning, speed by
  time-stretch, level matching. It is deterministic (`stochastic = False`), so
  retries are skipped.

**Headless CLI** (`thundertalk/cli.py`).

```
ThunderTalk --cli transcribe IN --out OUT.{srt,vtt,json,txt} [--engine auto|apple|<model id>] [--language auto|zh|en|…]
ThunderTalk --cli speak (--text T | --text-file F) --out OUT.{m4a,wav,mp3} [--engine auto|apple|kokoro|voxcpm2|indextts] [--voice ID] [--language auto|zh|en] [--speed 0.5–2]
ThunderTalk --cli voices [--engine …]
ThunderTalk --cli status
```

- Dispatch happens in `run.py`, `__main__.py` and `app.main` before any Qt
  import, so no window or Dock icon appears, and it can run alongside the GUI
  app.
- stdout carries only JSON lines: `stage`, `progress` and a final `result`.
  Libraries' prints go to stderr, because fd 1 is redirected for the duration
  of the command.
- Exit code is 0 or 1. Errors have a stable `code`: `bad_args`, `not_found`,
  `no_model`, `no_speech`, `permission_denied`, `locale_unsupported`,
  `decode`, `tts_missing`, `ffmpeg_missing`, `unavailable`, `failed`.
- `transcribe --engine auto` uses the dictation model from settings, falling
  back to Apple when it is not downloaded.
- Subtitle cues are at most 7 s and 2 lines (16 CJK or 42 Latin characters per
  line), in order and never overlapping. JSON carries cues plus word times
  when the engine has them.
- MCP (`thundertalk/mcp/server.py`) gains `transcribe_file`, `speak_text` and
  `list_local_voices`. They run the CLI as a subprocess (`THUNDERTALK_BIN` can
  point at the packaged binary) and need no server.

## Measured

M3 Max, macOS 15.6. Apple was compared with the recommended dictation model,
Qwen3-ASR-0.6B ONNX int8, on the same clips in the same session; both were warm.

- Chinese is scored as CER over characters and English as WER over words, by
  `tts_verify.tokens`. Digits and number words are dropped on both sides, so
  "3:00" and "三点" are not counted as errors.
- "File" is the Studio path (`transcribe_file`) and "dictation" is a single
  `recognize` call (clips ≤ 30 s).
- The human clips are the test recordings shipped with the downloaded models:
  - sensevoice `zh`/`en`;
  - Fun-ASR-Nano `far_2`, `far_3`, the six `rag_*` and `noise_en`;
  - Qwen3-ASR `raokouling`, `noise2`, `fast1`, `f1_noise`, `rap1`,
    `noise1-en`.
- The synthetic clips are the 34 Speak benchmark renders (VoxCPM2, IndexTTS
  and Kokoro voices reading the five texts in the bench's `texts.py`).

| Set | Apple | Qwen3-ASR-0.6B int8 |
|---|---|---|
| Chinese, clear human speech (sensevoice zh, 6 × rag) | **5.8 %** CER (7/120) | 3.3 % (4/120) |
| Chinese, far-field / noisy / fast / tongue-twister (far_2, far_3, noise2, fast1, raokouling) | **83.9 %** (328/391) | 13.6 % (53/391) |
| English, human (5 clips, mostly noisy, one rap) | **90.8 %** WER (511/563) | 19.2 % (108/563) |
| Chinese, synthetic (16 clips, 165 s) | **3.6 %** (99/2744) | 0.5 % (15/2744) |
| English, synthetic (10 clips) | **5.2 %** (46/880) | 2.2 % (19/880) |
| Chinese with English words, synthetic (8 clips) | **34.6 %** (169/488) | 2.9 % (14/488) |
| Speed, Studio path, all 50 clips | **~15× real time** | ~5× |
| Dictation latency, median of 37 clips ≤ 30 s | **1.03 s** | 3.14 s |

Read honestly:

- Apple is about 3× faster and fine on clear, close speech in one language.
- It drops most of the words in far-field, noisy, fast or sung audio. On far_2
  it returns only "8号线"; on fast1 and rap1, nothing at all. Partial results
  show the same, so this is the recognizer and not our chunking.
- English words inside a Chinese sentence come back as near-homophones
  ("Sleeze" for "slides", "zone" for "Zoom").
- It does not replace the recommended model. It is a zero-download start and
  fallback. The Models card says this in numbers.

**Apple voices, read back by an independent recogniser** (Qwen3-ASR 1.7B MLX):

- Setup: 7 voices (Li-Mu, Yu-shu, Tingting for Chinese and mixed text; Aaron,
  Nicky, Samantha and Daniel for English) read the bench texts through
  `speech.py`.
- Chinese: 0.0 % CER (0/1029).
- Chinese with English words: 0.0 % (0/183).
- English: 1.1 % WER (8/704).
- Speed: about 37× real time overall (Chinese 41×, English 35×); 60 s of
  Chinese renders in about 1.5 s.
- These numbers are intelligibility, not naturalness: the compact voices
  installed by default sound clearly synthetic next to VoxCPM2 or IndexTTS.

`--selftest apple` (a system voice speaks zh and en; Apple recognition reads it back):

- Chinese: read-back error 0.0, TTS 0.54 s and ASR 0.19 s.
- English: read-back error 0.0, TTS 0.28 s and ASR 0.16 s.

## Mac-specific findings

- **Asking for permission without the Info.plist key kills the process**
  (SIGABRT from TCC, exit 134), so `request_permission` checks the bundle for
  `NSSpeechRecognitionUsageDescription` first.
  - From source, recognition works anyway with status `not_determined`.
  - The packaged app asks once.
  - The same is true for `AVCaptureDevice.requestAccess`. Now that
    pyobjc-AVFoundation is a dependency, `platform_utils.request_microphone`
    takes the AVFoundation path only when `NSMicrophoneUsageDescription` is
    present. Source runs keep the open-a-stream probe.
- **Two on-device requests at once: one comes back empty.** This happens with
  separate recognisers and separate queues, even for different locales. Every
  request is serialized behind a process-wide lock. A regression test runs two
  threads at once.
- **A long single request skips speech after pauses.** Pieces of 8–12 s cut at
  pauses keep everything, and an empty piece that has speech energy is
  retried once.
- **Decoding in the wrong language is 4–7× slower than in the right one.** For
  example, a 12 s Chinese clip took 7.1 s through en-US against 1.0 s through
  zh-CN, and English through zh-CN took 3.9 s against 0.45 s. Auto language
  therefore:
  - tries the last language that won first;
  - accepts it when its confidence is ≥ 0.5, or when zh-CN produced mostly
    Chinese characters (zh-CN writes English speech in Latin letters, so
    Chinese characters mean Chinese audio);
  - otherwise tries the other locale, which replaces the first only when it
    is ≥ 0.2 more confident, gives Chinese characters, or the first heard
    nothing;
  - decides on the first 4 s of a longer piece, then decodes the piece once.
  - A language switch costs about 1.5–4.5 s once; the same language costs
    about 0.2 s.
- Confidence alone is unreliable across locales: en-US gave a Chinese piece
  0.30 and zh-CN gave it 0.41. Choosing by raw confidence turned one Chinese
  minute into English gibberish.
- On-device locales on this Mac: en-ID, en-PH, en-SA, en-US and zh-CN, out of
  63 supported. zh-TW, yue and ja are supported but not installed for
  on-device use, and they appear once added under Keyboard ▸ Dictation.
- Listing locales costs about 0.38 s and scanning voices about 0.21 s. Both
  are cached.
- About 191 voices are installed, all compact; 17 zh/en voices are offered.
  `AVSpeechSynthesisVoiceQuality` was 1 (default) for all of them. Premium and
  enhanced voices appear after downloading them in Spoken Content settings.
- `writeUtterance:toBufferCallback:` calls back on the main queue.
  - Under Qt's Cocoa event loop it works from a worker thread (0.7 s for a
    3.8 s sentence).
  - In a bare script's worker thread the queue never drains, hence the 8 s
    first-buffer timeout and the NSSpeechSynthesizer fallback.
  - The CLI renders on the main thread and pumps the run loop.

## Packaging

- `ThunderTalk.spec` has the following additions:
  - `NSSpeechRecognitionUsageDescription` in Info.plist.
  - `collect_submodules` for the PyObjC wrappers this needs: Speech,
    AVFoundation, CoreMedia, CoreAudio, Foundation, AppKit and objc. They are
    imported lazily, so PyInstaller would not find them.
- No entitlement is needed for speech recognition under the hardened runtime
  (the app is not sandboxed).
- New dependencies, macOS only: `pyobjc-framework-speech` and
  `pyobjc-framework-avfoundation` (12.x).
- Packaged verification is recorded below.

### Packaged verification (M3 Max, macOS 15.6, ad-hoc build 2026-10-13)

`python build_macos.py` → `dist/ThunderTalk.app`. All commands via
`dist/ThunderTalk.app/Contents/MacOS/ThunderTalk`.

| Check | Result |
|---|---|
| `--selftest apple` | PASS — zh read-back 0.0 (TTS 0.55 s, ASR 0.21 s); en read-back 0.0 (TTS 0.27 s, ASR 0.17 s). Permission `not_determined` in CLI (no prompt). |
| `--cli status` | `ok: true`, `apple_speech.on_device_locales`: en-ID, en-PH, en-SA, en-US, zh-CN; 17 Apple TTS voices. |
| `--cli voices --engine apple` | 17 voices, final `ok: true`. |
| `--cli transcribe far_3.wav → zh.srt` | 21.8 s audio in 1.02 s, 5 cues, monotonic ≤ 7 s. |
| `--cli transcribe noise_en.wav → en.srt` | 10.57 s in 1.73 s, 2 cues. |
| `--cli transcribe short.mp4 → srt` | ffmpeg decode OK, same engine path as wav. |
| `--cli speak "你好世界" → hi.m4a` | 0.93 s audio in 0.72 s. |
| `THUNDERTALK_BIN=… pytest tests/test_cli.py` | 11 passed, 2 skipped (dictation-model tests when engine not forced). |

PyInstaller needed `collect_submodules` for Speech, AVFoundation, CoreMedia,
CoreAudio, Foundation, AppKit and objc (already in `ThunderTalk.spec`). No extra
hidden-import failures after the build above.
