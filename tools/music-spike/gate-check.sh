#!/bin/bash
# Phase 4: lyric check calibration on the real takes with the app's Qwen3-ASR (app venv), under the GPU lock.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
PY=/Users/songallen/Desktop/ThunderTalk/.venv/bin/python
D=artifacts/music-spike/e2e
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
  /usr/bin/time -l env PYTHONPATH="$PWD" "$PY" tools/music-spike/check_takes.py \
  "$D/zh-stamps" "$D/zh-stamps-mps" "$D/turbo-planner" > "$D/check-takes.log" 2>&1
echo "check exit $?" >> "$D/check-takes.log"
