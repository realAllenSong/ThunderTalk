#!/bin/bash
# Phase 4: a real Chinese vocal take (sung lyrics from the helper) with sentence timestamps, under the GPU lock.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
D=artifacts/music-spike/e2e
mkdir -p "$D"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
  /usr/bin/time -l env PYTHONPATH="$PWD" "$BENCH/venv-music/bin/python" tools/music-spike/e2e.py \
  --duration 30 --takes 2 --planner --dit mlx --lang zh --tag zh-stamps > "$D/zh-stamps.log" 2>&1
echo "e2e exit $?" >> "$D/zh-stamps.log"
