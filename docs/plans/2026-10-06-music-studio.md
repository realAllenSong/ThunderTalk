# Studio ▸ Compose: songs and background music with ACE-Step 1.5 (plan)

Status: **plan only** (phase 1). Nothing below is implemented yet. Every number
is labelled as *verified* (measured by the user on this Mac on 2026-10-05),
*checked* (read from the upstream source or the Hugging Face API while writing
this plan) or *estimate* (to be replaced by a measurement before release).

## Why

The user generated songs with ACE-Step 1.5 on this M3 Max (36 GB, macOS 15.6)
and approved the result by ear: DiT `acestep-v15-xl-sft` (4B), planner LM
`acestep-5Hz-lm-1.7B` with `thinking=true`, 50 inference steps. The request is
to bring that into Studio as a third tab beside Transcribe and Speak:

- two modes — a **song with vocals** (style + lyrics) and **background music**
  (instrumental);
- several **takes** per request (quality varies a lot by seed; the user picks);
- a **lyric check** that reads each take back with ThunderTalk's own
  Qwen3-ASR and points at the lines that were sung wrong;
- the same safety rules as the rest of the app: one heavy model at a time,
  dictation always first, no network except model downloads.

## What the user's runs taught us (verified)

| # | Finding | Consequence for the design |
|---|---------|----------------------------|
| 1 | Upstream loads the DiT in float32 on MPS (`init_service_orchestrator.py`: `torch.bfloat16 if xpu else torch.float32` for every non-CUDA device) → ~20 GB for the 4B. bfloat16 halved it; free memory stayed 29–60 %, audio fine. The local test used an env-var patch (`ACESTEP_MPS_DTYPE`). | We choose bf16 ourselves at handler init in a vendored copy (below), no env-var patch. |
| 2 | Upstream aborts after 600 s (`ACESTEP_GENERATION_TIMEOUT`); one take on MPS exceeded it. | Raise it; our own stall watchdog replaces it. |
| 3 | `cli.py` with `thinking=true` writes `instruction.txt` and blocks on `input()`; a stale `instruction.txt` is silently reused. | Never use `cli.py`. Call `acestep.inference.generate_music` with `AceStepHandler` + `LLMHandler` in a worker process. |
| 4 | Docker took ~22 GB mid-run and pushed free memory to 18 %. | Admission check, live memory watchdog with a clean abort and a reason, unload by process exit. |
| 5 | Latin words in Chinese lyrics are mis-sung ("欧拉SONG" → 神机/算机/森); singing a phonetic spelling fixes it ("欧拉宋", sung 欧拉颂). Some phrases come out unclear (一行公式 → 遗憾, 原文 → 圆圆). | "Sung lyrics" override per line + pronunciation helper + lyric check with "new take" / "repaint this line". |
| 6 | 170 s song: LM planning 25–60 s at ~35–55 tok/s; diffusion ~6–7 s/step after warm-up, first steps 20+ s; total ~8 min. Output 48 kHz 16-bit stereo WAV, ≈ −15.5 LUFS, peak −0.9 dBFS. | Progress must show LM tokens and diffusion step k/N; takes run one after another. |

## What the upstream source says (checked, commit `ca1e85f`, 2026-08-29)

Shallow clone (code only, 30 MB) at
`~/Library/Caches/ThunderTalk-bench/ace-step-src`.

- **Licence.** Code: MIT (`LICENSE`, "Copyright (c) 2026 ACEStep"). Weights:
  the Hugging Face cards of `ACE-Step/Ace-Step1.5`, `ACE-Step/acestep-v15-xl-sft`,
  `ACE-Step/acestep-5Hz-lm-4B` and `ACE-Step/acestep-5Hz-lm-0.6B` all declare
  `license: mit`. The text encoder inside the main repo is Qwen3-Embedding-0.6B,
  whose own upstream licence is Apache-2.0; record both in
  `third_party/README.md` and the About page credits.
- **Python / torch.** `requires-python = ">=3.11,<3.13"` (we ship 3.12.11 ✓);
  macOS wants `torch>=2.9.1`, `torchaudio>=2.9.1` (our optional runtime pins
  2.11.0 ✓); `mlx>=0.25.2` (bundled 0.32.0 ✓), `mlx-lm>=0.20.0` (bundled 0.31.3 ✓).
- **transformers mismatch.** Upstream pins `transformers>=4.51.0,<4.58.0`
  (lockfile 4.57.6); the app bundles **5.6.2**. All 13 internal symbols the
  inference path imports (`rope_config_validation`, `layer_type_validation`,
  `_prepare_4d_causal_attention_mask`, `GradientCheckpointingLayer`,
  `ALL_ATTENTION_FUNCTIONS`, `EncoderDecoderCache`, `Qwen3RotaryEmbedding`…)
  still exist in 5.6.2 (import check, no model loaded). Behaviour differences
  (rope config, `from_pretrained(dtype=…)`) are untested → Phase 0 gate.
- **Inference-only dependency closure on a Mac:** torch, torchaudio,
  transformers, `diffusers` (only `AutoencoderOobleck` for the VAE),
  `einops` (bundled), `vector-quantize-pytorch` (FSQ), `loguru`, `soundfile`
  (bundled), `PyYAML` (bundled), `mlx`, `mlx-lm`. Not needed for inference:
  gradio, fastapi, peft, lightning, torchao (quantisation only), numba (scoring
  only), nano-vllm, modelscope, matplotlib.
- **The Mac path is half MLX already.** `initialize_service(use_mlx_dit=True)`
  converts the torch DiT decoder into an MLX decoder for `device in ("mps",
  "cpu")` (`mlx_dit_init.py` → `models/mlx/dit_convert.py`); the VAE decode also
  has an MLX path; the LM uses `mlx-lm` when `backend="mlx"`. Two traps:
  `convert_decoder_weights` casts every tensor with `.float()` (an fp32 MLX
  copy), and the torch decoder is **not** freed afterwards. 20 GB is exactly
  4.99 B params × 4 bytes, so the user's fp32 run most likely used the PyTorch
  DiT only; with MLX DiT active both copies would be resident. Which path we use
  is decided by measurement in Phase 0.
