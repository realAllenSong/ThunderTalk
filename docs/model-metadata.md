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
| [FireRedASR2 repo](https://github.com/FireRedTeam/FireRedASR2S), [paper](https://arxiv.org/html/2603.10420v1), [AED card](https://huggingface.co/FireRedTeam/FireRedASR2-AED) | Chinese / English and code switching. No explicit parameter count for these exports found in those sources (the HF config.yaml is empty); the count is left empty and the UI says unpublished. The CTC archive README says it exports the AED encoder and CTC head only, so an AED total must not be assigned to CTC. |
| [Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512), [sherpa release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) | 0.8B; Chinese, English and Japanese. This is Nano, **not** MLT-Nano's broader multilingual checkpoint. Hotwords use the factory's comma-separated input. |
| [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), [shipped v1.1 export](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | 82M; this v1.1 multi-lang archive exposes Chinese and English speakers. Other Kokoro checkpoints have different coverage. |
| [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | 2B; 30 named languages. MLX port; cloning supported. |
| [IndexTTS-2.5](https://huggingface.co/IndexTeam/IndexTTS-2.5) | Approximately 0.8B **GPT backbone**, explicitly labeled as such rather than a whole-engine count; Chinese, English, Japanese, Spanish, Arabic. |
| [ZipVoice](https://github.com/k2-fsa/ZipVoice), [paper](https://arxiv.org/abs/2506.13053), [sherpa usage](https://github.com/k2-fsa/sherpa/blob/master/docs/source/onnx/tts/zipvoice.rst) | 123M; Chinese / English; CPU zero-shot cloning requires reference audio **and its exact transcript**. |

The FireRed native AED pipeline has alignment timestamps; sherpa's selected
encoder/decoder export does not expose that additional CTC alignment branch.
CTC and Parakeet raw token start timestamps are preserved in `AsrResult`
and counted in the ASR selftest; Studio continues to use sentence spans.
Fun-ASR-Nano's uniformly distributed estimated token times are not advertised
or returned as native alignment. Its occasional standalone `/sil` marker is
removed from dictated/transcribed text.
These new ASR entries use Studio's existing silence-based, bounded
18-second chunking and sentence-span timestamps. No word-level alignment or
speaker labels are promised. Dictation remains resident; Studio alternate
engines hold a memory-policy lease and unload after the job. ZipVoice reuses
SpeechEngine's preload and idle-release policy.

ZipVoice's 109 MB archive needs the separate 54 MB `vocos_24khz.onnx` vocoder
from sherpa's `vocoder-models` release. Both downloads appear in its total
(163 MB). No new installed package or package upgrade is needed. The Python
wrapper minimum is tightened to the already installed/locked sherpa-onnx
1.12.38, which provides all required factories and the cloning overload.

## Built-in reference audio provenance and redistribution

ZipVoice reuses only `assets/voices/*.wav` and the exact transcripts in
`assets/voices/voices.json`. These are existing ThunderTalk-created synthetic
voices, designed from descriptions with Apache-2.0 VoxCPM2 by
`tools/make_preset_voices.py`; see `tts_backends/presets.py` for their provenance.
They are original project assets distributed under the repository's MIT
license (`LICENSE`), not recordings of people or upstream demo voices.
ZipVoice-generated previews in `assets/voices/previews/zipvoice/` are derived
only from these same project assets using Apache-2.0 ZipVoice. This introduces
no recording licensed by a third party and no new personal data.

No audio from `~/.thundertalk/voices` or `~/Downloads/sample.m4a` is used or
shipped. The normal “My voices” dialog transcribes the prepared reference with
the active dictation ASR, permits editing before saving, and requires nonempty
reference text. Existing saved references can be edited from their menu.

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
long Studio chunking, synthesizes Chinese/English/mixed speech from project
synthetic references and writes ZipVoice previews. Raw outputs and timings
are saved under `artifacts/models-v2/` (ignored by git).

Packaged app checks (run through the same GPU lock):

```sh
ThunderTalk --selftest asr --model fireredasr2-ctc-int8 --file public.wav
ThunderTalk --selftest asr --model fireredasr2-aed-int8 --file public.wav
ThunderTalk --selftest asr --model funasr-nano-int8 --file public.wav
ThunderTalk --selftest tts --engine zipvoice
```

ASR additionally accepts `--model-dir /read-only/cache/folder`. TTS resolves its
archive and vocoder under the selected HOME's model directory. Unknown or
missing models fail explicitly. ASR checks exercise dictation and Studio and
release the engine on success or failure. The reviewer will run frozen-app
checks after building; no bundle is produced by this feature task.

`tools/render_models_v2.py` writes 1200×800 EN/ZH offscreen captures without
loading models. The app's Paper & Ink palette is explicitly light-only
(`theme.force_light`); it has no dark theme to capture.
