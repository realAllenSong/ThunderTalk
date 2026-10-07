#!/bin/bash
# Phase 0 vocal gate after the rotary fix (transformers 5.6.2 left every inv_freq at zero):
# 5.6.2 with the PyTorch MPS DiT and with the MLX DiT, MLX VAE + 1 GB cache cap, then ASR read-back.
# Usage: gate-fixed.sh [pid-to-wait-for]
set -u
cd "$(dirname "$0")/../.."
[ -n "${1:-}" ] && while kill -0 "$1" 2>/dev/null; do sleep 5; done
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
G=tools/music-spike/gate.sh
S=docs/verification/music-gate-summary.log
D=artifacts/music-spike
COMMON=(--vocal --planner --duration 30 --vae-chunk 192 --mx-cache-gb 1 --transformers 5.6.2 --tag fixed)
$G vocal-fixed -- "${COMMON[@]}"
$G vocal-fixed-mlxdit -- "${COMMON[@]}" --mlx
python3 "$BENCH/lock.py" gpu -- env PYTHONPATH="$PWD" TT_BENCH="$BENCH" HF_HUB_OFFLINE=1 \
  /Users/songallen/Desktop/ThunderTalk/.venv/bin/python tools/music-spike/asr_check.py \
  $D/turbo-5.6.2-mps-vocal-vae192-mxcache1-fixed/gate.wav $D/turbo-5.6.2-mlx-vocal-vae192-mxcache1-fixed/gate.wav \
  > docs/verification/music-asr-fixed.log 2>&1
echo "asr-fixed exit $?" >> "$S"
echo "fixed chain done" >> "$S"