- **No per-step hook.** The MLX diffusion loop is
  `for step_idx in tqdm(range(num_steps))` (`models/mlx/dit_generate.py:422`); the
  MLX LM decode loops use `tqdm(total=max_new_tokens)` (`llm_inference.py`
  3284/3605/3881). Upstream's own progress during diffusion is a time-based
  *estimate* thread (`_start_diffusion_progress_estimator`).
- **Timeout** is a module constant read once at import:
  `_DEFAULT_GENERATION_TIMEOUT = int(os.environ.get("ACESTEP_GENERATION_TIMEOUT", "600"))`
  in `generate_music_execute.py`; on expiry the generation thread keeps running
  in the background.
- **Remote code.** `AutoModel.from_pretrained(..., trust_remote_code=True)` executes
  the `modeling_*.py` files that ship inside the weight repos, and
  `_sync_model_code_if_needed` rewrites them. We load the vendored model
  classes directly and never download `*.py` from a model repo.
- **Repaint.** `GenerationParams(task_type="repaint", src_audio=…,
  repainting_start=…, repainting_end=…)`; for a take we generated, a sidecar
  `<take>.json` with `repaint_source_latents_file` and `seed` lets upstream reuse
  the exact latents (`_load_cached_repaint_source`). It also globs
  `os.getcwd()/gradio_outputs`, so the worker `chdir`s into the job folder.
- **Lyric timestamps.** `generate_music` returns `extra_outputs` with
  `pred_latents`, `encoder_hidden_states`, `context_latents` and
  `lyric_token_idss`; the Gradio UI passes them to
  `AceStepHandler.get_lyric_timestamp(...)`, which aligns decoder
  cross-attention to the lyric tokens and returns sentence timestamps. That
  gives each lyric line a time range — the lyric check and repaint use it.
- **Inputs.** `caption` < 512 chars, `lyrics` < 4096 chars, `duration` 10–600 s,
  `bpm` 30–300, `vocal_language` from `constants.VALID_LANGUAGES` (50 codes +
  `unknown`), `instrumental=True` or `"[Instrumental]"`, `seed`, `inference_steps`.

### Model files (checked via the Hugging Face API, 2026-10-05)

| Component | Repo / folder | Stored as | Download |
|---|---|---|---:|
| DiT XL SFT ("4B") | `ACE-Step/acestep-v15-xl-sft` (4 shards) | 4.99 B tensors, **F32** | 19.95 GB |
| DiT turbo ("2B") | `ACE-Step/Ace-Step1.5` / `acestep-v15-turbo` | 2.39 B tensors, BF16 | 4.79 GB |
| Planner LM 1.7B | `ACE-Step/Ace-Step1.5` / `acestep-5Hz-lm-1.7B` | — | 3.76 GB |
| Text encoder | `ACE-Step/Ace-Step1.5` / `Qwen3-Embedding-0.6B` | — | 1.21 GB |
| VAE | `ACE-Step/Ace-Step1.5` / `vae` | — | 0.34 GB |
| (not offered) LM 4B / 0.6B | `ACE-Step/acestep-5Hz-lm-4B` / `-0.6B` | BF16 | 8.43 / 1.37 GB |

The user's figures (19 / 4.5 / 3.5 / 1.1 / 0.3 GB) are the same files in GiB.
The UI shows decimal GB like every other download in the app.

## Decisions

| Topic | Decision |
|---|---|
| Where | A third Studio tab, **Compose** (作曲), after Speak. Apple Silicon only. |
| Runtime | The existing optional PyTorch runtime plus a small pinned **music component** (same installer, manifest, SHA-256, atomic publish). Nothing new in the base download. |
| Upstream code | Pruned, pinned copy in `third_party/acestep/` with a short list of patches (dtype, hooks, timeout, no remote code), like `third_party/mlx_indextts`. Shipped as data, imported only inside the worker. |
| Execution | A disposable **spawned process** per job (pattern of `core/translate_process.py`); the process exit is the unload. Never `cli.py`. |
| Precision | bf16 on MPS, chosen by our worker when it creates the handler. |
| Priority | Worker pauses between diffusion steps / LM token blocks while `DICTATION` is raised or Metal is busy in-process; dictation never waits on music. |
| Memory | Admission ≥ 40 % free and ≥ 20 % projected after load; warn < 25 %; abort < 15 % (two samples in a row). |
| Models | XL SFT = best quality (≥ 32 GB Macs), turbo = light (≥ 16 GB). Planner LM 1.7B optional, offered by rule. 4B / 0.6B LMs not offered in v1. |
| Lyric check | Per line, using ACE-Step's own line timestamps + the app's Qwen3-ASR; tone-less pinyin comparison for Chinese so homophones pass. Advisory. |
| Linux | Not in v1 (tab hidden). |

## Architecture

```
StudioPage ─ Transcribe │ Speak │ Compose (ui/studio/compose_tab.py)
                                   │  MusicWorker / LyricCheckWorker / BackendDownloadWorker (ui/studio/workers.py, QThreads)
                                   ▼
                        core/music.py          request/take dataclasses, model catalog, hardware tiers, job folders
                        core/music_process.py  MusicProcess: spawn, guard thread (priority + memory), cancel, watchdog
                                   │ multiprocessing "spawn": Pipe (events up) + Events go / cancel (control down)
                                   ▼
                        core/music_worker.py   child: runtime.activate, vendored acestep, hooks, handlers (bf16), takes → WAV + sidecar
                        core/music_lyrics.py   section tags, display vs sung lyrics, pronunciation helper, lyric check scoring
                        core/music_rules.py    artist-name warnings (local list + heuristics)
```

### Reused, not duplicated

