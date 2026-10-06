#!/bin/bash
# Phase 0 vocal gate: transformers 5.6.2 vs 4.57.6 (MPS DiT), then the MLX DiT path, all with a 192-frame VAE tile.
cd "$(dirname "$0")/../.."
G=tools/music-spike/gate.sh
COMMON=(--vocal --planner --duration 30 --vae-chunk 192)
$G 5.6.2-vocal-vae192 -- "${COMMON[@]}" --transformers 5.6.2
$G 4.57.6-vocal-vae192 -- "${COMMON[@]}" --transformers 4.57.6
$G 5.6.2-vocal-mlx-vae192 -- "${COMMON[@]}" --transformers 5.6.2 --mlx
echo "chain done" >> docs/verification/music-gate-summary.log
