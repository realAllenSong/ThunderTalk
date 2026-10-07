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

`gate-stamps-mps.sh` (same job, `--dit mps`): outcome `done`, 39.8 s wall; takes 12.8 s / 11.2 s (alignment
included); child peak 16.65 GB. `lyric_timestamps` came back on both takes (8 sentences = 2 tags + 6 lines,
so they map one to one), but every confidence is 0.0 and the spans are not usable: take 1 gives the first
line 0.00–0.00 s; take 2 gives line 2 12.8–29.9 s and piles the last four lines up at 29.92 s with zero length.

Decision: `music_lyrics.line_spans` now rejects any line shorter than 0.3 s (`MIN_SPAN_S`), so takes like
these fall back to scan-and-assign. Sentence timestamps stay an optional hint; the scan path is the one
the lyric check relies on (both DiT backends).

## Lyric check calibration — Codex handoff

`gate-check.sh` completed with **check exit 0**, after 74 minutes of lock
headroom waiting. Qwen3-ASR ONNX loaded in 6.9 s; the check job ran 78.87 s
with peak physical footprint 3,003,392,640 bytes (2.80 GiB).
Raw log: `artifacts/music-spike/e2e/check-takes.log`.

| Take | Clear / total at 0.35 | Check s | Clear / total at calibrated 0.20 |
|---|---:|---:|---:|
| zh-stamps/take-1.wav | 4 / 6 | 12.6 | 2 / 6 |
| zh-stamps/take-2.wav | 1 / 6 | 14.5 | 1 / 6 |
| zh-stamps-mps/take-1.wav | 3 / 6 | 13.1 | 2 / 6 |
| zh-stamps-mps/take-2.wav | 2 / 6 | 11.6 | 1 / 6 |
| turbo-planner/take-1.wav | 0 / 8 | 9.5 | 0 / 8 |
| turbo-planner/take-2.wav | 4 / 8 | 9.7 | 4 / 8 |

The last column is a re-score of the **same recorded errors**, not another
model run. The 0.35 threshold hid important corruption inside otherwise
correct long lines: 一行公式藏着星光 → 遗憾故事藏着星光 (0.25), and
原文里有你的名字 → 眼眼里有你的名字 (0.25). Both now flag at **0.20**;
regression tests cover those exact full-line cases. Homophones still pass;
a one-word error in six English words (0.167) still passes. The 20 s English
takes cannot fit all eight requested lines: the four absent closing lines
correctly show not heard. These six takes show real seed/ASR variance and
were not approved by ear. A separate check of the intelligible Phase-0
30 s reference is queued at `artifacts/music-spike/e2e/check-reference.log`.

The remaining Phase 4 UI is implemented and fake-tested: sequential
LyricCheckWorker jobs begin only after MusicWorker.finished (the child has
exited); resident Qwen dictation settings are unchanged, temporary Qwen uses
vocal language and is released. Missing ASR offers the 879 MB ONNX download.
Sung overrides, acceptance/reset, checks, Play line, Edit sung lyrics, New take
and reviewed-range repaint are wired. Repaint requires saved latents, the
sidecar, and installed matching DiT; it preserves the original take.

Scan checks retain the recogniser's raw text alongside pinyin scoring. Their
line ranges use the contributing 15 s recognition windows, **not precise
word timestamps**. The view labels that approximation and requires range
review before repainting; unheard lines offer the take's manual range action.
EN/ZH layout captures (including sung editor and flagged check detail) were
reviewed at `artifacts/compose-layout/*-lyric-check-*-tab.png`.

The first full fake-based macOS suite under `lock.py build` passed **1215 passed,
4 skipped** in 169.44 s. An earlier unlocked targeted run had one unrelated
50 ms transcript-toggle timing assertion at 59.7 ms under high system load;
the full run passed it unchanged. A real repaint remains pending the GPU lock; UI verification is not an audio-quality claim.