| Existing piece | How Compose uses it |
|---|---|
| `core/runtime.py` + `runtime_manifest.json` | Generalised to named components (`torch`, `music`); music requires torch. Same `install()`, resume, SHA-256, `flock`, atomic rename, `require()` / `restart_needed()`. |
| `core/tts_backends/base.py` `Download`, `BackendInfo` | Each music model is described by a `BackendInfo` with its `Download`s, so `speech.download_backend` and `BackendDownloadWorker` fetch it with byte progress and cancel. One new `Download.kind = "hf-dir"` (repo + `allow` globs → `~/.thundertalk/models/<dest>`, via the existing `models._hf_snapshot(repo, local_dir, allow, …)`). The component branch `if info.id == "indextts"` becomes a `BackendInfo.components` tuple. |
| `models.download_model` disk check | Same rule (free ≥ 1.2 × size) in `download_backend`, shown before the click. |
| `ui/model_facts.facts_text` / `language_tags` | Facts line for each music model (params · size · GPU · speed) plus RAM needed. |
| `core/model_metadata.py` | `ACESTEP_LANGUAGES` (the 50 `VALID_LANGUAGES` codes, source cited in `docs/model-metadata.md`). |
| `core/translate_process.py` | Template for `MusicProcess`: spawn context, daemon, `_serve(connection)`, terminate → kill → close on unload, "worker stopped" error. |
| `core/memory_policy.managed` | `MusicProcess.run` is a leased method (`@managed(release="release_idle")`) so the idle sweeper never reclaims mid-job and the grace period restarts afterwards; `release_idle` kills a leftover process. |
| `core/gpu_lock.GPU_LOCK` | Taken at admission (no in-process Metal unit in flight while we free other models and check memory) and **probed** (`acquire(blocking=False)` + release) by the guard thread before granting each step. Never held during a step. |
| `core/priority.DICTATION`, `STUDIO` | Guard thread clears `go` while `DICTATION.active`; the job runs inside `STUDIO.running()` so live-preview timing ignores it. Lyric check calls `DICTATION.wait_clear()` between lines like file transcription does. |
| `SpeechEngine.release_gpu()` | Called at admission to drop an idle GPU voice model; if it returns `False` (Speak is busy) the job fails with `model_busy`. |
| `transcribe.selected_engine` + `_has_model_headroom` | Lyric check gets a Qwen3-ASR engine exactly like a Studio transcription with an alternate model. |
| `tts_verify.tokens` / `edit_distance` | Word/char error per lyric line (English path). |
| `ui/studio/workers._Worker`, `error_code`, `friendly_error` | `MusicWorker`, `LyricCheckWorker`; new codes `music:<code>:<detail>` mapped like `link:`/`burn:`. |
| `ui/studio/parts.PlayerBar`, `theme.Chip`, `theme.badge`, `SegmentedControl` | Take playback and controls; no new visual style. |
| `core/i18n_studio.py` | All strings under `studio.compose.*` / `studio.music.*`, EN + ZH. |
| `selftest.py` | New `music` check. |

## Runtime and dependencies

**Base download unchanged.** Torch stays excluded from `ThunderTalk.spec`
(`excludes=['torch', 'torchaudio', …]`). The vendored `third_party/acestep`
(pure Python, ~1–2 MB after pruning) is added as a **data tree**, not as hidden
imports, so PyInstaller never analyses its torch imports; the worker puts that
folder on `sys.path` before importing it.

**Music component** — a second manifest beside `runtime_manifest.json`
(`core/runtime_music_manifest.json`, id e.g. `acestep-1.5-ca1e85f-cp312-macos-arm64-v1`),
pinned wheels with URL, size and SHA-256, installed by the same
`runtime.install(component="music")`:

| Wheel | Why | Size |
|---|---|---|
| `vector-quantize-pytorch` (+ its small pure-Python deps, pinned) | FSQ layer in the DiT | estimate < 1 MB |
| `loguru` | upstream logging everywhere | ~0.1 MB |
| `diffusers` | `AutoencoderOobleck` VAE class | ~4 MB |
| `pypinyin` (MIT) | tone-less pinyin for the lyric check | ~1 MB |
| (fallback only) `transformers 4.57.6` | if Phase 0 shows 5.6.2 misbehaves | ~12 MB |

Estimated ≈ 6–10 MB on top of the 90 MB PyTorch runtime; exact figures come
from the pinned manifest. `diffusers`' import closure (`regex`, `requests`,
`Pillow`, `importlib_metadata`, `huggingface_hub` ≥ 0.34) is checked against the
bundle in Phase 0; if it drags in anything unbundled, vendor the Oobleck VAE
class (Apache-2.0, ~400 lines) into `third_party/acestep` instead.

**transformers.** Preferred: run on the bundled 5.6.2 (one copy in the app),
fixing any behaviour difference as a vendored patch. Fallback: the component
ships `transformers 4.57.6` and the **worker** puts it ahead of the bundle on
`sys.path`. That is safe only because the worker is a fresh spawned process that
has not imported transformers yet; the parent never sees it. PyInstaller 6's
path-based frozen importer has to honour that order — Phase 0 verifies it in a
packaged build before we rely on it.

`runtime.install` already refuses anything but Python 3.12 / Darwin / arm64,
which is also the music support matrix.

### Vendored upstream (`third_party/acestep/`)

From ACE-Step-1.5 at `ca1e85f`, MIT. Keep only the inference closure:
`inference.py`, `handler.py`, `llm_inference.py`, `constrained_logits_processor.py`,
`constants.py`, `gpu_config.py`, `core/generation/handler/*` (no `*_test.py`, no
`lora/`, no training presets), `models/{common,mlx,xl_sft,turbo}`. Remove
`ui/`, `api/`, `training*/`, `third_parts/nano-vllm`, `openrouter*`, `cli.py`,
scoring (numba). Patches, each listed in `third_party/README.md`:

1. **dtype at init**: `initialize_service(..., dtype=None)`; when given, it wins
   over the device rule. The worker passes `torch.bfloat16` on MPS. If MLX DiT
   is kept (Phase 0), `convert_decoder_weights` keeps the dtype
   (`mx.array(...).astype(mx.bfloat16)`) and the torch decoder is released
   after conversion.
