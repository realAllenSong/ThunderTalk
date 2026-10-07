#!/bin/bash
# Phase 0 vocal gate after the _init_weights fix (transformers 5.6.2 re-randomised the loaded DiT
# weights and zeroed the rotary/FSQ buffers): 5.6.2 state dump diffed against the 4.57.6 dump, then
# 30 s vocal takes with the PyTorch MPS DiT and the MLX DiT (MLX VAE, 1 GB cache cap), then ASR.
# Usage: gate-weights.sh [pid-to-wait-for]
set -u
cd "$(dirname "$0")/../.."
[ -n "${1:-}" ] && while kill -0 "$1" 2>/dev/null; do sleep 5; done
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
G=tools/music-spike/gate.sh
S=docs/verification/music-gate-summary.log
D=artifacts/music-spike
SD=$D/statediff
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
  env PYTHONPATH="$PWD" TT_BENCH="$BENCH" "$BENCH/venv-music/bin/python" tools/music-spike/state_dump.py \
  5.6.2 "$SD/state-5.6.2-weights.json" > "$SD/dump-5.6.2-weights.log" 2>&1
echo "statedump-5.6.2-weights exit $?" >> "$S"
python3 tools/music-spike/state_diff.py "$SD/state-4.57.6.json" "$SD/state-5.6.2-weights.json" \
  > docs/verification/music-statediff-weights.log 2>&1
echo "statediff-weights exit $?" >> "$S"
COMMON=(--vocal --planner --duration 30 --vae-chunk 192 --mx-cache-gb 1 --transformers 5.6.2 --tag weights)
$G vocal-weights -- "${COMMON[@]}"
$G vocal-weights-mlxdit -- "${COMMON[@]}" --mlx
python3 "$BENCH/lock.py" gpu -- env PYTHONPATH="$PWD" TT_BENCH="$BENCH" HF_HUB_OFFLINE=1 \
  /Users/songallen/Desktop/ThunderTalk/.venv/bin/python tools/music-spike/asr_check.py \
  $D/turbo-5.6.2-mps-vocal-vae192-mxcache1-weights/gate.wav $D/turbo-5.6.2-mlx-vocal-vae192-mxcache1-weights/gate.wav \
  > docs/verification/music-asr-weights.log 2>&1
echo "asr-weights exit $?" >> "$S"
echo "weights chain done" >> "$S"
