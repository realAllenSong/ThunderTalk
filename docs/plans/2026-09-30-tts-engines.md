# Speak: three engines replace Qwen3-TTS

## Why

Qwen3-TTS (1.5.x) was stable after the fixes in `2026-09-28-tts-studio.md`, but
in daily use it still sounded uneven and offered only nine voices. The request:
support IndexTTS and VoxCPM (latest versions) and Kokoro, with built-in voices
and cloning of the user's own voice.

## What ships

| Engine | Weights | Runs on | Built-in voices | Cloning |
|---|---|---|---|---|
| VoxCPM2 (OpenBMB, Apache-2.0) | `mlx-community/VoxCPM2-8bit`, 3.2 GB | Apple GPU via mlx-audio | 8 shared presets | yes (reference + transcript) |
| IndexTTS-2.5 (bilibili licence) | `vanch007/mlx-indextts2-2.5-8bit` 1.7 GB + `facebook/w2v-bert-2.0` 2.3 GB | Apple GPU via vendored `third_party/mlx_indextts` | 8 shared presets | yes |
| Kokoro v1.1 (Apache-2.0) | sherpa-onnx `kokoro-multi-lang-v1_1`, 364 MB | CPU (ONNX) | 23 curated of 103 | no |

`core/speech.py` runs the existing pipeline (piece planning, duration band,
ASR read-back, retries, level matching, joining, speed) over a backend
interface (`core/tts_backends/base.py`). The Qwen3-TTS engine in `core/tts.py`
is kept but no longer offered.

**Built-in voices.** Neither cloning engine has named speakers, and none of the
68 prompt clips on the two official demo pages is licensed for redistribution
(several imitate public figures or come from games). The 8 presets in
`assets/voices/` were designed by VoxCPM2 from text
descriptions (no real person) with fixed seeds, checked by
`tools/verify_preset_voices.py` (read-back 0 %, pitch matches the labelled
gender), and are used by both VoxCPM2 and IndexTTS.

## Measured (M3 Max, same texts, independent Qwen3-ASR 1.7B read-back)

- Read-back error: 0–3.4 % on Chinese, English and mixed passages for every
  engine and voice, except Kokoro's Chinese voices on mixed text (8–16 %: e.g.
  "meeting" → 密林) — Kokoro is best for pure Chinese or pure English.
- Pitch stability (20-s windows, semitones): within the natural read-speech
  range for all three (sd 2.2–4.4, block swing 0.2–2.1).
- Speed: Kokoro ~4× real time on the CPU; VoxCPM2 and IndexTTS ~1× on the GPU.
- Cloning the maintainer's 3.6 s recording: speaker similarity 0.97–0.98 for
  both VoxCPM2 and IndexTTS (same-speaker bound for that clip: 0.967), read-back
  0 % on the three test lines in the packaged app.

## Mac-specific findings

- The IndexTTS MLX port turns anything longer than a sentence or two into a
  quiet ~400 Hz hum: fp16 GroupNorm statistics in S2Mel. Fixed by upstream PR #5
  (float32 accumulation), applied in `third_party/`.
- The official IndexTTS on PyTorch/MPS is reported slow (RTF 3–5) and crash-prone
  on M3; the MLX port is the practical route.
- VoxCPM2 voice design produces a new speaker per call, so a designed voice is
  pinned to one reference clip. Reference arrays must be at 48 kHz.
- Duration floor: fast voices (and clones of fast speakers) run below the
  estimate; with the read-back verifier active the floor drops from 0.72 to
  0.45 and the verifier decides. This removed false retries (112 s → 42 s and
  140 s → 47 s on two long passages).
- Packaging: VoxCPM2 (mlx-audio) and IndexTTS need SciPy, which the bundle used
  to exclude; PyInstaller 6.19's SciPy hook predates SciPy 1.18's move of
  `array_api_compat` to `scipy._external`, so that module is named explicitly.
  wetext's data (`contractions`, `anyascii`) is collected too. All three engines
  and cloning pass `--selftest` inside the packaged app.