2. **hooks**: a tiny `acestep/_hooks.py` with `on_step(k, n)` and
   `on_tokens(done, total)`; the four `tqdm` loops call it (default no-op).
   This is the only place our cancel / pause / progress enter upstream.
3. **timeout**: `_DEFAULT_GENERATION_TIMEOUT` is read from `_hooks.TIMEOUT_S`
   (worker sets it to the take budget, see below) instead of the environment.
4. **no remote code**: load `AceStepConditionGenerationModel` from the vendored
   `models/xl_sft` or `models/turbo` package directly; skip
   `_sync_model_code_if_needed`; `LLMHandler` loads with
   `trust_remote_code=False` (Qwen3 is native in transformers and mlx-lm).

## Execution

`MusicProcess` (`core/music_process.py`) mirrors `TranslationProcess` but is
one job per process: spawn → load → all takes → exit.

```
parent (MusicWorker QThread)                       child (_serve in core/music_worker.py)
MusicProcess.run(request, on_event)                runtime.activate("torch","music"); sys.path += third_party
  admission (GPU_LOCK, release_gpu, memory)        os.chdir(job_dir); _hooks.on_step = gate; _hooks.on_tokens = gate
  spawn(Pipe, go, cancel)  ─────────────────────▶  AceStepHandler().initialize_service(project_root=<models>/ace-step,
  guard thread (10 Hz): go = !DICTATION.active          config_path=<dit>, device="mps", dtype=bfloat16, use_mlx_dit=<Phase 0>)
        and GPU_LOCK free and memory ok            LLMHandler().initialize(..., backend="mlx") if planner
  memory watchdog (1 Hz)                           for i, seed in enumerate(seeds):
  stall watchdog                                       generate_music(dit, llm, GenerationParams(...), GenerationConfig(batch_size=1, seeds=[seed]))
  ◀──── ("phase","load") ("tokens",d,N) ("step",k,N) ("paused","dictation") ("take",i,path,meta) ("done") / ("error",code,detail)
  join / terminate → kill after grace                  get_lyric_timestamp(...) → sidecar; write WAV + <take>.json + latents .npy
```

- **One take per call, sequential.** `batch_size=1`, seed `base + i` (or random
  per take when the seed field is empty; every seed is recorded). Models load
  once per job. A finished take is written and reported immediately, so a later
  cancel or abort keeps it.
- **Progress.** LM: `on_tokens(done, total)` every 16 tokens (`total` ≈ CoT +
  5 codes per second of audio). Diffusion: `on_step(k, N)` before each step.
  VAE decode and file writing are reported as phases. The UI shows
  "Planning the song · 412 / ~1,050", "Composing · step 17 / 50",
  "Rendering audio", plus elapsed time and the per-take estimate.
- **Cancel.** The parent sets `cancel`; the next hook raises `MusicCancelled`
  (≤ one step). If the child has not exited 10 s later: `terminate()`, then
  `kill()`. Exiting is the unload — torch's MPS heap and MLX buffers go with it.
- **Timeout.** Upstream's 600 s limit becomes
  `_hooks.TIMEOUT_S = max(1800, 3 × estimated take time)`. Ours is a **stall**
  watchdog in the parent: no event for 180 s (first XL steps take 20+ s, a long
  VAE decode maybe a minute) → kill, error `music:stalled`.
- **Never `cli.py`**, no `instruction.txt`, no `input()`; the worker's stdin is
  closed.
- **Planner placement (Phase 0 decides).** Default: upstream's integrated call
  (`thinking=True`), which is what the user verified. Alternative if it measures
  the same: plan all takes with the LM first, unload it, then load the DiT and
  pass `audio_codes` + the planned metadata with `thinking=False` — a lower peak
  (LM and DiT never resident together), the only way the planner fits on 24 GB.

## Memory and priority safety

**Free memory gauge.** `kern.memorystatus_level` via `ctypes`
`sysctlbyname` — the same "system-wide memory free percentage" that
`memory_pressure` prints and `transcribe._has_model_headroom` already parses —
read without spawning a process; `memory_pressure` parsing is the fallback.
Factored into one helper (`memory_policy.free_percent()`), which
`_has_model_headroom` then reuses.

| When | Rule | Result |
|---|---|---|
| Before spawn (under `GPU_LOCK`) | `SpeechEngine.release_gpu()` succeeds; free ≥ 40 %; `total × free − ram_need ≥ 20 % × total` | otherwise `memory` / `model_busy` before anything loads |
| During the job, 1 Hz | free < 25 % | amber notice "Memory is getting low" in the progress line |
| During the job | free < 15 % on two consecutive samples, or `kern.memorystatus_vm_pressure_level` reports critical | terminate → kill; error `music:memory_low:<pct>` → "Stopped to protect your Mac: free memory fell to 14 %. Finished takes are kept." |
| After the job | process exits | everything freed; `trim_caches()` not needed in the parent |

`ram_need` comes from the model catalog (estimates until Phase 6 measures
them). The 15 % / 25 % thresholds sit around the 18 % the user saw when
Docker took 22 GB: a run that dips to 18 % keeps going with a warning; one
that keeps falling stops while the Mac is still responsive.

**One heavy model at a time.** The job takes the existing alternate-model slot
(`transcribe._EXTRA_MODEL_LOCK`, moved to `core/priority.py` as `EXTRA_MODEL`
so both modules import it) for its whole lifetime. A Studio transcription with
an extra model, or a second music job, reports `model_busy`; the lyric check
runs after the slot is released. At the UI level, Generate is disabled with a
tooltip while Speak or a Transcribe job is busy, and vice versa.

**Dictation first, exactly how:**

1. The guard thread in the parent recomputes
   `allowed = not DICTATION.active and gpu_free() and memory_ok` ten times a
   second and sets or clears the shared `go` event. `gpu_free()` is
   `GPU_LOCK.acquire(blocking=False)` followed by an immediate release.
