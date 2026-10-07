#!/bin/bash
# Phase 0 VAE memory diagnosis: the 30 s vocal take aborts in the MLX VAE decode (+13–17 GB).
# Variants: per-tile trace with a 1 GB MLX cache cap, then also without mx.compile, then fp16.
cd "$(dirname "$0")/../.."
G=tools/music-spike/gate.sh
COMMON=(--vocal --planner --duration 30 --vae-chunk 192 --transformers 5.6.2 --mx-cache-gb 1)
$G vae-trace-cache1 -- "${COMMON[@]}"
$G vae-trace-cache1-nocompile -- "${COMMON[@]}" --vae-nocompile
$G vae-trace-cache1-fp16 -- "${COMMON[@]}" --vae-fp16
echo "vae chain done" >> docs/verification/music-gate-summary.log
