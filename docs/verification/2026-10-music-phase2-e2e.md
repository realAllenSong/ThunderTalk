# Music Phase 2 — real job through `MusicProcess` (2026-10-06)

`tools/music-spike/gate-e2e.sh` → `tools/music-spike/e2e.py`: turbo (8 steps), planner 1.7B (MLX),
MLX DiT, bf16, English vocal with the spike's original lyrics, 20 s, 2 takes (seeds 1234, 1235),
run under `lock.py gpu` with the venv-music python (transformers 5.6.2 + vendored patches).

| | |
|---|---|
| Outcome | `done`, exit 0 |
| Wall (spawn → child exit) | 41.7 s; load 22.7 s; take 1 10.2 s, take 2 7.8 s (DiT 1.43 s, MLX VAE 1.02 s) |
| Child peak phys_footprint (0.5 s samples) | 17.74 GB |
| Parent peak footprint | 25 MB (no torch in the parent) |
| Child after `run()` returned | gone (`pid is None`) |
| Events seen | `phase` load → plan → render → write per take, `tokens`, `step`, `take` ×2 |
| Files | `take-{1,2}.wav` 48 kHz stereo PCM_16 20.0 s, WAV comment "AI-generated with ACE-Step 1.5 in ThunderTalk"; `take-N.json`; `take-N.latents.npy` ([500, 64] f32) |

## Phase 4 — Chinese take with sentence timestamps

`tools/music-spike/gate-stamps.sh`: same pipeline, `--lang zh` (sung lyrics from the pronunciation helper,
欧拉SONG → 欧拉宋), 30 s, 2 takes, planner, MLX DiT.

| | |
|---|---|
| Outcome | `done`, 43.5 s wall; takes 12.4 s / 10.1 s; child peak 17.78 GB |
| `lyric_timestamps` | `null` on both takes: `handler.get_lyric_timestamp` calls `model.decoder`, which the MLX DiT init sets to `None` after converting the weights (`TypeError: 'NoneType' object is not callable`) |

Decision: the worker now skips alignment when the torch decoder is gone (no traceback, no wasted pass);
MLX-DiT takes are checked with the lyric check's scan-and-assign fallback. Keeping a second (torch) copy of
the DiT only for alignment would add several GB to an already 17.8 GB job. The MPS DiT path (16 GB tier)
keeps sentence timestamps — `tools/music-spike/gate-stamps-mps.sh` checks it.