2. Before every diffusion step, every 16 LM tokens and before the VAE decode,
   the child's hook waits on `go` (in 0.1 s slices, checking `cancel`) and
   sends `("paused", "dictation")` once, so the UI says
   "Paused while you dictate".
3. Music never holds a lock that dictation takes, so dictation never waits.
   The unit already in flight is not preempted: at most one step (≈ 6–7 s on
   XL, 20 s+ for the first ones) overlaps a dictation. With the recommended
   Qwen3-ASR ONNX (CPU) dictation there is no GPU contention; with an MLX
   dictation model the two share the GPU for that one step.
4. Acceptance (Phase 6): a 5 s dictation while XL is composing must finish
   within +30 % of its idle latency. If it does not, the fallback is
   `SIGSTOP` to the worker on `DICTATION.begin` and `SIGCONT` on `end`
   (immediate, no upstream hook needed; untested with Metal in flight, so not
   the default).

## Models and hardware

`core/music.py` holds the catalog (`MUSIC_MODELS`), each entry a `BackendInfo`
plus music facts:

| Model | Params (publisher / tensors) | Download | RAM needed (estimate) | Speed (170 s song) | Steps |
|---|---|---:|---|---|---:|
| **XL SFT** — best quality | 4B / 4.99 B | 19.95 + 1.21 + 0.34 = 21.5 GB | ~12–15 GB in bf16 (10.0 GB weights) | verified ~8 min incl. planner | 50 |
| **Turbo** — light | 2B / 2.39 B | 4.79 + 1.21 + 0.34 = 6.3 GB | ~7–8 GB | estimate 1–2 min (8 steps) | 8 |
| + Planner LM 1.7B | 1.7B | + 3.76 GB | + ~4 GB while planning | verified 25–60 s | — |

60 s instrumental on XL: estimate 3–4 min (diffusion cost scales with latent
length: 1,500 frames at 25 Hz vs 4,250 for 170 s; planning ~300 code tokens).
Both are replaced by Phase 6 measurements, which also fill `ram_mb` and the
speed keys (`models.speed.music_xl`, `music_turbo`).

Shown **before** any download: the facts line, "Needs about N GB of memory",
the download size and "M GB free on this Mac" (from `shutil.disk_usage`).
The Download button is disabled with the reason when the disk check fails.

**Planner LM rule.** It is offered on Macs with ≥ 24 GB. It is pre-ticked
(downloaded together with the DiT) only when the Mac has ≥ 32 GB **and** at
least 20 GB of disk would remain free after the whole download; otherwise it is
an unticked option showing its size. In BGM mode it stays usable (it plans
structure and BPM) but is off by default. The 4B and 0.6B LMs are not offered in
v1.

| Mac | Offered | Defaults |
|---|---|---|
| 8 GB | Nothing; the tab explains that music needs 16 GB | — |
| 16 GB | Turbo only, no planner, length ≤ 240 s | 1 take |
| 24 GB | Turbo + optional planner (sequential placement only); XL shown disabled ("needs 32 GB") | 2 takes |
| 32–36 GB (this M3 Max) | XL SFT recommended, turbo, planner pre-ticked by the rule | XL, planner on, 50 steps, 2 takes |
| ≥ 64 GB | same as 36 GB | same |

The memory watchdog applies on every tier; the tier table only decides what is
offered.

**Linux (CPU):** not supported in v1. The runtime installer is macOS-arm64 only,
and a 2–5 B DiT on CPU would take far longer than a listener will wait. The tab
is hidden where `models.is_variant_compatible` says the platform is not Apple
Silicon.

**On disk:** `~/.thundertalk/models/ace-step/checkpoints/{acestep-v15-xl-sft,
acestep-v15-turbo, vae, Qwen3-Embedding-0.6B, acestep-5Hz-lm-1.7B}` — the layout
`initialize_service(project_root=~/.thundertalk/models/ace-step)` expects. Allowed
globs: `*.json`, `*.safetensors`, `*.pt` (silence latent, loaded with
`weights_only=True`), tokenizer files; never `*.py`. Readiness = every
expected file present with the size from the repo listing.

## UI / UX — Studio ▸ Compose

Single column like Speak, Paper & Ink widgets only.

1. **Mode** (`SegmentedControl`): *Song with vocals* · *Background music*.
2. **Engine card**: model chips (XL SFT · Turbo) with the facts line, memory
   and disk lines, Download / progress / Cancel (`BackendDownloadWorker`),
   planner checkbox "Plan the song first — better structure (+3.8 GB)".
3. **Style** (multi-line, 512-char counter). Placeholder: "e.g. warm indie pop,
   acoustic guitar, soft female vocal, 90s feel". Inline artist warning (below).
4. **Lyrics** (song mode only): `QPlainTextEdit` with a syntax highlighter for
   `[intro] [verse] [pre-chorus] [chorus] [bridge] [outro]`, an "Insert section"
   menu, a 4096-char counter. Latin words in a Chinese line get a dotted
   underline; clicking one shows pronunciation chips.
5. **Sung lyrics** (expander, collapsed by default): one row per lyric line —
   *shown* text on the left, editable *sung* text on the right, prefilled by
   the helper; a reset per row. Only the sung text goes to the model; exports
   and the lyrics file use the shown text.
6. **Settings row**: Length (10–600 s, mm:ss), BPM (Auto or 30–300), Language
   (zh, en, ja, ko, yue, es, fr, de, … from `ACESTEP_LANGUAGES`), Takes (1–5),
   Seed (Random or a number). Estimated time per take for this Mac under it.
7. **Generate / Cancel**; progress line with phase, k/N, elapsed, and the
   paused / low-memory states.
