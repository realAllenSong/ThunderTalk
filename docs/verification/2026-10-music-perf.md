# Music Phase 6 — M3 Max verification (2026-10-07)

This report uses completed fresh runs, not overwritten retry files. Hardware:
Apple M3 Max, 14 CPU cores, 36 GiB unified memory, macOS arm64. All model runs
and heavy downloads in this continuation use `lock.py gpu`; builds and the
full fake-based macOS pytest use `lock.py build`. No concurrent own model/build
jobs, no memory-guard bypass. Main 4fe46b2 was merged before the final build;
`app.py` retains main's immediate capture before audio muting.

## Measurement method

`tools/bench_music.py`: one freshly spawned MusicProcess per A–D, original
Chinese lyrics (`tools/bench_music.py::LYRICS`), genre/mood/instrument-only
style, seed 42, planner 1.7B on. XL: 50 diffusion steps; turbo: 8. BF16 DiT,
fp32 MLX VAE, 192-frame VAE tiles, 1 GiB MLX buffer cache. Weights are isolated
under `~/Library/Caches/ThunderTalk-bench/music-models`; optional runtime HOME
is `artifacts/music-spike/package-home`. Offline model loading.

Wall time includes spawn/import, model load, planning/conditioning, diffusion,
VAE decode, WAV/sidecar/latents write and child exit. Worker physical footprint
(`proc_pid_rusage`, 0.5 s samples) counts Apple unified-memory allocations;
it excludes the parent/ASR footprint and can miss short transients. Minimum
free memory is system-wide. Planning time includes conditioning before step 0;
reported planner tokens are block-hook counts, rounded down to multiples of 16.
Loudness/true peak/LRA use ffmpeg BS.1770 loudnorm analysis; WAV validation
requires finite, non-silent stereo 48 kHz samples, duration within 1 s and
sample peak ≤1. Listening quality remains for the user.

Dictation uses Apple's built-in Samantha saying our original English probe,
trimmed/padded to five seconds at 16 kHz. Qwen3-ASR ONNX uses its normal CPU
configuration, nine threads. DICTATION priority begins at key press, remains
raised for the simulated five-second capture, and ends after recognition,
matching main's capture behavior. Report release-to-text and press-to-text.
Each is a single probe, not a statistical latency guarantee.

## Implementation and deviations

XL converts one verified safetensors shard at a time in a disposable CPU child,
atomically replaces it with BF16 and publishes readiness only after all shards
verify. Final inventory: 829 BF16 tensors, four files, 9,974,621,452 tensor bytes
(9,974,719,556 file bytes); no F32/F16/F64 floating tensors or retained F32 copy.
The conversion marker makes subsequent launches skip conversion. Disk fell
externally from 115 GiB to about 28 GiB free; our retained files stay below the
60 GB budget. Cursor fetched network-only XL data outside the GPU lock; this
was a deviation. Future heavy fetches in the chain now use the GPU lock.

Long XL jobs initially failed native critical-pressure checks. Eager per-tensor
MLX conversion, restoring 192-frame decode tiles and keeping the temporary
torch decoder on CPU until MLX replaces it removed transient duplicate heaps.
The first finer-hook XL170 attempt then aborted on native critical pressure
at decode (29% free, minimum 25%, no WAV). The planner now unloads at step 0
after its codes are complete and reloads before each subsequent take. Initial
load peak still counts for admission. This changes planner residency, not its
codes, precision, or the 1.7B-only choice. A two-take regression checks planning
and unload/reload order.

Remaining torch encoders still run on MPS; a failed MLX conversion retains the
full torch fallback. Admission ≥40%, the additional post-admission reserve,
warning <25%, abort <15% twice/native critical pressure, cancellation and stall
watchdogs remain in force.

The original diffusion-step hook is too coarse for immediate recognition.
Opt-in checkpoints complete MLX attention/MLP blocks and VAE tiles before
yielding; parent polling is 50 ms. A cancellation inside upstream's service
thread now propagates BaseException to the main worker, so it reports Cancelled
instead of a missing-output failure. Actual failed latency attempts cancelled
and exited cleanly after this fix. Default upstream execution outside the music
worker does not materialize arrays for these new hooks.

