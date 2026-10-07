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
