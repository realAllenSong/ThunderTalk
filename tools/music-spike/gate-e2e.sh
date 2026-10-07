#!/bin/bash
# Phase 2 end-to-end: a real turbo job (2 takes, planner) through core/music_process.py under the GPU lock.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
D=artifacts/music-spike/e2e
mkdir -p "$D"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
  /usr/bin/time -l env PYTHONPATH="$PWD" "$BENCH/venv-music/bin/python" tools/music-spike/e2e.py \
  --duration 20 --takes 2 --planner --dit mlx --tag turbo-planner > "$D/turbo-planner.log" 2>&1
echo "e2e exit $?" >> "$D/turbo-planner.log"
