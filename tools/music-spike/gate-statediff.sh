#!/bin/bash
# Phase 0: dump tokenizer ids, text-encoder and DiT tensors under transformers 4.57.6 and 5.6.2
# (CPU only, each behind the machine-wide GPU lock) and diff them. The rotary fix alone did not
# bring the vocals back on 5.6.2, so something else differs.
set -u
cd "$(dirname "$0")/../.."
BENCH="$HOME/Library/Caches/ThunderTalk-bench"
S=docs/verification/music-gate-summary.log
D=artifacts/music-spike/statediff
mkdir -p "$D"
for v in 4.57.6 5.6.2; do
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 "$BENCH/lock.py" gpu -- \
    env PYTHONPATH="$PWD" TT_BENCH="$BENCH" "$BENCH/venv-music/bin/python" tools/music-spike/state_dump.py \
    "$v" "$D/state-$v.json" > "$D/dump-$v.log" 2>&1
  echo "statedump-$v exit $?" >> "$S"
done
python3 tools/music-spike/state_diff.py "$D/state-4.57.6.json" "$D/state-5.6.2.json" \
  > docs/verification/music-statediff.log 2>&1
echo "statediff exit $?" >> "$S"
echo "statediff chain done" >> "$S"
