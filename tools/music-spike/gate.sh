#!/bin/bash
# Phase 0 gate runs, one after another, each behind the machine-wide GPU lock.
# Usage: tools/music-spike/gate.sh <name> -- <run.py args>   (repeatable via several calls)
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
name="$1"; shift; [ "${1:-}" = "--" ] && shift
mkdir -p artifacts/music-spike/test-home
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
  env PYTHONPATH="$PWD" HOME="$PWD/artifacts/music-spike/test-home" \
  "$BENCH/venv-music/bin/python" tools/music-spike/run.py "$@" \
  > "docs/verification/music-gate-$name.log" 2>&1
echo "$name exit $?" >> docs/verification/music-gate-summary.log