8. **Takes list**: one row per take — ▶ (shared `PlayerBar`), "Take 2 · seed
   1234 · 2:50", lyric-check badge ("12 / 14 lines clear" or "Check 2 lines"),
   *Export WAV…*, ⋯ menu (*Repaint a range…*, *Delete*). Expanding a row shows
   the lyric-check view: every line, flagged ones highlighted with what was
   heard ("heard: 遗憾"), and per line *Play line* · *Edit sung lyrics* ·
   *Repaint this line*; per take *New take* (same settings, new seed).
9. **Footer note** (muted): "Describe style by genre, mood and instruments. Don't
   name real artists or reuse existing lyrics or melodies. Streaming platforms
   such as Spotify ask you to disclose AI-generated music."

Jobs live in `~/.thundertalk/music/<date>-<slug>/` (WAV, sidecar JSON, latents
for repaint); Export copies the WAV to a chosen path. Exported WAVs carry an
INFO comment "AI-generated with ACE-Step 1.5 in ThunderTalk" (soundfile string
fields). The engine/mode/length/takes choices persist through `settings` like
Speak's `_pref` / `_save_pref`.

`StudioPage`: `_tab_options()` gains `("compose", t("studio.tab.compose"))`, the
0/1 index becomes a key→index map, and `shutdown` / `retranslate` / `refresh` /
`stop_playback` include the new tab. `studio.subtitle` mentions music.
New widgets go into `ui/studio/music_parts.py` (`parts.py` is already 1,157
lines).

## Lyric check

Runs automatically after each job (after the worker has exited, so the music
model and the recogniser are never resident together), one `LyricCheckWorker`
per take; skipped in BGM mode.

1. **Recogniser.** The active dictation model if it is a Qwen3-ASR variant;
   else an installed Qwen3-ASR through `transcribe.selected_engine`; else the
   take row offers the 879 MB Qwen3-ASR ONNX download. A temporary engine gets
   `set_language(vocal_language)`; the dictation engine is never re-configured.
2. **Line windows.** From the sentence timestamps the worker saved
   (`get_lyric_timestamp`), padded ±0.4 s, resampled 48 k → 16 k mono with
   `audio_io.resample`. Fallback when timestamps are missing: recognise the take
   in 15 s windows with 2 s overlap and assign the text to lines by global
   alignment (`difflib.SequenceMatcher`) against the sung lyrics.
3. **Score per line.** Chinese: syllable error rate on **tone-less pinyin**
   (`pypinyin.lazy_pinyin`), so 宋/颂 count as correct; English and other
   space-separated languages: `tts_verify.tokens` + `edit_distance`. A line
   passes if its error against the *sung* or the *shown* text is ≤ 0.20 (calibrated from 0.35; see Phase 4 verification), so
   "song" heard for 宋 is fine. Empty recognition = "not heard". The threshold
   is calibrated in Phase 4 on the user's known failures (一行公式 → 遗憾,
   原文 → 圆圆) and on takes the user approved.
