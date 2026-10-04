# Model facts and Task V CPU engines

Facts describe the shipped variant and capabilities exposed by ThunderTalk,
not every feature of an upstream research pipeline. Download sizes are
compressed archives or the existing filtered HF downloads, not RAM estimates.
Speed notes for existing engines retain the app's M3 Max measurements. New
engines show “Speed not measured” because a short smoke test is not a general
performance claim. The default dictation and cloning engines are unchanged.

## Official sources (checked 2026-10-04)

| Model | Parameters / language evidence |
| --- | --- |
| [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | 0.6B / 1.7B; 30 named languages plus 22 Chinese dialects, represented separately from language tags. |
| [Parakeet v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) | 0.6B; v3's 25 named European languages; v2 English only. Neither supports Chinese. |
| [SenseVoice Small](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) | 234M; Mandarin, Cantonese, English, Japanese, Korean. The broader SenseVoice family has different coverage. |
| [MOSS](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize) | 0.9B; 50+ languages claimed, but no full language list. Tags list Chinese, English and its 14 named challenge languages without inventing the remainder. The tooltip states this limitation. Upstream custom hotword prompting is not exposed by this app's MOSS loader. |
| [SeamlessM4T v2](https://huggingface.co/facebook/seamless-m4t-v2-large) | 2.3B; tags follow the card's source **speech** column, including script variants. Source text, target text and target speech coverage differ. CPU fallback and Apple GPU via MPS. |
| [Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512), [sherpa release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) | 0.8B; Chinese, English and Japanese. This is Nano, **not** MLT-Nano's broader multilingual checkpoint. Hotwords use the factory's comma-separated input. |
| [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), [shipped v1.1 export](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | 82M; this v1.1 multi-lang archive exposes Chinese and English speakers. Other Kokoro checkpoints have different coverage. |
| [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | 2B; 30 named languages. MLX port; cloning supported. |
| [IndexTTS-2.5](https://huggingface.co/IndexTeam/IndexTTS-2.5) | Approximately 0.8B **GPT backbone**, explicitly labeled as such rather than a whole-engine count; Chinese, English, Japanese, Spanish, Arabic. |

Parakeet raw token start timestamps are preserved in `AsrResult`
and counted in the ASR selftest; Studio continues to use sentence spans.
Fun-ASR-Nano's uniformly distributed estimated token times are not advertised
or returned as native alignment. Its occasional standalone `/sil` marker is
removed from dictated/transcribed text.

Fun-ASR-Nano remains labeled as a test model. It uses Studio's existing
silence-based, bounded 18-second chunking and sentence-span timestamps.
No word-level alignment or speaker labels are promised. Dictation remains
resident; Studio alternate engines hold a memory-policy lease and unload
after the job. No package upgrade is required: the installed/locked
sherpa-onnx 1.12.38 provides the Fun-ASR-Nano factory.

Saved settings referencing retired ASR variants resolve to the usual
hardware-recommended Qwen3-ASR 0.6B variant. Retired Speak engines resolve to
VoxCPM2, and retired built-in voices resolve to the normal default voice.
Saved personal voice references are preserved. Preference recovery does not
download or delete any model files; the ASR default loads only if available.

## Reproducing verification

Run from this worktree. Keep HOME changes **inside the locked child command**;
`lock.py` itself must resolve its machine-wide lock in the real HOME.

```sh
python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- env \
  HOME="$PWD/artifacts/models-v2/home" PYTHONPATH="$PWD" \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  BENCH_MODELS="$HOME/Library/Caches/ThunderTalk-bench/models-bench" \
  ~/Desktop/ThunderTalk/.venv/bin/python tools/verify_models_v2.py
```

The script accesses benchmark model folders read-only, uses public FLEURS
Chinese/English and ASCEND mixed-language clips already cached there, verifies
Fun-ASR-Nano dictation and long Studio chunking. Raw outputs and timings
are saved under `artifacts/models-v2/` (ignored by git).

Packaged app checks (run through the same GPU lock):

```sh
ThunderTalk --selftest asr --model funasr-nano-int8 --file public.wav
```

ASR additionally accepts `--model-dir /read-only/cache/folder`. Explicit
selftest requests for unknown or missing models fail clearly. ASR checks exercise
dictation and Studio and release the engine on success or failure. The reviewer will run frozen-app
checks after building; no bundle is produced by this feature task.

`tools/render_models_v2.py` writes 1200×800 EN/ZH offscreen captures without
loading models. The app's Paper & Ink palette is explicitly light-only
(`theme.force_light`); it has no dark theme to capture.

## Final dictation windows and recovery

`max_clip_seconds` is an app decode-window policy, **not a recording-length
limit**. Final dictation uses the same adaptive pause cuts as Studio, keeps
all audio spans, and adds 150 ms of context at each edge within the bound.
Matching word/CJK suffixes and prefixes are removed only at overlapping
speech boundaries; repetitions across quiet pauses remain intact.

- **Fun-ASR-Nano ONNX:** 20 s and 64 UTF-8 bytes of hotwords. The shipped
  `llm.int8.onnx` metadata has `max_total_len=512`. The
  [sherpa implementation](https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/csrc/offline-recognizer-funasr-nano-impl.cc)
  allocates that cache for prompt, audio and generation together and truncates
  audio placeholders on overflow. An upper audio-token rate and prompt
  reserve also detect possible output saturation; uncertain cases retry in
  smaller windows. These estimates deliberately prefer recovery over a
  partial transcript. The Python wrapper does not expose its stderr-only
  context warning as a structured flag.
- **Qwen3-ASR ONNX:** 20 s / 64 hotword bytes in both memory modes. Its
  [sherpa decoder](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.38/sherpa-onnx/csrc/offline-recognizer-qwen3-asr-impl.cc)
  also has prompt/audio KV limits, plus output-token limits (this app requests
  1024/256 in low mode and 4096/2048 in high mode). The bounded policy avoids
  depending on a universal two-minute context window.
- **Qwen3-ASR MLX:** 30 s / 512 hotword bytes, keeping the app's per-clip
  generation budget away from its 4096-token ceiling. This is a conservative
  application window, not the upstream model's long-audio maximum.
- **SenseVoice:** 30 s, matching its model card's
  [direct inference limit](https://huggingface.co/FunAudioLLM/SenseVoiceSmall).
- **Parakeet:** 30 s as a memory/concurrency policy. Its
  [upstream card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  supports much longer input with appropriate attention/memory; there is no
  evidence for a Fun-ASR-style 512-token cap in this transducer.
- **MOSS:** 5400 s, matching the published
  [90-minute single-pass support](https://huggingface.co/OpenMOSS-Team/MOSS-Transcribe-Diarize).
  Its speaker-aware native decoding remains intact within that window.

Hotwords are kept whole, in configured order; oversize words are skipped.
A byte bound is conservative for byte-BPE tokenizers and requires no extra
package. The original configured list is preserved for editing and history.

All final ASR workers retry empty or possibly truncated speech in ≤8 s
windows, then use the last clean preview if necessary. The final-result
handler applies the same gate to other workers (including translation).
Preview recovery is explicitly labeled in History. Speech without any
recoverable text gets a failed history entry rather than “No speech”.
Only silent audio with no preview gets that message.

History includes `recognition_source` and `recording_path`; legacy entries
load with defaults. Failed, partial, preview-recovered or re-decoded takes
are always retained privately in `recordings/recovery/`, including when
normal recording capture is disabled. This recovery directory is excluded
from the normal 20-take rotation. History exposes an “Open recording folder”
action so the take can be imported into Studio. No recovery audio is uploaded.

For a private real regression, use the machine-wide lock and an isolated
HOME, then run `tools/verify_long_dictation.py --audio /local/take.wav
--models-dir /read-only/models`. It checks 20 s edge clips and the complete
take with Fun-ASR-Nano and Qwen ONNX, with a stress hotword prompt. Its output
contains transcript edges, so keep logs private and untracked.
