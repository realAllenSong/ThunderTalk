# Music Phase 0 gate — ACE-Step 1.5 turbo on Apple Silicon

Status: passed — memory resolved, vocals intelligible on transformers 5.6.2 (see "Vocals").

Machine: Apple Silicon Mac (shared, other work running), macOS 15 (darwin 24.6). torch 2.11.0, MLX,
transformers 5.6.2 (bundled) and 4.57.6 (private fallback copy). Model: `acestep-v15-turbo` (bf16),
8 diffusion steps, 1.7B 5 Hz planner on MLX, seed 42. Harness: `tools/music-spike/run.py` via
`gate.sh` (one disposable spawned worker, behind `lock.py gpu`; aborts if free memory < 15 % twice).
Raw logs (gitignored): `docs/verification/music-gate-*.log`, results under `artifacts/music-spike/`.

## Memory

Peak = worker physical footprint (`proc_pid_rusage`), GB.

| run (30 s vocal + planner) | load | planning | diffusion | VAE decode | result |
|---|---|---|---|---|---|
| 5.6.2, MPS DiT, MLX VAE tile 192 | 10.8 | 13.0 | 13.1 | 25.8 | aborted (< 15 % free) |
| 4.57.6, MPS DiT, MLX VAE tile 192 | 10.4 | 14.8 | 14.9 | 30.8 | aborted |
| 5.6.2, MLX DiT, MLX VAE tile 192 | 14.4 | 13.8 | 15.6 | 32.5 | aborted |
| 5.6.2, MPS DiT, MLX VAE 192, **MLX cache 1 GB** | 10.8 | 13.0 | 13.1 | **17.9** | done, 99 s wall |
| same, no `mx.compile` | 10.2 | 13.0 | 13.1 | 17.0 | done, 99 s |
| same, fp16 VAE | 10.8 | 12.7 | 12.8 | 15.6 | done, 109 s |

Per-tile trace (cache-capped run): at decode start torch MPS holds 11.3 GB (DiT + text encoder),
MLX active 4.4 GB (planner + VAE weights). Across 13 tiles MLX active stays 4.4–4.6 GB and the MLX
cache 1.07–1.13 GB; MLX peak 9.2 GB. Without the cap the MLX buffer cache grew by 13–17 GB during
decode — that, not the decode itself or the transformers version, was the blocker.
VAE decode time for 30 s: 5.2 s compiled, 14.9 s uncompiled, 12.0 s fp16.

Decisions:
- The worker calls `mx.set_cache_limit(1 GB)` before loading anything; keep `mx.compile` on.
- MLX VAE with 192-frame tiles, fp32 (fp16 saves ~2 GB but changes the output a lot — see Vocals).
- PyTorch MPS DiT (the MLX DiT path used ~2.5 GB more at load and gave no memory win).
- transformers version does not affect memory; ship the bundled 5.6.2 unless the vocal check differs.

## Vocals

ASR check: `tools/music-spike/asr_check.py` (Qwen3-ASR ONNX, English, 15 s windows). Control: the
lyrics spoken by macOS `say`, mixed into the same take at equal RMS.