The approved SIGSTOP/SIGCONT fallback was tested: Metal resumed and the full
170 s song finished, but latency still missed +30%. It is not shipped. Retained
stress results (priority raised only at recognition): step hooks +54.9%,
SIGSTOP +46.4%, layer hooks +48.3%, attention/MLP hooks +32.8%. Corrected
capture timing also showed a miss (0.448/0.706 s); preserve the acceptance
failure rather than modifying ASR settings or the baseline to obtain a pass.
These failures and the final measurement are distinct; old successful WAVs
with overwritten failed benchmark JSON are excluded from the final table.

## Repaint and lyrics

A real cached-latent turbo repaint of 3–6 s completed in 19.583 s, peak
15,142,062,528 bytes (14.10 GiB); child gone, source hash unchanged, output
shape/sample rate matched and finite. Outside a one-second blend guard: SNR
73.444 dB, maximum change one PCM16 step (0.00003052). Inside: SNR −2.139 dB,
maximum change 0.565765. This verifies a local audio change, not improved lyrics.
Raw result: `artifacts/music-spike/phase6/repaint/repaint-check.json`.

Neither MLX nor collapsed MPS alignment provided trustworthy line timestamps.
The lyric view uses the contributing scan windows, labels them approximate,
and asks for range review before repaint. The 0.20 pinyin/token threshold flags
the supplied corruption cases; read-back results are advisory and do not prove
by-ear quality or exact word timing.

## Final source A–D

Fresh output root: `artifacts/music-spike/phase6-final-release/`. Each directory
contains benchmark.json, WAV, sidecar and repaint latents. Worker code: 0fc3e84.
All four outcomes Done; every child exited. No failed retry is counted.

| Run | Wall s | Peak worker bytes | Peak GiB | Min free | LUFS | dBTP | LRA LU |
|---|---:|---:|---:|---:|---:|---:|---:|
| A · XL / 60 s instrumental | 134.549 | 19,284,764,288 | 17.960 | 30% | -14.71 | -0.95 | 3.7 |
| B · XL / 170 s vocal | 374.323 | 19,377,112,320 | 18.046 | 27% | -17.02 | -1.00 | 9.4 |
| C · turbo / 60 s instrumental | 31.658 | 13,981,061,120 | 13.021 | 49% | -13.10 | -1.00 | 3.9 |
| D · turbo / 170 s vocal | 70.992 | 14,241,173,632 | 13.263 | 41% | -15.03 | -0.72 | 7.6 |

All WAVs are exactly the requested duration, stereo 48 kHz PCM16, finite and
non-silent. Sample peaks A–D: 0.89123535, 0.89126587, 0.89126587, 0.89126587.

| Run | Spawn/import | Load | Plan/condition | Diffusion | VAE | Write | Exit |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 2.318 | 13.672 | 9.946 | 100.607 | 7.353 | 0.031 | 0.622 |
| B | 2.007 | 15.418 | 25.451 | 309.811 | 20.803 | 0.083 | 0.751 |
| C | 2.107 | 8.865 | 9.516 | 3.377 | 7.225 | 0.045 | 0.522 |
| D | 2.126 | 7.945 | 25.273 | 14.855 | 20.185 | 0.080 | 0.529 |

All phase costs are seconds.

| Run | First step s | Steady step s | Reported LM tokens | Reported tokens/s |
|---|---:|---:|---:|---:|
| A | 2.646 | 1.999 | 304 | 30.57 |
| B | 13.801 | 6.040 | 864 | 33.95 |
| C | 0.700 | 0.380 | 304 | 31.95 |
| D | 7.139 | 1.101 | 864 | 34.19 |

B and D include a five-second capture-priority pause plus recognition in their
first diffusion interval; that interval is not a pure GPU step speed. A/C have
no ASR resident. B/D load ASR before admission; parent memory is excluded from
the worker peak. These are single runs on a shared machine, not throughput
or latency guarantees. Planner sampling is not proven bitwise reproducible.

## Catalog facts

Measured upper peaks rounded up to 100 MiB: XL 18,500 MiB, turbo 14,100 MiB (also covers current repaint).
The generation peaks include the planner; use the same conservative bound without it and do
not add it twice. Admission keeps the additional 20% system-memory reserve.
The 16 GiB experimental MPS tier remains offered, but its distinct no-planner
peak has not been remeasured; these conservative MLX bounds can refuse it.
No actual 16/24 GiB device or 300/600 s XL song was verified.

