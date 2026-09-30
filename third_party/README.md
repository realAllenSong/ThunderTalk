# Third-party code

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
