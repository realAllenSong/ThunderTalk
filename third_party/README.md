# Third-party code

## ACE-Step (Phase 0 spike; not exposed in the app)

Inference-only ACE-Step 1.5 source from the locally pinned checkout at
`ca1e85fe9430179831e6bc6be790c332190a3866`, reproduced by
`tools/music-spike/vendor.py`. The upstream root licence is MIT, retained in
`acestep/LICENSE`; Qwen-derived model/configuration files also retain their
Apache-2.0 copyright and licence headers. The text encoder weights are
Qwen3-Embedding-0.6B (Apache-2.0); the ACE-Step checkpoints are MIT.

Local patches for the compatibility gate:

- Explicit handler dtype argument, so the worker chooses MPS bf16.
- Construct FSQ on CPU: its initialization reads scalar values and cannot run
  under transformers 5.6.2's meta-device construction context.
- `AceStepPreTrainedModel._init_weights` defers rotary embeddings to the base
  initializer. Under transformers 5 the non-persistent `inv_freq` buffers are
  created on the meta device and only the base initializer refills them; the
  upstream override skipped it, so every rotary frequency was zero (no position
  information: takes came out without intelligible vocals). transformers 4.57.6
  computed them in `__init__` and was unaffected.
- Direct vendored turbo/XL model classes, no remote model code or code syncing.
- Offline checkpoint precheck and path resolver; missing weights cannot trigger
  upstream downloads. The LM uses `trust_remote_code=False`.
- Diffusion-step and MLX token-block hooks; a worker-controlled timeout budget.
- MLX decoder weights materialized as bf16, with the duplicate torch decoder
  freed only after successful conversion.
- Removed UI, servers, CLI, training, LoRA and scoring; kept lyric alignment
  and its DTW helper, without the optional numba dependency.
- Upstream Python files carry `ruff: noqa`; the authored integration tools and
  tests are linted. This avoids mass formatting/refactoring of pinned code.

Compatibility and audio quality remain unverified until the Phase 0 gate runs.

## mlx_indextts

IndexTTS 2.5 inference on Apple Silicon (MLX), from
[vanch007/mlx-indextts2](https://github.com/vanch007/mlx-indextts2) at commit
`a7666367b8551656a2029ad75f259cb5e4936b3b`, MIT licence (see `mlx_indextts/LICENSE`).

Local changes:

- Applied upstream PR [#5](https://github.com/vanch007/mlx-indextts2/pull/5): S2Mel
  GroupNorm statistics accumulated in float32. Without it, anything longer than a
  sentence or two came out as a low hum on this hardware.
- Replaced the two `librosa` calls (audio loading, Slaney mel filters) with
  `_librosa_free.py` (soundfile + torchaudio), because librosa pulls in scipy and
  numba, which the app bundle does not ship.
- The Japanese tokenizer (fugashi, ~250 MB with its dictionary) is created lazily
  on first Japanese text instead of at start-up; it is not bundled.
- Removed the CLI, REST API, web UI, video library, novel planner and runtime
  wrapper modules, which ThunderTalk does not use.

The model weights (downloaded at runtime from `vanch007/mlx-indextts2-2.5-8bit`)
are IndexTTS-2.5 by bilibili under the bilibili Model Use License Agreement.