4. **Actions.** *New take* queues one more take with the same settings.
   *Repaint this line* starts a job with `task_type="repaint"`, the take as
   `src_audio`, the line's window as `repainting_start/end`, `repaint_mode=
   "balanced"`; the cached latents sidecar keeps the rest of the take
   identical. Enabled only when the sidecar exists and the same DiT is
   installed. Cost ≈ one take without planning (estimate).

Singing over a full mix is hard for an ASR model, and there is no source
separation in v1, so the result is labelled advice ("may have been sung
unclearly"), never an automatic rejection.

**Pronunciation helper** (`music_lyrics.suggest_sung(word, context_lang)`), local
only:

1. The user's own choices first (`~/.thundertalk/music/pronunciations.json`,
   saved whenever they accept or type a sung form).
2. A small built-in table of words common in lyrics and tech (song → 宋,
   hello → 哈喽, OK → 欧克, AI → 诶艾, …), shipped in `assets/music/sung_zh.tsv`.
3. Rule-based transliteration for anything else: all-caps tokens are spelled
   letter by letter; other words are split into orthographic syllables (onset
   consonants + vowel group + coda, with "ng"/"n" codas kept) and each syllable is
   mapped to a common character from a ~250-entry syllable table in the style of
   the Xinhua name-transliteration scheme (so → 索, song → 宋, la → 拉).
   Shown as "suggested — listen and adjust".

Example: shown `欧拉SONG` → suggested sung `欧拉宋`; the lyric check later
accepts "欧拉颂" because the tone-less pinyin matches.

## Content rules

- **Style by genre, mood and instruments only**, stated in the placeholder and
  the footer note. Upstream's reference-audio and cover tasks are **not exposed**
  in v1, so a user cannot feed in an existing recording to imitate.
- **Artist names** (`core/music_rules.find_artist_mentions(text) -> list[Span]`),
  checked as you type (debounced) in Style and Lyrics:
  - a local list `assets/music/artists.txt` (~5,000 singers and bands, English
    and Chinese names) built once by `tools/make_artist_list.py` from Wikidata
    (CC0) by number of sitelinks; matched case-folded on word boundaries for
    Latin script, as substrings of ≥ 2 characters for CJK;
  - names that are also common words or genres (Queen, Yes, Muse, Genesis,
    Rush, …) only count next to a style cue;
  - style cues alone also flag an unknown name: "in the style of X", "sounds
    like X", "X-style", "像X一样", "X风格", "模仿X", "翻唱".
  The warning names the match and offers *Remove*. It does not block Generate
  (false positives are likely); the take's sidecar records that a warning was
  shown. Whether to block instead is an open question.
- **No reproducing existing songs.** There is no offline way to recognise
  copied lyrics or melodies; the rule is stated in the UI, and the "翻唱" /
  "cover of" cue above catches the obvious requests.
- **Disclosure.** Footer note plus the WAV comment. No network calls of any kind
  outside model and runtime downloads.

## Tests (repo style: fakes, no torch, no models)

| File | Covers |
|---|---|
| `tests/test_music_process.py` | Fake `Pipe` / `Process` like `test_translate_process.py`: spawn context + daemon; events forwarded in order; cancel → hook raises → exit, kill after grace; `DICTATION.begin()` clears `go`, `end()` sets it; another thread holding `GPU_LOCK` pauses; memory gauge stubbed to 14 % twice → terminate + `music:memory_low:14`; one sample at 14 % does not; crash → `worker_stopped`; stall watchdog with a fake clock; lease armed during the job and `release_idle` kills a leftover process; `model_busy` when the extra-model slot is taken. |
| `tests/test_music_worker.py` | `_serve` with a fake `acestep` package injected into `sys.modules`: dtype passed is bf16 on MPS; `_hooks.TIMEOUT_S` raised; `batch_size=1` and seeds `base+i`; WAV written at 48 kHz/16-bit stereo with the INFO comment; sidecar has `seed`, `repaint_source_latents_file`, timestamps; `chdir` to the job folder; no `cli` import; no `*.py` in the download allow-list. |
| `tests/test_music_lyrics.py` | Section-tag parsing and validation; shown vs sung mapping round-trip; suggestions (欧拉SONG → 欧拉宋, AI → letters, user dictionary wins); lyric scoring with a fake recogniser (homophones pass, garbled line flagged, missing line flagged, English WER path); timestamp windows and the global-alignment fallback. |
| `tests/test_music_rules.py` | Listed artist found in EN and ZH; genre words not flagged; ambiguous names only with a cue; cue patterns flag unknown names. |
| `tests/test_runtime.py` (extend) | Named components; music requires torch; manifest SHA mismatch rejected; `restart_needed` per component. |
| `tests/test_models_download.py` (extend) | `hf-dir` downloads with allow globs into the expected folder; readiness check; disk-space refusal; tier table per RAM size. |
| `tests/test_studio_ui.py` (extend) | Compose tab with real widgets and a fake `MusicProcess`: mode switch hides lyrics and sung rows; length / BPM bounds; Generate disabled when not downloaded, when Speak is busy, or when memory admission fails (with the reason); progress and paused states; takes appear as they finish; lyric-check view highlights flagged lines; tab hidden off Apple Silicon. |
| `tests/test_studio_i18n.py` | Picks up every new `studio.compose.*` / `studio.music.*` key in both languages with identical placeholders (existing check). |

**Selftest.** `thundertalk --selftest music [--model turbo|xl]` inside the
packaged app (through `lock.py gpu` on the bench Mac): installs nothing,
requires the component and the model, generates a 10 s instrumental with
the installed model (8 steps on turbo, 12 on XL) and passes when the WAV is
48 kHz stereo, 9–11 s long, not silent, peak ≤ 0 dBFS, the process exited, and
it prints load time, generation time and peak footprint. `--selftest runtime`
also checks the music component.

## Performance report (to produce before calling it done)

`tools/bench_music.py`, run once per configuration through
`python3 ~/Library/Caches/ThunderTalk-bench/lock.py gpu -- …` with nothing else
heavy running, `HF_HUB_OFFLINE=1`:

| Run | Config |
|---|---|
| A | 60 s instrumental, XL SFT bf16, planner on, 50 steps, 1 take, seed 42 |
| B | 170 s Chinese song (fixed lyrics with section tags), same config |
| C, D | A and B on turbo (8 steps), for the light-model facts |

Recorded per run: wall time per phase (spawn + import, model load, planning
tokens and tok/s, first diffusion step, steady s/step, VAE decode, write),
total; **peak worker footprint** (`proc_pid_rusage` `ri_phys_footprint`, sampled
every 0.5 s — counts MPS/MLX unified memory, unlike RSS); minimum system free %;
output loudness and peak; dictation latency for a 5 s clip during run B versus
idle. Results go into `docs/verification/2026-10-music-perf.md` and replace every
"estimate" in the catalog and in this document.

## Phases

| # | Work | Effort | Gate |
|---|---|---|---|
| 0 | **Spike** (dev venv, behind `lock.py gpu`): vendor + prune at `ca1e85f`, apply patches 1–4; run turbo and XL on transformers 5.6.2; measure MLX-DiT (bf16, torch decoder freed) vs PyTorch-MPS DiT for footprint and s/step; integrated vs sequential planner (same seed → same codes?); `diffusers` import closure; frozen-app import of the data-tree copy and (if needed) the transformers shadowing. | 1.5–2 d | Go/no-go on transformers 5.6.2, DiT path and planner placement, written into this plan. |
| 1 | Runtime components (`runtime.py`, music manifest), `Download.kind="hf-dir"`, `BackendInfo.components`, music catalog and tiers, `ACESTEP_LANGUAGES`, `docs/model-metadata.md`. | 1 d | Tests green; component installs in a packaged build. |
| 2 | `music_worker.py`, `music_process.py` (guard, watchdogs, cancel), `memory_policy.free_percent`, `EXTRA_MODEL` move, `MusicWorker`, error codes. | 2 d | Fake-process tests; one real turbo job end to end. |
| 3 | Compose tab, `music_parts.py`, `StudioPage` wiring, strings EN + ZH. | 2–3 d | UI tests; screenshots in both languages reviewed. |
| 4 | Lyric timestamps in the worker, `music_lyrics.py` (sung lyrics, helper, scoring), `LyricCheckWorker`, repaint action; threshold calibration on known failures. | 2 d | User's examples flagged; approved takes mostly clear. |
| 5 | `music_rules.py`, artist list tool + asset, footer note, WAV comment, About-page credits, `third_party/README.md`. | 0.5–1 d | Rules tests. |
| 6 | Selftest, packaged build (`lock.py build`), performance report A–D, dictation-latency check, fill measured facts. | 1 d | Report written; numbers in the catalog. |

Total ≈ 10–12 working days.

## Risks

- **transformers 5.6.2.** Symbols exist, behaviour may not match (rope
  parameters, `Qwen3RotaryEmbedding` signature, `from_pretrained(dtype=)`).
  Mitigation: Phase 0 gate; vendored fix, or the shadowed 4.57.6 in the worker.
- **Memory on the MLX DiT path.** fp32 conversion plus a resident torch decoder
  could exceed 30 GB for XL. Mitigation: patch 1 or `use_mlx_dit=False`,
  whichever Phase 0 measures better.
- **Upstream churn.** Pinned commit, pruned copy, patch list; upgrades are a
  deliberate re-vendor with the same tests.
- **Duration.** A 600 s song on XL may take ~25–30 min per take (estimate by
  scaling). Length is allowed to 600 s; the time estimate is shown before
  Generate.
- **Disk.** The full XL setup is 25.3 GB, mostly an F32 checkpoint we only run
  in bf16.
- **Lyric check accuracy** over a full mix; advisory only.
- **Seed variance**: quality differs a lot between takes; mitigated by takes and
  "new take", not solved.
- **Packaging**: spawn under PyInstaller (`freeze_support` already handled for
  the translator), data-tree import path, notarisation of the downloaded wheels
  (same as the existing PyTorch runtime).

## Open questions for the user

1. **Artist names:** warn with *Remove* (planned) or block Generate until removed?
2. **Memory thresholds:** abort below 15 % free, warn below 25 %, admit at ≥ 40 % —
   acceptable, or stricter (e.g. abort at 20 %)?
3. **XL checkpoint on disk:** convert the 19.95 GB F32 checkpoint once to bf16
   after download (≈ 10 GB saved, faster load, identical to what we run), or keep
   the original files?
4. **Planner model:** the spec says "don't auto-download the 4B planner LM unless
   the machine has room", while the verified setup uses the 1.7B. The plan offers
   only the 1.7B (pre-ticked by the RAM/disk rule) and no 4B LM in v1. Is that
   what you meant, or should the 8.4 GB 4B LM be offered on ≥ 64 GB Macs?
5. **Length on XL:** keep the full 10–600 s, or cap XL at 300 s by default with
   600 s behind a confirmation?
6. **Takes:** default 2 (≈ 16 min for two 170 s songs on XL), or 3?
7. **Lower tiers:** offer turbo on 16 GB Macs although nobody has judged its
   quality by ear yet, or hide Compose below 24 GB?
8. **Storage:** keep jobs in `~/.thundertalk/music` (planned, with a "delete job"
   action), or save takes straight into `~/Music/ThunderTalk`?
9. **Lyric check:** automatic after every job (planned), or only on request?
10. **Linux:** confirm music is out of scope for v1.

## Decisions (approved by the user, 2026-10-06)

1. **Artist names:** warn and offer *Remove*; never block Generate.
2. **Memory thresholds:** start only at ≥ 40 % free, warn below 25 %, abort cleanly below 15 % (with the reason shown).
3. **XL checkpoint:** convert the F32 checkpoint to bf16 once after download and keep only the bf16 copy.
4. **Planner model:** v1 offers only the 1.7B LM.
5. **Song length on XL:** default cap 300 s; up to 600 s selectable, with the time estimate shown before Generate.
6. **Default takes:** 2.
7. **16 GB Macs:** offer the 2B turbo, labelled experimental; hide Compose below 16 GB.
8. **Takes location:** `~/Music/ThunderTalk`.
9. **Lyric check:** runs automatically after every job with vocals.
10. **Linux:** out of scope for v1 (macOS only).

## Phase 0 results (2026-10-07)

Full write-up and raw numbers: `docs/verification/2026-10-music-phase0.md`. Turbo, 30 s English song,
1.7B planner, 8 steps, seed 42, MLX VAE (tile 192), MLX buffer cache capped at 1 GB.

| Question | Result | Decision |
|---|---|---|
| transformers 5.6.2 | Two v5 behaviour changes broke the vendored DiT silently: non-persistent rotary buffers stayed zero, and the post-load `_init_weights` pass re-randomised 644/677 loaded weights (the vendored override wrote through `.data`). Both fixed as vendored patches with a regression test; after the fix every DiT tensor matches 4.57.6. | **Go on the bundled 5.6.2.** No private 4.57.6 copy, no `sys.path` shadowing. |
| Vocals (ASR read-back, word recall) | MLX DiT 0.97, PyTorch MPS DiT 0.71, 4.57.6 control 0.83; mixed-in spoken control 0.94–1.00 on every take. | Lyrics are sung intelligibly. |
| Memory | The blocker was MLX's unbounded buffer cache during VAE decode (+13–17 GB). With `mx.set_cache_limit(1 GB)`: peak 15.6 GB (MLX DiT) / 14.5 GB (MPS DiT); PyTorch VAE 13.8 GB but ~1.5× slower. | Worker caps the MLX cache first thing. |
| DiT path | MLX DiT: +1.1 GB peak, best read-back. MPS DiT: lower peak. | Default MLX DiT; MPS DiT on the 16 GB tier. |
| Precision | bf16 DiT on both paths; VAE kept fp32 (fp16 saves ~2 GB, not re-validated on the fixed weights). | bf16 DiT, fp32 VAE. |
| Planner placement | Integrated (`thinking=True`) works within the measured peaks; sequential placement not measured. | Integrated; revisit only for the 24 GB tier if Phase 6 shows pressure. |
| Not yet measured | XL (not downloaded; 115 GB disk free), `diffusers` import closure inside the frozen app, data-tree import in a packaged build. | Moved to Phase 6 (packaged build + selftest). |

## Handoff implementation notes (Codex, 2026-10-07)

- Phase 4's remaining UI can be developed with fakes while the predecessor's
  calibration job waits for machine-wide headroom. That job has now completed.
  Its recorded 0.25 errors hid the user's known corruption inside longer lines
  at 0.35, so calibration lowers PASS_ERROR to 0.20 with full-line regressions.
  Homophones still pass. No lock bypass.
- Neither measured DiT backend supplied trustworthy line timestamps. Scan-based
  line actions use the contributing recognition windows, labelled approximate;
  repaint opens a range review rather than silently treating them as exact.
  The 300 s XL default now has an explicit opt-in to 600 s with the estimate.