Before the fix, every 5.6.2 take (MLX VAE compiled / uncompiled / fp16, PyTorch VAE) and the
10 s instrumental read back empty, while the control read back almost verbatim on every take
("Morning light upon the water. Footsteps on an empty road. We can build a little shelter from the
kindness that we saw. …"). So the checker hears words over these beds and the takes had no
intelligible vocal. The takes also differed strongly with the same seed (spectral centroid 312 /
2274 / 2378 Hz for compiled / uncompiled / fp16 decode).

Root cause: the run logs every `Qwen3RotaryEmbedding.inv_freq`. Under 5.6.2 all five (decoder,
lyric encoder, timbre encoder, tokenizer pooler, detokenizer) were **all zeros**; under 4.57.6 they
match `1 / 1e6^(2i/128)` (the config's `rope_theta`). transformers 5 builds non-persistent buffers
on the meta device and refills them in the base `_init_weights`; the vendored ACE-Step override did
not call it. Fixed in the vendored models (and `vendor.py`), regression test
`tests/test_music_vendor.py` (fails without the patch: 7/8 frequencies wrong; passes with it, turbo
and XL; since folded into `test_reloaded_model_keeps_weights_and_rebuilt_buffers`).

PyTorch MPS VAE instead of MLX (unfixed 5.6.2, same settings): peak 13.8 GB vs 15.9 GB, wall
103 s vs 67 s.

Positive control — transformers 4.57.6 (correct rotary), same settings: raw read-back "Morning light
upon the water. Footsteps on an empty road. We can build a little shelter, kind of like we show.
Carry light across the river. … let the quiet garden grow. Every …", word recall 0.83 (control 0.94).
Unfixed 5.6.2 in the same chain: 0.00. Peak 21.0 GB, 84 s wall.

Fixed 5.6.2 (`tools/music-spike/gate-fixed.sh`): the rotary buffers now log the correct
frequencies, but both takes still read back **empty** (MPS DiT: peak 14.9 GB, 65 s wall, −15.0 LUFS,
loudness range 1.2 LU; MLX DiT likewise empty) while the control reads back at 0.97 / 0.94. The 4.57.6
take has a loudness range of 12.6 LU, the fixed 5.6.2 take 1.2 LU — a flat bed. So the zero rotary was
a real bug but not the only 5.x difference.

State diff (`tools/music-spike/gate-statediff.sh`: tokenizer ids, Qwen3-Embedding output and every
DiT parameter/buffer under both versions, CPU): tokenizer ids and all 310 text-encoder tensors are
identical. But **644 of 677 DiT parameters differ**: on 5.6.2 every RMSNorm weight sums to exactly its
width (all ones) and every projection has the statistics of N(0, 0.02) — the checkpoint is loaded
("677/677") and then re-randomised. The FSQ quantizer buffers (`_levels`, `_basis`, `scales`,
`soft_clamp_input_value`, `implicit_codebook`) are zero too. Cause: transformers 5 calls
`_init_weights` on every module after loading and relies on its guarded `init.*` helpers to skip
loaded tensors; the vendored override wrote through `.data`, bypassing the guard. Fix: the override
now delegates to the base initializer (same scheme as upstream) and rebuilds the FSQ constants from a
fresh CPU `ResidualFSQ` (commit da760f1). Regression test
`test_reloaded_model_keeps_weights_and_rebuilt_buffers` save/reloads a lyric encoder and audio
tokenizer (turbo + XL) and checks every parameter and buffer: red on the old patch, green now.

After the fix (`tools/music-spike/gate-weights.sh`, 5.6.2 dump diffed against the 4.57.6 dump):
**all 677 DiT parameters and all 310 text-encoder tensors identical**. Left over: `original_inv_freq`
(a buffer only transformers 5 registers) and the FSQ `implicit_codebook`, which differs only by bf16
rounding (|x| sum 224 887 vs 224 700: built in fp32 and cast, vs built under the bf16 default dtype).

Vocal takes after the fix (same chain, 30 s, planner on, MLX VAE tile 192, MLX cache 1 GB):

| 5.6.2 fixed | raw recall | control | peak GB | wall s | LUFS | LRA LU |
|---|---|---|---|---|---|---|
| PyTorch MPS DiT | 0.71 | 0.97 | 14.5 | 24.6 | −14.0 | 6.2 |
| MLX DiT | **0.97** | 1.00 | 15.6 | 30.5 | −15.2 | 7.6 |
| (4.57.6 MPS DiT, earlier) | 0.83 | 0.94 | 21.0 | 84 | | 12.6 |

MLX DiT read-back: "Morning light upon the water. Footsteps on an empty boat. We can build a little
shelter from the kindness that we sow. … Carry light across the river. Let the quiet garden grow.
Every day a new beginning. Every seed a place to go." Wall times here are lower than the earlier
rows because the machine was less loaded (same code path otherwise); compare within a row group.

Decisions after the gate:
- Ship the bundled transformers 5.6.2 with the vendored patches; no private 4.57.6 copy.
- Default DiT backend: MLX when available (best read-back, +1.1 GB peak), PyTorch MPS DiT as the
  fallback / low-memory path (14.5 GB peak). Both keep `mx.set_cache_limit(1 GB)`.
- bf16 weights throughout (turbo checkpoint is bf16); the fp16 VAE observation above was made on
  the broken weights and is not re-tested — keep the VAE in fp32.