Upper observed diffusion/decode/write rates: XL 1.95, turbo 0.21 s per audio
second, plus 0.17 for planning/conditioning when enabled. These rates exclude
initial model load; they are predictions outside the measured 60/170 s cases.
The estimate label names the M3 Max reference. EN/ZH model facts now show the
measured 170 s wall times and memory units. Benchmarks preceded this catalog
fill; the final packaged checks use the measured admission bounds.

## Dictation acceptance

The early probes mixed a main-thread idle call with a background concurrent
call. An idle-only three-call comparison gave ~0.413–0.444 s on the main
thread and ~0.413–0.421 s in the background (thread-parity-probe/results.json).
The definitive XL probe uses the **same background thread** for both calls,
normal nine-thread ONNX settings, the five-second capture window, and clean
cancellation after measurement (no WAV expected).

| Condition | Release-to-text s | ASR ms | Press-to-text s |
|---|---:|---:|---:|
| Idle | 0.447157 | 423 | 5.457197 |
| XL generation | 0.675399 | 655 | 5.684183 |

Release latency increased **51.0%**, so the planned +30% limit **failed**.
Both texts exactly matched our original probe. Raw file:
`artifacts/music-spike/phase6-final-probe/dictation.json` (matching thread IDs),
benchmark.json reports Cancelled and no remaining child. Capture-to-text total
increased ~4.2%; this does not replace the failed release-latency criterion.
The full B probe was 0.465547/0.700516 s (+50.5%); D was
0.454027/0.682378 s (+50.3%). Those use the earlier mixed-thread harness.

This is a synthetic ASR/priority test; it excludes microphone device timing,
live preview, paste delivery and GUI hotkey end-to-end latency. No personal
voice or user recording was used. The next tuning work should investigate
ASR working-set/cache effects under resident music weights; slowing the idle
baseline, changing ASR settings or weakening memory guards is not a fix.

## Final read-back

Same product scorer / threshold 0.20, Qwen3-ASR ONNX, scan windows:
XL **23/24** lines clear (8.710 s check); turbo **16/24** (8.886 s).
The intelligible Phase-0 English reference is **7/8** clear at this threshold.
Raw checks and heard text are retained in take-1.lyric-check.json beside each
final vocal WAV and phase6/check-reference-final.json. The reference is
objective calibration data, not a user-approved-by-ear song.

A clear badge permits some syllable errors and homophones; it is not semantic
proof. For example, the XL read-back of 原文记下新的方向 as 远远寄下新的方向
has error 0.125 and passes the 0.20 bound. The raw heard text remains visible
for review. Do not interpret 23/24 as 23 perfectly reproduced lyric lines.

## Current-code follow-up

Two 10 s turbo vocal takes, planner enabled, seeds 1234/1235: Done in 24.4 s,
12.84 GiB sampled worker, child gone; both exactly 10 s. The real log confirms
`llm_initialized=True, use_lm=True` for both takes after planner reload.
Raw: `artifacts/music-spike/phase6/two-takes-final.json` / .log.

Repaint 3–6 s on the final turbo170 take (same current 192-frame decode):
Done in **37.149 s**, peak **14,773,039,936 bytes / 13.758 GiB**, child gone;
source hash unchanged; same duration, shape and sample rate, finite samples.
Outside a one-second blend guard: **76.544 dB SNR**, one PCM16 step maximum
change. Inside: **0.868 dB SNR**, 0.911438 maximum change. Raw:
`artifacts/music-spike/phase6-final-release/repaint-current/repaint-check.json`.
This higher peak is included in turbo's admission bound. XL repaint remains
unverified on real weights; matching-model/range guards are covered with fakes.

## Source validation before final build

Full macOS fake-based suite: **1233 passed, 4 skipped, 80.46 s**
(`artifacts/pytest-phase6-measured-facts.log`), through build lock with
`PYTHONPATH=$PWD` and the shared interpreter. Changed-file ruff and diff check
clean. Real vendored turbo/XL weight-reload/buffer regressions in private
venv-music: **11 passed, 2.82 s**, through GPU lock
(`artifacts/pytest-music-vendor-final.log`). Exact re-vendor: all 93 Python
files match. Two optional real-vendor tests are skipped by the shared suite
because their optional music dependencies are private; the private run covers
them. No shared dependency was installed
or upgraded in this continuation; pypinyin >=0.55 was added in inherited Phase 4.

Final EN/ZH setup and expanded lyric-check layout captures were reviewed in
`artifacts/compose-layout-final/`; measured facts wrap within the existing
scrolling Paper & Ink form, and range advice stays in both languages.
