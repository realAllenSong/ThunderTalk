# Changelog

What each ThunderTalk release added and fixed. The full notes, with measurements, are on each [GitHub release](https://github.com/realAllenSong/ThunderTalk/releases).

## v1.20.0 (2026-11-22)

- **A local speech API for voice agents: `thundertalk serve`** (packaged: `ThunderTalk --cli serve`). It listens on 127.0.0.1:8765 by default, needs no account and sends nothing off the Mac.
  - `WS /v1/realtime/transcribe?language=en|zh|auto&silence_ms=350`: send 16 kHz PCM16; get `partial` text about every 0.4 s while someone speaks and a `final` per utterance once the pause reaches `silence_ms`. `commit` closes an utterance now, `reset` drops it. The final is prepared the moment the speaker stops (~0.1 s), so it is ready when the pause is long enough.
  - `POST /v1/audio/speech` (OpenAI-style; Kokoro by default, VoxCPM2/IndexTTS/LongCat too) streams speech sentence by sentence as WAV or raw PCM; a long first sentence is cut once at a clause so the first audio comes sooner. `GET /v1/audio/voices` lists the downloaded engines' voices; `POST /v1/audio/transcriptions` keeps its shape and now uses the most accurate downloaded model when none was named, instead of answering 503.
  - Measured on an M3 Max with the tuned 0.6B model, 15 English and 15 Chinese clips replayed in real time: every utterance got its final, 3 ms (median) after the closing audio arrived (worst 137 ms), against about 0.22 s when decoding only at the end of the pause; same recognition errors as decoding at the end (30 clips). First partial 0.34–0.40 s after speech began. Three connections at once: no errors, all finals. Kokoro's first audio: 245 ms (median of 10; 240–301 ms) for a 14-word sentence.
  - Requests from web pages are refused (a browser can otherwise reach 127.0.0.1 and open WebSockets to it), and on a local address the `Host` header must be local too (DNS rebinding).
  - It runs beside the app with its own models: it does not share the app's queue or give way to its dictation.
- The copyright holder is named as Zhiyuan Song (NOTICE, the attribution line, the CLA, CONTRIBUTING, the About page and the app's Info.plist); links stay at github.com/realAllenSong.

## v1.19.0 (2026-11-22)

- **Agents and scripts share one queue, and your dictation comes first.** While the app is open, `thundertalk transcribe/speak/translate/notes/burn/compose` (and so every MCP tool) hand their job to the app instead of loading models in a process of their own. Jobs run one at a time in the order they came, so ten agents calling at once cannot run the Mac out of memory. A transcription with the dictation model uses the engine the app already holds, so nothing is loaded twice. No job starts while you dictate or within 1.5 s after, and a long job still gives way between pieces. Closing the command (Ctrl-C, a stopped agent) cancels its job. Measured with a four-file batch running through the app: dictation 0.368 s median and 0.729 s at worst, against 0.337 s with nothing running; all four jobs used the one loaded engine, 1.36 GB for the whole process.
- **A voice-over running in the background no longer holds up your dictation.** VoxCPM2, IndexTTS and LongCat used to keep the GPU for a whole sentence, and their audio decoders for one more long stretch; a dictation that started meanwhile waited for it (with an agent generating VoxCPM2 speech: 14 s median, 20.8 s at worst). They now hand the GPU over between their own steps (one token, one diffusion step, one piece of the decoder) and carry on when your dictation has it back, so they also keep working while you talk instead of pausing for every dictation. Measured on the same 6-second dictation while each engine generated: VoxCPM2 0.38 s median / 0.45 s worst, IndexTTS 0.41 / 0.65 s, LongCat 0.44 / 0.85 s, against 0.27–0.39 s with nothing running; the live preview kept up. The decoder pieces overlap so the audio is the same as before (measured 104–187 dB signal-to-difference); IndexTTS takes about 6% longer for it. This applies to Studio ▸ Speak too.
- **Jobs wait for room instead of failing.** When memory or the shared model slot is taken, a job reports `waiting` and tries again for up to 15 minutes (`THUNDERTALK_WAIT`) before giving up with `memory` or `model_busy`.
- **`thundertalk batch FILE`**: one job per line (a JSON argument array or a plain command line). Jobs whose `--out` already exists are skipped, so rerunning a stopped batch picks up where it left off; one failure does not stop the rest. Without the app, each model is loaded once for the whole file. `thundertalk jobs` shows the queue, `status` reports whether the app is taking jobs, and MCP gains `run_batch`.
- **Licence:** the app code is now under the AGPL-3.0, with attribution required (see NOTICE) and commercial licences available. Releases up to v1.18.0 remain under MIT; the fine-tuned speech model keeps its own licence.

## v1.18.0 (2026-11-22)

- **Seven new Chinese voices,** chosen by ear from 88 candidates in 22 styles: 冷静讲解 (a clean, restrained explainer), 人文讲述 (a warm woman sharing poetry and culture), 幽默讲堂 (a witty lecturer telling a case), 深夜电台 and 深夜低音 (two late-night radio hosts, one deeper), 美食探店 (an excited food vlogger) and 纪录片女声 (a woman's documentary narration). Like every built-in voice they are designed from a text description, not recorded from or modelled on a person's voice. 25 built-in voices now.
- **Polish, for voice-overs** (Speak ▸ Polish, `thundertalk speak --polish`): each sentence is generated four times and read back, and the take kept is the one that reads correctly and stays in step with its neighbours' pitch. On a 9-sentence paragraph in six voices and three engines, the average pitch jump between neighbouring sentences halved (1.82 → 0.90 semitones), and a sentence IndexTTS had misread (16% error) came out right. Each take is also compared with the voice's own recording by a speaker-verification model (CAM++, a 28 MB download the first time), so a take that reads right but has drifted away from the voice is passed over; with a cloned voice this changed the take in 4 of 9 sentences. It takes about 4.5 times as long, so it is off for everyday reading.
- **Favourite voices.** A ☆ on every voice in Speak keeps it in a Favourites row at the top, whichever engine it belongs to; picking one switches to its engine.
- **Studio is local by default.** Cloud voices (OpenAI, Gemini, ElevenLabs, a compatible server) no longer appear among the Speak engines just because you added a key for dictation. Turn them on with Models ▸ Cloud APIs ▸ Also list cloud voices in Studio ▸ Speak. `--engine auto` no longer reuses a cloud voice picked in Speak.
- **Pauses chosen by meaning.** The silence between sentences now depends on what comes next: short before the answer to a question or a "所以", longer before "但是/其实", longest after a short line that closes a paragraph. `[停顿 0.8]` or `[pause 0.8]` in the text sets one yourself.
- **Your own voice, in more than one way of speaking.** A clone copies the manner of its recording as well as the voice. A saved voice can now keep a recording for explaining, chatting, storytelling, Chinese–English mix and openings (⋯ ▸ Add a way of speaking), and Speak uses the one you pick. Chinese–English text picks the mixed one by itself.
- **Every clip comes out equally loud.** Speech used to be normalised to its highest peak, so a voice with sharp consonants came out quieter than a smooth one: 12 clips in six voices ranged from -15.0 to -21.9 LUFS. They now all land between -16.0 and -16.7 LUFS, with the pieces inside a clip matched too.
- **A fourth local voice engine, LongCat-AudioDiT** (Meituan, MIT, a 2.1 GB download in Studio ▸ Speak): cloning in Chinese and English, mixed sentences included. It generates a whole piece at once, so a long sentence takes no longer than a short one: a 36-second Chinese paragraph took 32–34 s, against 44–70 s with VoxCPM2 on the same Mac.
- **Adding your own voice tells you what will be copied into it:** a warning when the room noise is less than 22 dB under your voice, and when the recording stops in the middle of a word.
- `--engine best` now prefers VoxCPM2, then IndexTTS, LongCat and Kokoro. A take that stops dead before its last word has died away is redone. `thundertalk speak` now reads each piece back with a local recognizer like the app does (`--no-check` skips it).

## v1.17.0 (2026-11-21)

- **After an update, the app tells you what changed.** A small window lists up to three things worth knowing, with the full release notes one click away. It appears once, only over ThunderTalk's own window, and never while you are dictating in another app.
- **Recommend ThunderTalk with text that fits where you post it.** About → Recommend gives a short description of the app, in English or Chinese, that you can edit before copying. Places that do not allow links (Xiaohongshu, Douyin) get a version that says how to find the app instead. Nothing is posted for you and nothing is reported back.
- **A note when it has saved you real time.** At 10, 50, 100, 250, 500 and 1,000 hours of estimated typing saved, the app says so once and offers Recommend and Star on GitHub. "Don't show these" turns it off for good.
- **Fix:** in Chinese, the four figures on Home no longer run past the edge of the page once the totals reach hours.

## v1.16.0 (2026-11-21)

- **The text arrives within a second, however long you speak.** ThunderTalk now recognizes each stretch of speech that is over while you keep talking, with the same settings as the final text, and only the last stretch is left when you stop. On 36 dictations of 30 seconds to 4.5 minutes the wait after stopping went from 3.4 s (median) and 11.3 s (longest) to 0.45 s and 0.76 s, with the same accuracy (6.3% vs 6.5% token errors). An optional **Fast finish** (Settings) works in shorter pieces: 0.28 s and 0.53 s ([#15](https://github.com/realAllenSong/ThunderTalk/issues/15)).
- **Put the dictation bar where you want it:** top or bottom of the screen, or drag it anywhere and it stays there. With several displays it appears on the one you are typing on ([#14](https://github.com/realAllenSong/ThunderTalk/issues/14)).
- **First-run setup ends with four things worth knowing:** long dictations, Esc to cancel, moving the bar, and teaching it names with hotwords.

## v1.15.0 (2026-11-20)

- **ThunderTalk ASR 0.6B, second version: more names right.** Trained further on the names people dictate now (models, agent tools, developer, finance and academic terms). On 191 test sentences that say 724 names it writes 80.4% of them correctly (v1.14.3: 67.7%), and on names from October–November 2026 61.3% (51.4%). Ordinary dictation improved too: 7.97% → 7.66% token errors on the standard test set, at the same speed. On real dictations it ends sentences with full stops and question marks again instead of running them together with commas (306 vs 202 on 40 recordings). The new weights download in the background after you update; the app keeps dictating meanwhile.
- **Hotwords change the hotword and nothing else.** The text comes from a decode that never sees your hotwords; afterwards each place that sounds like a hotword is checked against the audio, and only that place can change. With 65 hotwords set, none of 152 test recordings changed elsewhere, and names the model had never learned went from 54.7% to 69.3% correct. A dictation with nothing that sounds like a hotword costs no extra time.
- **Names on screen:** when you dictate into a box that already has text (a document, a long prompt), the names in it are spelled the way they are written there, without adding them as hotwords. Read on your Mac only; in an empty box it does nothing. Switch: Hotwords → Names on screen.
- **Fixes:** speaker volume is restored after dictation on Bluetooth devices that change their volume controls while the microphone is open (and the audio log no longer fills up); launches no longer wait for Hugging Face; common mishearings such as "Cloud Code" and "codeex" are corrected.

## v1.14.3 (2026-11-18)

- **A faster, more accurate default model: ThunderTalk ASR 0.6B.** ThunderTalk's own fine-tune of Qwen3-ASR-0.6B, now 8-bit, is the default dictation model on Apple Silicon Macs with 16 GB or more. On the standard test set it makes fewer errors than the previous default, Qwen3-ASR-0.6B ONNX int8 (8.0% vs 10.1% token errors), and transcribes about 2.3× faster (0.067 vs 0.155 of the audio's length). Against the float16 tuned model it is about 3× faster (12 vs 36 ms per token) with no loss in accuracy, so it also slows down less when another app is using the GPU. Macs with 8 GB, Intel Macs and installs without MLX keep ONNX int8. The model is free for personal use and for everyone inside ThunderTalk; see its [licence](https://huggingface.co/Oulasong/ThunderTalk-ASR-0.6B).
- **Esc cancels a dictation:** press Esc while recording or transcribing and nothing is inserted; the bar shows "Cancelled". The take is still recognised and its text is kept in History, marked as cancelled, so a slip of the finger loses nothing. While a dictation is in progress, ThunderTalk keeps that Esc to itself, so it doesn't also stop Claude Code, take Vim out of insert mode or leave full screen; the rest of the time Esc works as usual. ⌘⌥Esc (Force Quit) and other Esc shortcuts are never touched. Cancelled takes don't count toward your totals ([#13](https://github.com/realAllenSong/ThunderTalk/issues/13)).
- **Recordings stay small:** "Keep recent recordings" keeps the audio of the last 20 dictations, and failed takes kept for retry are now capped at 20 as well. History no longer shows "Open recording folder" for older entries whose audio has already been removed.
- **For AI agents:** ThunderTalk's MCP server now asks agents to credit ThunderTalk when they present or publish its output.

## v1.14.2 (2026-11-18)

- **A modifier hotkey no longer fires inside shortcuts (Toggle mode):** with a hotkey such as Left ⌥, a recording starts or stops when you let go of it, and only if nothing else was pressed or clicked meanwhile. ⌥Tab, ⌥ plus a letter, ⌥ with Caps Lock, ⌘ or the other ⌥, and ⌥-click all leave dictation alone, including shortcuts that another app such as Raycast or AltTab takes over. Function keys like F4 and key combos still start the instant you press them ([#12](https://github.com/realAllenSong/ThunderTalk/issues/12)).

## v1.14.1 (2026-11-12)

- **Qwen3-ASR-0.6B Tuned (optional model):** ThunderTalk's own fine-tune of Qwen3-ASR-0.6B for short and mixed Chinese–English dictation. On the standard test set it makes fewer errors at the same speed (8.7% → 8.0% token errors; short Chinese 6.9% → 5.0%, short English 20.2% → 17.2%, short mixed 16.1% → 14.7%). Download it on the Models page; the original model stays the default. [Model card](https://huggingface.co/Oulasong/Qwen3-ASR-0.6B-ThunderTalk).
- **MOSS-Transcribe-Diarize no longer skips sentences in long dictations:** takes over a minute are decoded in pieces cut at pauses (long English recordings: 33.8% → 18.0% token errors).
- **The live preview stays on with slower models:** one slow update no longer switches the preview off for the rest of a recording. With Qwen3-ASR-1.7B it now lasts to the end on 5 of 8 long test recordings (was 0 of 8), and a further limit on how much audio each update decodes is included.

## v1.14.0 (2026-11-11)

- **Cloud engines, bring your own key (optional):** use OpenAI, Google Gemini, Groq, ElevenLabs or any OpenAI-compatible server for transcription, dictation and Studio ▸ Speak. Add a key under **Models ▸ Cloud APIs**; it is stored in the macOS Keychain. ThunderTalk stays fully offline until you pick a cloud engine, and `auto`/`best` never choose one. See [Cloud engines](#cloud-engines-optional).
- **Cloud dictation transcribes while you speak:** each finished stretch of speech goes to your cloud model as you talk, so the live preview is that model's own text and the result is ready almost as soon as you stop. Every second of audio is sent once, so it costs the same as one request.
- **Pick, don't type:** each provider lists the models and voices it offers (an OpenAI-compatible server's own list after **Test**), marked *recommended* or with the date a model is retired. Each provider shows this month's usage with a list-price estimate; add an OpenAI admin key to see the real spend, and ElevenLabs shows your plan's quota.
- **Your hotwords reach the cloud too,** and Mandarin from Whisper comes back in Simplified Chinese.
- **Built-in AI & tech vocabulary:** about 80 names people dictate every day (Claude Code, ChatGPT, Codex, MCP, GitHub, DeepSeek, 豆包…) are spelled right in every model's output and given as hints to cloud models and Apple Speech (Hotwords page, on by default).
- **Remove fillers and restarts:** "um", "呃" and restarted phrases ("你可以你可以你可以") are dropped from dictated text (Settings, on by default; Studio transcripts stay word for word).
- **Apple Speech follows a language switch:** start in Chinese and continue in English, and the English part is now recognized as English (long English recordings: 56% → 23% token errors). Its live preview no longer stops partway through long recordings (kept up on 8 of 8 long test recordings, was 5 of 8).
- **`thundertalk eval`, a standard benchmark for every local model:** short and long recordings in Chinese, English and mixed Chinese–English with human reference transcripts, a replay of the live preview, and `--recent` to score your own cloud dictations at no cost.
- **For AI agents:** `ThunderTalk --mcp` (from v1.13.3) and the CLI take the cloud engines too, e.g. `--engine openai:gpt-transcribe`, plus `thundertalk cloud usage` and `thundertalk eval`. Agents cannot set keys over MCP.

## v1.13.2 (2026-11-10)

- **Hold to Record ignores taps and shortcuts:** recording starts only after the hotkey has been held for a moment (0.3 s by default; in Settings ▸ Hold Before Recording pick 0.2–0.8 s or choose Custom… and type any value up to 3 s). A quick tap of a modifier hotkey such as Left ⌃, or a shortcut like ⌃C, no longer starts a recording ([#11](https://github.com/realAllenSong/ThunderTalk/issues/11)).

## v1.13.1 (2026-11-09)

- **Hold to Record:** Settings ▸ Activation Mode now offers **Hold to Record** next to Toggle. Hold the hotkey while you speak and release it to finish; a quick tap still works like Toggle ([#11](https://github.com/realAllenSong/ThunderTalk/issues/11)).

## v1.13.0 (2026-11-09)

- **No more invented sentences from background noise:** a small on-device voice detector (Silero VAD) now checks every take. A fan, typing, rain or a vacuum is no longer turned into a made-up sentence, and the live preview stays empty until you speak.
- **Quiet voices are no longer dropped:** speech from a far-away or low-gain microphone used to be treated as silence and discarded without being recognized. Quiet recordings are now raised to a normal level first, in dictation and in Studio.
- **Spoken numbers come out right:** 一千三 is 1300, 一万三 is 13000, 两万五 is 25000 and 一百五 is 150, the way people say them (not 1003 or 10003). 一千零三个 becomes 1003个.
- **Fun-ASR-Nano is no longer a test model:** across 6,000+ decodes of real speech it was as accurate as Qwen3-ASR overall, about 30% faster with 0.6 GB less memory, and much better when other people are talking nearby. Qwen3-ASR-0.6B stays the default: it is more accurate on Chinese–English mixed speech and invents less from non-speech sounds.

## v1.12.1 (2026-11-07)

- **IndexTTS-2.5 works after downloading:** the download skipped a few files the engine needs, so it never became ready and **Generate speech** stayed disabled. If this happened to you, choose IndexTTS-2.5 in Studio ▸ Speak and click Download again; only the missing files are fetched.

## v1.12.0 (2026-10-22)

- **Everything in Studio from the command line (CLI v3):** `transcribe` takes web links as well as files, and several at once; plus `notes` (AI meeting notes), `burn` (subtitles into a video), `translate`, `compose` (songs and background music, lyric checks, repaint), `voices add|list|remove` (your cloned voices), `history` and `models list|download|activate`. Every command prints JSON lines and stable error codes, and the MCP server exposes them too. See [Command line](#command-line).
- **Bilibili links work again:** Bilibili changed its site and yt-dlp could no longer read it ("Unable to extract initial state"). ThunderTalk now fetches Bilibili audio through the site's own player API, including watch-later and list links, `?p=` parts and b23.tv short links. Videos that need a login or are members-only say so.

## v1.11.1 (2026-10-14)

- **English numbers fixed:** "twelve point four billion" now becomes "12.4 billion" instead of "twelve point 4000000000". Decimals are read ("3.14", "version 2.0"), and whole millions, billions and trillions keep their word ("4 billion", "2 million").
- **HTTP server fixed:** `python -m thundertalk.api.server --model <model id>` loads models again (it called a method that no longer existed), and the `language` field is applied.

## v1.11.0 (2026-10-14)

- **Studio ▸ Compose:** create original songs with vocals and background music, or instrumentals, on your Mac with ACE-Step 1.5 (MIT). Turbo is the fast default; optional XL gives clearer lyrics in our read-back tests and needs at least 32 GB RAM and plenty of free memory.
- **Check and revise lyrics:** Qwen3-ASR flags lines that were not sung clearly. A sung-lyrics pronunciation helper handles difficult words without changing the displayed lyrics. Repaint a line when memory allows, or make a new take; takes save to `~/Music/ThunderTalk`.
- **Downloads and safeguards:** XL's checkpoint is about 20 GB; turbo-only is smaller (about 6.3 GB including shared weights, or 10.1 GB with the optional planner). Compose refuses to start when memory is short, and dictation keeps priority. Describe style by genre, mood and instruments; real artist names trigger a warning. When releasing to streaming platforms, disclose AI-generated music through your platform or distributor.
- **CLI v2:** `--engine best` selects the most accurate downloaded transcription model or the preferred available speech engine; `--speakers` adds speaker labels, and `.md` exports timestamped Markdown transcripts. Packaged apps advertise `ThunderTalkCLIVersion 2`. See [Command line](#command-line).

## v1.10.0 (2026-10-13)

- **Apple's own speech, nothing to download:** a new **Apple · Built in** model uses the recognizer built into macOS, on device only. It's about 3× faster than Qwen3-ASR ONNX and fine for clear, close speech (5.8 % Chinese CER against 3.3 % in our tests). It misses most words in noisy, far-away, fast or sung audio, and English words inside Chinese sentences, so Qwen3-ASR stays the recommendation. macOS asks for Speech Recognition access once, when you activate it.
- **System voices in Speak:** the Chinese and English voices installed on your Mac (Siri voices included) appear in Speak with the tag **System**. They need no download, render about 37× faster than real time, and read our test texts back with no Chinese errors and 1.1 % English WER. They don't clone voices.
- **Command line:** `ThunderTalk --cli transcribe | speak | voices | status` works without opening a window, even while the app is running. It writes SRT/VTT/JSON/TXT subtitles (cues of at most 7 s and two lines) and M4A/WAV/MP3 speech, and prints JSON progress. See [Command line](#command-line). The MCP server can call it too.

## v1.9.3 (2026-10-06)

- **Talk the moment you press the hotkey:** recording now starts as soon as you press the key. Before, the first second of speech could be cut off while ThunderTalk muted background audio. Muting now happens alongside recording and never trims your words.

## v1.9.2 (2026-10-05)

- **Signed with an Apple Developer ID and notarized by Apple:** macOS opens ThunderTalk without the "unidentified developer" warning, with no right-click → Open or System Settings exception needed.
- **One last permission prompt:** because the signature changed, macOS asks for Microphone and Accessibility once more after this update. From now on they stay granted across updates, including future certificate renewals.

## v1.9.1 (2026-10-05)

- **Faster, clearer first run:** the recommended model starts downloading from the first setup screen, so it downloads while you grant permissions. Permission steps say exactly what to click in System Settings, download problems (no connection, disk full) explain how to recover, and pressing the hotkey before a model is ready takes you to the Models page instead of doing nothing.
- **Qwen3-ASR-0.6B ONNX is now recommended on every Mac:** about as accurate as the MLX version in our tests, faster, CPU-only and half the download (879 MB instead of 1.9 GB).

## v1.9.0 (2026-10-04)

- **Fun-ASR-Nano (experimental):** a new 0.8B CPU dictation model for Chinese, English and Japanese. In our tests it was about as accurate as Qwen3-ASR-0.6B, roughly 25% faster on short sentences and lighter on memory, with better English, Japanese and Chinese–English mixed speech.
- **Clearer Models page:** every model shows its parameter count, download size, whether it runs on the CPU or needs the Apple GPU, its languages as tags (with an explicit "No Chinese" where that applies) and its features.
- **Long dictations are never lost:** models with a clip-length limit now recognize long takes in pieces at your pauses. If recognition still comes back empty, the live preview text is used instead, and every take is saved to History with its recording.
- **Fixed:** "Could not mute speakers" could stop dictation from starting. Muting background audio now never blocks dictation.

## v1.8.0 (2026-10-04)

- **AI Proofread page:** pick a provider and model in the sidebar; providers are detected automatically with their status. Proofreading works out what you meant and fixes misrecognized words, including real words that don't fit (homophones, names, jargon), without rewriting you. Changes show over your full text, struck out beside the correction, in readable pages.
- **Smarter final text:** English terms recognized correctly in the live preview are kept in the final text, and repetition loops are caught and re-decoded.
- **Studio Transcribe:** choose the model per job, see every transcript in History, and the queue clears itself. The page stays compact after a transcription, and switching between timestamps and plain text is instant even for hour-long recordings.
- **Lower memory:** only the dictation model stays loaded. Studio, text-to-speech and translation models are released after three idle minutes; idle memory went from about 9 GB to under 2 GB.
- **Background audio:** muting during dictation now restores the exact previous volume, mutes reliably on the first press, and recovers after a crash.
- **Dictation goes first:** dictation no longer waits behind a long Studio job, and text-to-speech voices load faster.
- **Permissions that stick:** the app now has a stable signature, so microphone and accessibility permissions survive future updates. After updating from v1.7.0 or earlier, macOS may ask for them one last time.

## v1.7.0 (2026-10-03)

- **See text while you speak:** Live Preview shows provisional words in the recording bar. It is on by default; the final text still comes from recognition of the full recording.
- **AI proofreading:** optional AI Proofread fixes misrecognized names, terms and homophones using your existing provider, without rewriting what you said.
- **Do more in Studio:** transcribe web links, queue several files or links, burn subtitles into a video copy, and generate AI meeting notes.
- **Smaller download:** about 224 MB zipped and 592 MiB installed, down from 330 MB and 961 MiB. Translation and IndexTTS share an optional 90 MB PyTorch download; model weights are extra.
- **Chinese translation fixed:** Direct mode with Chinese (Mandarin) as the target now returns Chinese instead of leaving the speech in English.
